import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from openpilot.common.constants import CV
from openpilot.lightpilot import route_zones
from openpilot.lightpilot.speed_cap import (BRAKING_OFFSET_M, DEFAULTS, NavSpeedCap, Resume, allowed_speed, apply_cap, compute_cap,
                                            load_config)
from openpilot.selfdrive.navigation.navd import RouteEngine

KPH = CV.KPH_TO_MS
METER = 1 / 111319.49079327358  # degrees of latitude


def straight_step(length_m: float, start_m: float = 0.0, **extra):
  """A step along the equator from start_m to start_m + length_m."""
  coords = [[start_m * METER, 0], [(start_m + length_m / 2) * METER, 0], [(start_m + length_m) * METER, 0]]
  return {'geometry': {'coordinates': coords}, 'distance': length_m, 'duration': length_m / 10, 'bannerInstructions': [], **extra}


def make_route():
  # 0-400 m depart; 400-480 roundabout (exit 2); 480-1000 straight with a stop sign 100 m in; 1000-1200 turn right; 1200-1300 arrive
  steps = [
    straight_step(400, 0, maneuver={'type': 'depart'}),
    straight_step(80, 400, maneuver={'type': 'roundabout', 'exit': 2}),
    straight_step(520, 480, maneuver={'type': 'exit roundabout'},
                  intersections=[{'stop_sign': True, 'geometry_index': 1}, {'traffic_signal': True, 'geometry_index': 2}]),
    straight_step(200, 1000, maneuver={'type': 'turn', 'modifier': 'right'}),
    straight_step(100, 1200, maneuver={'type': 'off ramp', 'modifier': 'right'}),
  ]
  return steps, [np.array(s['geometry']['coordinates'], dtype=np.float64) for s in steps]


class TestZones(unittest.TestCase):
  def test_extract(self):
    route, geometry = make_route()
    zones = route_zones.extract_zones(route, geometry)
    kinds = [z['kind'] for z in zones]
    self.assertEqual(kinds, ['roundabout', 'stop', 'signal', 'turn', 'exit'])
    roundabout, stop, signal, turn, exit_ = zones
    self.assertEqual((roundabout['start'], roundabout['end'], roundabout['exit']), (400, 480, 2))
    self.assertAlmostEqual(stop['start'] + route_zones.POINT_ZONE_M / 2, 480 + 260, delta=1)  # index 1 of 3 points: halfway
    self.assertAlmostEqual(signal['start'] + route_zones.POINT_ZONE_M / 2, 480 + 520, delta=1)  # index 2: the end of the step
    self.assertEqual((turn['start'], turn['modifier']), (1000, 'right'))
    self.assertEqual(exit_['start'], 1200)

  def test_straight_continue_is_not_a_zone(self):
    route = [straight_step(100, 0, maneuver={'type': 'turn', 'modifier': 'straight'}), straight_step(100, 100, maneuver={'type': 'continue'})]
    self.assertEqual(route_zones.extract_zones(route, [np.array(s['geometry']['coordinates']) for s in route]), [])

  def test_long_roundabout_step_is_capped(self):
    # a route without roundabout_exits: the roundabout step runs on to the next maneuver (585 m)
    route = [straight_step(20, 0, maneuver={'type': 'depart'}), straight_step(585, 20, maneuver={'type': 'roundabout', 'exit': 1})]
    zone = route_zones.extract_zones(route, [s['geometry']['coordinates'] for s in route])[0]
    self.assertEqual(zone['end'] - zone['start'], route_zones.MAX_ROUNDABOUT_M)

  def test_zones_ahead(self):
    route, geometry = make_route()
    zones = route_zones.extract_zones(route, geometry)
    ahead = route_zones.zones_ahead(zones, progress=300)
    self.assertEqual([z['kind'] for z in ahead], ['roundabout', 'stop', 'signal', 'turn', 'exit'])  # all within the 1500 m lookahead
    self.assertEqual(ahead[0]['start'], 100)
    ahead = route_zones.zones_ahead(zones, progress=470)  # inside the roundabout
    self.assertEqual((ahead[0]['kind'], ahead[0]['start'], ahead[0]['end']), ('roundabout', -70, 10))
    ahead = route_zones.zones_ahead(zones, progress=520)  # 40 m past the roundabout's end: gone
    self.assertNotIn('roundabout', [z['kind'] for z in ahead])
    far = route_zones.zones_ahead(zones, progress=-1200)  # 1200 m before the route start: only what is within 1500 m
    self.assertEqual([z['kind'] for z in far], ['roundabout', 'stop'][:len(far)])

  def test_write_read_and_staleness(self):
    with tempfile.TemporaryDirectory() as d:
      path = str(Path(d) / 'zones.json')
      self.assertIsNone(route_zones.read_zones(path))  # missing
      route_zones.write_zones([{'kind': 'turn', 'start': 50, 'end': 65}], path, now=100.0)
      zones, age = route_zones.read_zones(path, now=101.5)
      self.assertEqual((zones[0]['kind'], age), ('turn', 1.5))
      self.assertIsNone(route_zones.read_zones(path, now=100.0 + route_zones.MAX_AGE_S + 0.1))  # stale
      self.assertIsNone(route_zones.read_zones(path, now=99.0))  # from the future
      Path(path).write_text('{not json')
      self.assertIsNone(route_zones.read_zones(path))

  def test_older_files_are_read_when_asked(self):
    with tempfile.TemporaryDirectory() as d:
      path = str(Path(d) / 'zones.json')
      route_zones.write_zones([{'kind': 'turn', 'start': 50, 'end': 65}], path, now=100.0)
      self.assertIsNone(route_zones.read_zones(path, now=108.0))  # too old for the screen and the phone
      self.assertEqual(route_zones.read_zones(path, now=108.0, max_age=route_zones.HOLD_AGE_S)[1], 8.0)  # the planner still uses it
      self.assertIsNone(route_zones.read_zones(path, now=100.0 + route_zones.HOLD_AGE_S + 0.1, max_age=route_zones.HOLD_AGE_S))
      route_zones.write_zones([], path, now=107.0, route=False)  # no route is no route, however fresh or old the limit
      self.assertIsNone(route_zones.read_zones(path, now=107.5, max_age=route_zones.HOLD_AGE_S))


class TestCapKeepsWorkingWhenNavdIsLate(unittest.TestCase):
  def test_cap_still_applies_with_a_file_8_s_old(self):
    import time
    with tempfile.TemporaryDirectory() as d:
      path = str(Path(d) / 'zones.json')
      # written 8 s ago, roundabout 200 m ahead of the car then; the car has covered 8 * 13.9 = 111 m since
      route_zones.write_zones([{'kind': 'roundabout', 'start': 200.0, 'end': 280.0}], path, now=time.monotonic() - 8.0)
      cap = NavSpeedCap(config_path=Path(d) / 'none.json', zones_path=path)
      v_target, _ = cap.update(50 * KPH, 90 * KPH, 0.0)
      self.assertLess(v_target, 90 * KPH)
      self.assertEqual(cap.kind, 'roundabout')


class TestCap(unittest.TestCase):
  def test_allowed_speed_profile(self):
    target = 30 * KPH
    self.assertAlmostEqual(allowed_speed(0, target, 0.45), target)
    self.assertAlmostEqual(allowed_speed(BRAKING_OFFSET_M, target, 0.45), target)
    d = 108.0
    self.assertAlmostEqual(allowed_speed(d, target, 0.45), math.sqrt(target ** 2 + 2 * 0.45 * (d - BRAKING_OFFSET_M)))
    self.assertGreater(allowed_speed(150, target, 0.45), allowed_speed(100, target, 0.45))  # farther: faster is fine

  def test_compute_cap_phases(self):
    config = dict(DEFAULTS)
    zone = {'kind': 'roundabout', 'start': 200.0, 'end': 280.0}
    v_ego = 50 * KPH
    far = compute_cap([zone], config, v_ego, 0.0)
    self.assertGreater(far[0], v_ego)  # 200 m away at 50 km/h: no need to slow yet
    near = compute_cap([{**zone, 'start': 40.0, 'end': 120.0}], config, v_ego, 0.0)
    self.assertLess(near[0], v_ego)  # 40 m away: braking is due
    inside = compute_cap([{**zone, 'start': -30.0, 'end': 50.0}], config, v_ego, 0.0)
    self.assertAlmostEqual(inside[0], 30 * KPH)  # held through the zone
    self.assertIsNotNone(compute_cap([{**zone, 'start': -90.0, 'end': -3.0}], config, v_ego, 0.0))  # held 5 m past the end
    self.assertIsNone(compute_cap([{**zone, 'start': -90.0, 'end': -8.0}], config, v_ego, 0.0))  # then released

  def test_file_age_is_compensated(self):
    config = dict(DEFAULTS)
    zone = {'kind': 'turn', 'start': 80.0, 'end': 95.0}
    v_ego = 10.0
    fresh = compute_cap([zone], config, v_ego, 0.0)[0]
    aged = compute_cap([zone], config, v_ego, 1.0)[0]  # the car moved 10 m since: the zone is closer, the cap tighter
    self.assertLess(aged, fresh)

  def test_roundabout_and_turn_reach_target_early_stop_does_not(self):
    config = dict(DEFAULTS)
    for kind, kph in (('roundabout', 30.0), ('turn', 30.0)):
      cap = compute_cap([{'kind': kind, 'start': 40.0, 'end': 60.0}], config, 13.0, 0.0)[0]
      self.assertAlmostEqual(cap, kph / 3.6, places=3)  # already at the target 40 m before the zone
    stop = compute_cap([{'kind': 'stop', 'start': 40.0, 'end': 45.0}], config, 13.0, 0.0)[0]
    self.assertGreater(stop, 25.0 / 3.6)  # stops keep the late 8 m offset

  def test_kinds_and_off_switches(self):
    config = {**DEFAULTS, 'signal_kph': 0.0}
    self.assertIsNone(compute_cap([{'kind': 'signal', 'start': 5.0, 'end': 15.0}], config, 10.0, 0.0))  # off by default
    self.assertIsNone(compute_cap([{'kind': 'exit', 'start': 5.0, 'end': 15.0}], config, 10.0, 0.0))  # exits never cap
    config['signal_kph'] = 20.0
    self.assertAlmostEqual(compute_cap([{'kind': 'signal', 'start': 5.0, 'end': 15.0}], config, 10.0, 0.0)[0], 20 * KPH)
    nearest = compute_cap([{'kind': 'stop', 'start': 5.0, 'end': 15.0}, {'kind': 'roundabout', 'start': 5.0, 'end': 15.0}], config, 10.0, 0.0)
    self.assertEqual(nearest[1], 'stop')  # the lowest cap wins: 25 vs 30 km/h

  def test_apply_cap_only_lowers(self):
    self.assertEqual(apply_cap(10.0, 0.2, None, 0.45), (10.0, 0.2))
    self.assertEqual(apply_cap(10.0, 0.2, (12.0, 'turn'), 0.45), (10.0, 0.2))  # a cap above the target changes nothing
    self.assertEqual(apply_cap(10.0, 0.2, (8.0, 'turn'), 0.45), (8.0, -0.45))
    self.assertEqual(apply_cap(10.0, -0.9, (8.0, 'turn'), 0.45), (8.0, -0.9))  # an already harder braking is kept

  def test_config(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / 'config.json'
      self.assertEqual(load_config(path), DEFAULTS)  # missing
      path.write_text(json.dumps({'nav_speed_cap': {'roundabout_kph': 35, 'enabled': False, 'decel': 5.0, 'turn_kph': 'fast', 'stop_kph': True}}))
      config = load_config(path)
      self.assertEqual((config['roundabout_kph'], config['enabled'], config['decel']), (35.0, False, 1.0))  # decel clamped
      self.assertEqual((config['turn_kph'], config['stop_kph']), (DEFAULTS['turn_kph'], DEFAULTS['stop_kph']))  # invalid values ignored
      path.write_text('{broken')
      self.assertEqual(load_config(path), DEFAULTS)
      path.write_text('{"nav_speed_cap": [1]}')
      self.assertEqual(load_config(path), DEFAULTS)

  def test_navspeedcap_end_to_end(self):
    with tempfile.TemporaryDirectory() as d:
      zones_path, config_path = str(Path(d) / 'zones.json'), Path(d) / 'config.json'
      cap = NavSpeedCap(config_path, zones_path)
      v_ego = 40 * KPH
      self.assertEqual(cap.update(v_ego, 14.0, 0.1), (14.0, 0.1))  # no file: no cap
      route_zones.write_zones([{'kind': 'roundabout', 'start': 30.0, 'end': 110.0}], zones_path)
      v, a = cap.update(v_ego, 14.0, 0.1)
      self.assertLess(v, v_ego)
      self.assertLessEqual(a, -0.45)
      self.assertEqual(cap.kind, 'roundabout')
      self.assertEqual(cap.update(v_ego, 3.0, 0.0), (3.0, 0.0))  # a target already below the cap is left alone
      self.assertIsNone(cap.kind)
      route_zones.write_zones([{'kind': 'roundabout'}], zones_path)  # malformed zone: no crash, no cap
      self.assertEqual(cap.update(v_ego, 14.0, 0.1), (14.0, 0.1))
      config_path.write_text(json.dumps({'nav_speed_cap': {'enabled': False}}))
      cap.config_time -= 10  # force a reload
      route_zones.write_zones([{'kind': 'roundabout', 'start': 30.0, 'end': 110.0}], zones_path)
      self.assertEqual(cap.update(v_ego, 14.0, 0.1), (14.0, 0.1))  # disabled in the config


class TestTurnsAndResume(unittest.TestCase):
  def test_continue_left_is_a_turn_continue_slight_is_not(self):
    def zones(maneuver):
      steps = [straight_step(300, 0, maneuver={'type': 'depart'}), straight_step(200, 300, maneuver=maneuver)]
      return route_zones.extract_zones(steps, [np.array(s['geometry']['coordinates'], dtype=np.float64) for s in steps])

    self.assertEqual([z['kind'] for z in zones({'type': 'continue', 'modifier': 'left'})], ['turn'])
    self.assertEqual(zones({'type': 'continue', 'modifier': 'slight left'}), [])
    self.assertEqual(zones({'type': 'continue', 'modifier': 'straight'}), [])

  def test_slight_turn_has_its_own_speed(self):
    def cap(modifier, config=DEFAULTS):
      return compute_cap([{'kind': 'turn', 'start': -1.0, 'end': 14.0, 'modifier': modifier}], config, 10.0, 0.0)

    self.assertAlmostEqual(cap('right')[0], 30 * KPH)
    self.assertAlmostEqual(cap('slight right')[0], 50 * KPH)
    self.assertIsNone(cap('slight left', {**DEFAULTS, 'slight_turn_kph': 0.0}))

  def test_speed_comes_back_gently_after_a_cap(self):
    resume = Resume()
    self.assertEqual(resume.update(14.0, 14.0, 14.0, 1.0, now=10.0), 14.0)  # never capped: nothing to do
    self.assertEqual(resume.update(8.3, 14.0, 8.3, 1.0, now=20.0), 8.3)  # capped
    self.assertAlmostEqual(resume.update(8.3, 14.0, 14.0, 1.0, now=20.05), 8.35)  # the cap is gone: +1 m/s every second
    self.assertAlmostEqual(resume.update(8.4, 14.0, 14.0, 1.0, now=20.55), 8.85)
    self.assertAlmostEqual(resume.update(12.0, 14.0, 14.0, 1.0, now=20.6), 12.0)  # the driver accelerates: follow the car
    self.assertEqual(resume.update(12.0, 11.0, 11.0, 1.0, now=20.65), 11.0)  # the set speed was lowered below it: done
    self.assertEqual(resume.update(11.0, 14.0, 14.0, 1.0, now=20.7), 14.0)

  def test_config_limits_resume_accel(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / 'config.json'
      path.write_text(json.dumps({'nav_speed_cap': {'resume_accel': 9, 'slight_turn_kph': 40}}))
      config = load_config(path)
      self.assertEqual((config['resume_accel'], config['slight_turn_kph']), (2.0, 40.0))


class TestNavdWritesZones(unittest.TestCase):
  def test_zones_follow_the_car(self):
    route, geometry = make_route()
    response = MagicMock(status_code=200)
    response.json.return_value = {'routes': [{'legs': [{'steps': route}]}]}
    params = MagicMock()
    params.get.side_effect = {'NavDestination': {'latitude': 0, 'longitude': 1300 * METER}, 'LanguageSetting': 'main_it'}.get
    with tempfile.TemporaryDirectory() as d, patch('openpilot.selfdrive.navigation.navd.Api'), \
         patch('openpilot.selfdrive.navigation.navd.load_mapbox_token', return_value='pk.x'), \
         patch.object(route_zones, 'ZONES_PATH', str(Path(d) / 'zones.json')):
      engine = RouteEngine(MagicMock(), MagicMock(), params=params)
      engine.last_position, engine.last_bearing = (0, 0), 90
      with patch('requests.get', return_value=response):
        engine.calculate_route((1300 * METER, 0))
      self.assertEqual(len(engine.lp_zones), 5)
      engine.last_position = (350 * METER, 0)  # 350 m along the first step: the roundabout starts 50 m ahead
      engine.send_instruction()
      zones, _ = route_zones.read_zones()
      self.assertEqual(zones[0]['kind'], 'roundabout')
      self.assertAlmostEqual(zones[0]['start'], 50, delta=1.5)
      # the file carries the time of the GPS fix: a fix that is 4 s old makes the readers move the zones on by speed * 4
      engine.lp_fix_time = time.monotonic() - 4.0
      engine.send_instruction()
      self.assertIsNone(route_zones.read_zones())  # too old for the strict readers
      _, age = route_zones.read_zones(max_age=route_zones.HOLD_AGE_S)
      self.assertAlmostEqual(age, 4.0, delta=0.5)
      engine.lp_fix_time = None
      # away from the route (a wrong turn) its zones are another road's: no data, at once when far, after two fixes when near
      engine.last_position = (350 * METER, 60 * METER)
      engine.send_instruction()
      self.assertIsNone(route_zones.read_zones())
      engine.last_position = (350 * METER, 0)
      engine.send_instruction()
      self.assertIsNotNone(route_zones.read_zones())
      engine.last_position = (350 * METER, 30 * METER)
      engine.send_instruction()
      self.assertIsNotNone(route_zones.read_zones())  # one fix 30 m away can be GPS noise
      engine.send_instruction()
      self.assertIsNone(route_zones.read_zones())
      engine.clear_route()
      engine.send_instruction()
      self.assertIsNone(route_zones.read_zones())  # no route: no data at all


if __name__ == '__main__':
  unittest.main()
