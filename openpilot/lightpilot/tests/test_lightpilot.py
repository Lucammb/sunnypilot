import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from openpilot.lightpilot import set_destination
from openpilot.lightpilot.config import load_mapbox_token
from openpilot.selfdrive.navigation.navd import RouteEngine


def mapbox_response(steps):
  response = MagicMock(status_code=200)
  response.json.return_value = {'routes': [{'legs': [{'steps': steps}]}]}
  return response


def make_engine(values, token=None):
  params = MagicMock()
  params.get.side_effect = values.get
  with patch('openpilot.selfdrive.navigation.navd.Api') as api, patch('openpilot.selfdrive.navigation.navd.load_mapbox_token', return_value=token):
    api.return_value.get_token.return_value = 'comma-token'
    engine = RouteEngine(MagicMock(), MagicMock(), params=params)
  engine.last_position, engine.last_bearing = (0, 0), 90
  return engine


STEPS = [{'geometry': {'coordinates': [[0, 0], [0.001, 0]]}, 'distance': 111.0, 'duration': 10, 'bannerInstructions': []},
         {'geometry': {'coordinates': [[0.001, 0], [0.002, 0]]}, 'distance': 111.0, 'duration': 10, 'bannerInstructions': []}]


class TestToken(unittest.TestCase):
  def test_env_wins_then_file_then_none(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / 'mapbox_token'
      with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('MAPBOX_TOKEN', None)
        self.assertIsNone(load_mapbox_token(path))  # no file
        path.write_text('# comment\n\n  pk.file-token  \nignored\n')
        self.assertEqual(load_mapbox_token(path), 'pk.file-token')
        os.environ['MAPBOX_TOKEN'] = ' pk.env-token '
        self.assertEqual(load_mapbox_token(path), 'pk.env-token')
        os.environ['MAPBOX_TOKEN'] = ''
        path.write_text('# only a comment\n')
        self.assertIsNone(load_mapbox_token(path))


class TestNavdResilience(unittest.TestCase):
  def test_token_selects_mapbox_host(self):
    self.assertEqual(make_engine({}, token='pk.x').mapbox_host, 'https://api.mapbox.com')
    self.assertIn('/v1/nav', make_engine({}).mapbox_host)  # no token: the comma API as before

  def test_network_drop_keeps_route_and_retries_new_destination(self):
    values = {'NavDestination': {'latitude': 0, 'longitude': 0.002}, 'LanguageSetting': 'main_it'}
    engine = make_engine(values, token='pk.x')
    with patch('requests.get', return_value=mapbox_response(STEPS)):
      engine.recompute_route(gps_ok=True)
    self.assertEqual(engine.nav_destination, (0.002, 0))
    old_route = engine.route

    values['NavDestination'] = {'latitude': 0, 'longitude': 0.004}  # a new destination, but the network is down
    engine.recompute_hold = 0
    with patch('requests.get', side_effect=requests.ConnectionError('down')):
      engine.recompute_route(gps_ok=True)
    self.assertIs(engine.route, old_route)
    self.assertEqual(engine.nav_destination, (0.002, 0))  # still the old one, so the new one is retried

    engine.recompute_hold = 0  # network is back
    with patch('requests.get', return_value=mapbox_response(STEPS[:1])) as get:
      engine.recompute_route(gps_ok=True)
    self.assertEqual(get.call_count, 1)
    self.assertEqual(engine.nav_destination, (0.004, 0))
    self.assertEqual(len(engine.route), 1)

  def test_italian_language_is_requested(self):
    values = {'NavDestination': {'latitude': 0, 'longitude': 0.002}, 'LanguageSetting': 'main_it'}
    engine = make_engine(values, token='pk.x')
    with patch('requests.get', return_value=mapbox_response(STEPS)) as get:
      engine.recompute_route(gps_ok=True)
    self.assertEqual(get.call_args.kwargs['params']['language'], 'it')
    self.assertEqual(get.call_args.kwargs['params']['access_token'], 'pk.x')


class TestSetDestination(unittest.TestCase):
  def test_parse_coordinates(self):
    self.assertEqual(set_destination.parse_coordinates('45.4064,9.1859'), (45.4064, 9.1859))
    self.assertEqual(set_destination.parse_coordinates(' 45.4 ; -9.18 '), (45.4, -9.18))
    self.assertIsNone(set_destination.parse_coordinates('Via Roma 1, Milano'))
    with self.assertRaises(ValueError):
      set_destination.parse_coordinates('95,9')
    with self.assertRaises(ValueError):
      set_destination.parse_coordinates('45,200')

  def test_geocode(self):
    resp = MagicMock()
    resp.json.return_value = {'features': [{'center': [9.19, 45.46], 'place_name': 'Duomo, Milano'}]}
    with patch('requests.get', return_value=resp) as get:
      self.assertEqual(set_destination.geocode('Duomo Milano', 'pk.x', proximity=(45.4, 9.1)), (45.46, 9.19, 'Duomo, Milano'))
    self.assertEqual(get.call_args.kwargs['params']['proximity'], '9.1,45.4')  # Mapbox wants lon,lat
    resp.json.return_value = {'features': []}
    with patch('requests.get', return_value=resp), self.assertRaises(LookupError):
      set_destination.geocode('zzzz', 'pk.x')

  def run_main(self, argv, token=None):
    params = MagicMock()
    with patch('openpilot.common.params.Params', return_value=params), patch.object(set_destination, 'load_mapbox_token', return_value=token):
      return set_destination.main(argv), params

  def test_set_coordinates(self):
    code, params = self.run_main(['45.4064,9.1859'])
    self.assertEqual(code, 0)
    params.put.assert_called_once_with('NavDestination', {'latitude': 45.4064, 'longitude': 9.1859})
    params.put_bool.assert_called_once_with('NavigationEnabled', True)

  def test_dry_run_and_clear_and_errors(self):
    code, params = self.run_main(['45.4,9.1', '--dry-run'])
    self.assertEqual(code, 0)
    params.put.assert_not_called()
    code, params = self.run_main(['--clear'])
    self.assertEqual(code, 0)
    params.remove.assert_called_once_with('NavDestination')
    code, params = self.run_main(['Via Roma 1, Milano'], token=None)  # address search needs the token
    self.assertEqual(code, 2)
    params.put.assert_not_called()
    code, params = self.run_main(['95,9'])
    self.assertEqual(code, 1)
    with self.assertRaises(SystemExit):  # neither a destination nor --clear
      self.run_main([])


if __name__ == '__main__':
  unittest.main()


class TestMapSource(unittest.TestCase):
  def test_comma_tiles_off_by_default_and_switchable(self):
    import json
    import tempfile
    from pathlib import Path
    from openpilot.lightpilot.config import use_comma_tiles
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / 'config.json'
      self.assertFalse(use_comma_tiles(path))  # no file
      path.write_text(json.dumps({'map': {'comma_tiles': True}}))
      self.assertTrue(use_comma_tiles(path))
      path.write_text('not json')
      self.assertFalse(use_comma_tiles(path))


class TestHudLayout:
  def test_speed_is_always_centered(self):
    from openpilot.lightpilot.hud_layout import speed_center_x
    assert speed_center_x(0, 2160, 300) == 1080
    assert speed_center_x(0, 1080, 300) == 540

  def test_sign_beside_the_set_speed_when_full_width(self):
    from openpilot.lightpilot.hud_layout import speed_limit_sign_origin
    assert speed_limit_sign_origin(0, 0, 2160, 200, 204) == (284, 39)

  def test_sign_small_and_top_right_in_the_half_view(self):
    from openpilot.lightpilot.hud_layout import speed_limit_sign_origin, speed_limit_sign_scale
    scale = speed_limit_sign_scale(1080)
    assert scale < 1.0 and speed_limit_sign_scale(2160) == 1.0
    x, y = speed_limit_sign_origin(0, 0, 1080, 200, 204)
    assert y < 100  # at the top
    assert x + 200 * scale > 1080 - 200  # on the right side
    assert x + 200 * scale + 9 * scale <= 1080  # and inside the view
