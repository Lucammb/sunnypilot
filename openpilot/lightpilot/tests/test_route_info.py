import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from openpilot.lightpilot import route_info, route_zones
from openpilot.lightpilot.drive_mode import DEFAULTS as MODE_DEFAULTS, zone_ahead
from openpilot.lightpilot.speed_cap import DEFAULTS, compute_cap, zone_target
from openpilot.selfdrive.navigation.navd import RouteEngine

METER = 1 / 111319.49079327358  # degrees of latitude, and of longitude on the equator
KPH = 1 / 3.6


def path(points_m):
  """(east, north) meters -> (longitude, latitude) on the equator."""
  return [[x * METER, y * METER] for x, y in points_m]


def line(a, b, spacing=10.0):
  n = max(1, round(math.dist(a, b) / spacing))
  return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def arc(center, radius, start_deg, end_deg, spacing=6.0):
  n = max(2, round(abs(math.radians(end_deg - start_deg)) * radius / spacing))
  angles = [math.radians(start_deg + (end_deg - start_deg) * i / n) for i in range(n + 1)]
  return [(center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)) for a in angles]


def step(points_m, mtype='continue', **extra):
  coords = path(points_m)
  length = sum(math.dist(a, b) for a, b in zip(points_m, points_m[1:], strict=False))
  maneuver = {'type': mtype, **{k: extra.pop(k) for k in ('modifier', 'exit', 'bearing_before', 'bearing_after') if k in extra}}
  return {'geometry': {'coordinates': coords}, 'distance': length, 'duration': length / 10, 'bannerInstructions': [], 'maneuver': maneuver, **extra}


def geometry(steps):
  return [np.array(s['geometry']['coordinates'], dtype=np.float64) for s in steps]


def mapbox_route(steps, maxspeed=None, congestion=None):
  coords = [c for s in steps for c in s['geometry']['coordinates']]
  annotation = {}
  if maxspeed is not None:
    annotation['maxspeed'] = maxspeed(coords)
  if congestion is not None:
    annotation['congestion'] = congestion(coords)
  return {'geometry': {'coordinates': coords}, 'legs': [{'steps': steps, 'annotation': annotation}]}


class TestShape(unittest.TestCase):
  def test_ring_radius(self):
    ring = np.array(path(arc((0, 0), 18.0, 200, 340)))
    self.assertAlmostEqual(route_info.ring_radius(ring), 18.0, delta=0.5)
    self.assertIsNone(route_info.ring_radius(np.array(path(line((0, 0), (40, 0))))))  # drawn straight: no radius
    self.assertIsNone(route_info.ring_radius(np.array(path([(0, 0), (10, 0)]))))

  def test_roundabout_speed_follows_its_radius(self):
    def target(radius):
      return zone_target({'kind': 'roundabout', 'start': 0, 'end': 40, 'radius': radius}, DEFAULTS) / KPH

    self.assertAlmostEqual(target(8.0), 20.0)  # a small one: not below 20
    self.assertAlmostEqual(target(20.0), math.sqrt(2.3 * 20) * 3.6, places=3)  # about 24
    self.assertAlmostEqual(target(80.0), 40.0)  # a large one: not above 40
    self.assertAlmostEqual(zone_target({'kind': 'roundabout', 'start': 0, 'end': 40}, DEFAULTS) / KPH, 30.0)  # shape unknown
    self.assertAlmostEqual(zone_target({'kind': 'roundabout', 'start': 0, 'end': 40, 'radius': 8.0}, {**DEFAULTS, 'shape': False}) / KPH, 30.0)
    self.assertEqual(zone_target({'kind': 'roundabout', 'start': 0, 'end': 40, 'radius': 8.0}, {**DEFAULTS, 'roundabout_kph': 0.0}), 0.0)

  def test_turn_speed_follows_its_angle(self):
    def target(angle, modifier='right'):
      return zone_target({'kind': 'turn', 'start': 0, 'end': 15, 'modifier': modifier, 'angle': angle}, DEFAULTS) / KPH

    self.assertAlmostEqual(target(90.0), 26.0)
    self.assertAlmostEqual(target(150.0), 20.0)
    self.assertAlmostEqual(target(20.0, 'slight right'), 45.0)
    self.assertEqual(route_info.turn_angle({'bearing_before': 350, 'bearing_after': 80}), 90.0)
    self.assertEqual(route_info.turn_angle({'bearing_before': 10, 'bearing_after': 280}), 90.0)
    self.assertIsNone(route_info.turn_angle({'bearing_before': 10}))

  def test_tight_bend_is_a_curve_zone_a_straight_road_is_not(self):
    # 300 m east, a 90 degree left bend of 40 m radius, 300 m north
    points = line((0, 0), (300, 0)) + arc((300, 40), 40.0, -90, 0)[1:] + line((340, 40), (340, 340))[1:]
    steps = [step(points, 'depart')]
    zones = route_info.curve_zones(geometry(steps), [0.0], [steps[0]['distance']])
    self.assertEqual(len(zones), 1)
    self.assertAlmostEqual(zones[0]['start'], 300, delta=25)
    self.assertAlmostEqual(zones[0]['end'], 300 + 40 * math.pi / 2, delta=25)
    self.assertAlmostEqual(zones[0]['radius'], 40, delta=8)
    self.assertAlmostEqual(zone_target(zones[0], DEFAULTS) / KPH, math.sqrt(2.3 * 40) * 3.6, delta=4)  # about 35 km/h
    self.assertEqual(zone_target(zones[0], {**DEFAULTS, 'shape': False}), 0.0)

    # a straight road drawn with a wobble, a lane shift, and a wide motorway-like bend: nothing
    wobble = [(x, 1.5 * math.sin(x / 15.0)) for x in range(0, 800, 10)]
    shift = line((0, 0), (200, 0)) + line((230, 6), (500, 6))
    wide = line((0, 0), (200, 0)) + arc((200, 400), 400.0, -90, -30)[1:]
    for points in (wobble, shift, wide):
      s = [step(points, 'depart')]
      self.assertEqual(route_info.curve_zones(geometry(s), [0.0], [s[0]['distance']]), [], points[:2])

  def test_a_curve_at_a_turn_or_roundabout_is_that_maneuver(self):
    first = step(line((0, 0), (300, 0)), 'depart')
    second = step(line((300, 0), (300, 300)), 'turn', modifier='left', bearing_before=90, bearing_after=0)
    zones = route_zones.extract_zones([first, second], geometry([first, second]))
    self.assertEqual([z['kind'] for z in zones], ['turn'])
    self.assertEqual(zones[0]['angle'], 90.0)


class TestPoints(unittest.TestCase):
  def test_toll_yield_rail_and_merge(self):
    first = step(line((0, 0), (1000, 0)), 'depart', intersections=[
      {'location': [200 * METER, 0], 'yield_sign': True},
      {'location': [500 * METER, 0], 'railway_crossing': True},
      {'location': [800 * METER, 0], 'toll_collection': {'type': 'toll_booth'}},
      {'location': [900 * METER, 0], 'toll_collection': {'type': 'toll_gantry'}},  # driven through at speed
    ])
    second = step(line((1000, 0), (2000, 0)), 'merge', modifier='slight left')
    zones = route_zones.extract_zones([first, second], geometry([first, second]))
    self.assertEqual([(z['kind'], round(z['start'])) for z in zones], [('yield', 195), ('rail', 495), ('toll', 795), ('merge', 1000)])
    for kind in ('yield', 'rail', 'toll'):
      self.assertAlmostEqual(compute_cap([{'kind': kind, 'start': -1.0, 'end': 9.0}], DEFAULTS, 10.0, 0.0)[0], 30 * KPH)
    self.assertIsNone(compute_cap([{'kind': 'merge', 'start': -1.0, 'end': 100.0}], DEFAULTS, 10.0, 0.0))
    for kind in ('yield', 'rail', 'toll', 'merge', 'jam'):  # experimental through all of them
      self.assertEqual(zone_ahead([{'kind': kind, 'start': 50.0, 'end': 60.0}], 25.0, 0.0, MODE_DEFAULTS), kind)

  def test_an_intersection_is_placed_by_its_coordinates(self):
    # Mapbox counts geometry_index from the start of the whole route: in a later step it is not an index of that step
    first = step(line((0, 0), (500, 0)), 'depart')
    second = step(line((500, 0), (1500, 0)), 'continue', intersections=[{'location': [800 * METER, 0], 'geometry_index': 80, 'traffic_signal': True}])
    zones = route_zones.extract_zones([first, second], geometry([first, second]))
    self.assertAlmostEqual(zones[0]['start'], 795, delta=1)


class TestAnnotations(unittest.TestCase):
  def setUp(self):
    self.steps = [step(line((0, 0), (1000, 0)), 'depart'), step(line((1000, 0), (3000, 0)), 'continue')]

  def test_speed_limits(self):
    def maxspeed(coords):
      out = []
      for a, _ in zip(coords, coords[1:], strict=False):
        x = a[0] / METER
        kph = 50 if x < 595 else None if x < 895 else 90
        out.append({'none': True} if x >= 2495 else {'unknown': True} if kph is None else {'speed': kph, 'unit': 'km/h'})
      return out

    limits = route_info.speed_limits(mapbox_route(self.steps, maxspeed=maxspeed))
    self.assertEqual([(round(a), round(b), k) for a, b, k in limits], [(0, 600, 50.0), (900, 2500, 90.0)])
    self.assertEqual(route_info.limit_at(limits, 300), 50.0)
    self.assertIsNone(route_info.limit_at(limits, 700))
    self.assertEqual(route_info.limit_at(limits, 1200), 90.0)
    self.assertIsNone(route_info.limit_at(limits, 2800))
    self.assertEqual(route_info.limit_kph({'speed': 30, 'unit': 'mph'}), 48.0)
    self.assertEqual(route_info.speed_limits({'legs': [{'steps': self.steps}]}), [])  # a route without the annotation
    self.assertEqual(route_info.speed_limits(mapbox_route(self.steps, maxspeed=lambda c: [{'speed': 50, 'unit': 'km/h'}])), [])  # wrong length

  def test_queues(self):
    def congestion(coords):
      def level(x):
        return 'heavy' if 400 <= x < 480 else 'severe' if 1200 <= x < 1500 or 1560 <= x < 1900 else 'moderate' if x < 2000 else 'low'
      return [level(a[0] / METER) for a, _ in zip(coords, coords[1:], strict=False)]

    zones = route_info.jam_zones(mapbox_route(self.steps, congestion=congestion))
    self.assertEqual([(z['kind'], round(z['start']), round(z['end'])) for z in zones], [('jam', 1200, 1900)])  # 80 m is a junction; the two join

  def test_tunnels(self):
    steps = [step(line((0, 0), (1000, 0)), 'depart', intersections=[
      {'location': [0, 0], 'classes': ['motorway']}, {'location': [300 * METER, 0], 'classes': ['tunnel', 'motorway']},
      {'location': [700 * METER, 0], 'classes': ['tunnel']}]),
      step(line((1000, 0), (2000, 0)), 'continue', intersections=[{'location': [1000 * METER, 0], 'classes': ['tunnel']},
                                                                  {'location': [1400 * METER, 0], 'classes': []}])]
    found = route_info.tunnels(steps, geometry(steps), route_zones.step_starts(steps))
    self.assertEqual([(round(a), round(b)) for a, b in found], [(300, 1400)])


class TestNavd(unittest.TestCase):
  def engine(self, d, steps, route):
    response = MagicMock(status_code=200)
    response.json.return_value = {'routes': [route]}
    params = MagicMock()
    params.get.side_effect = {'NavDestination': {'latitude': 0, 'longitude': 3000 * METER}, 'LanguageSetting': 'main_it'}.get
    engine = RouteEngine(MagicMock(), MagicMock(), params=params)
    engine.last_position, engine.last_bearing, engine.lp_gps_ok = (0, 0), 90, True
    with patch('requests.get', return_value=response) as get:
      engine.calculate_route((3000 * METER, 0))
      self.assertEqual(get.call_args.kwargs['params']['annotations'], 'maxspeed,congestion')
    return engine

  def test_limit_and_tunnel(self):
    steps = [step(line((0, 0), (3000, 0)), 'depart', intersections=[
      {'location': [0, 0], 'classes': []}, {'location': [1000 * METER, 0], 'classes': ['tunnel']}, {'location': [2000 * METER, 0], 'classes': []},
      {'location': [2300 * METER, 0], 'stop_sign': True}])]
    route = mapbox_route(steps, maxspeed=lambda coords: [{'speed': 70 if a[0] / METER < 1500 else 50, 'unit': 'km/h'} for a in coords[1:]])
    with tempfile.TemporaryDirectory() as d, patch('openpilot.selfdrive.navigation.navd.Api'), \
         patch('openpilot.selfdrive.navigation.navd.load_mapbox_token', return_value='pk.x'), \
         patch.object(route_zones, 'ZONES_PATH', str(Path(d) / 'zones.json')):
      engine = self.engine(d, steps, route)
      engine.last_position = (900 * METER, 0)
      engine.send_instruction()
      self.assertEqual(route_zones.read_limit(), 70.0)
      message = engine.pm.send.call_args.args[1]
      self.assertAlmostEqual(message.navInstruction.speedLimit, 70 / 3.6, places=3)  # the phone shows it too

      # the GPS is lost at the tunnel mouth: the position goes on by the car's speed, 20 m/s for 40 s
      engine.lp_gps_ok = False
      engine.sm.__getitem__.return_value.vEgo = 20.0
      start = time.monotonic()
      for second in range(1, 41):
        with patch('time.monotonic', return_value=start + second):
          engine.send_instruction()
      with patch('time.monotonic', return_value=start + 40):
        zones, age = route_zones.read_zones()
        self.assertAlmostEqual(age, 0.0, delta=0.1)
        self.assertEqual(zones[0]['kind'], 'stop')
        self.assertAlmostEqual(zones[0]['start'], 2295 - 1700, delta=25)  # 900 + 800 m: the stop sign is 600 m ahead
        self.assertEqual(route_zones.read_limit(), 50.0)
      # past the tunnel and still no GPS: it stops guessing, the data ages and expires
      for second in range(41, 100):
        with patch('time.monotonic', return_value=start + second):
          engine.send_instruction()
      with patch('time.monotonic', return_value=start + 99):
        self.assertIsNone(route_zones.read_zones(max_age=route_zones.HOLD_AGE_S))

  def test_route_still_comes_if_the_extras_are_refused(self):
    steps = [step(line((0, 0), (3000, 0)), 'depart')]
    refused, fine = MagicMock(status_code=422), MagicMock(status_code=200)
    fine.json.return_value = {'routes': [{'legs': [{'steps': steps}]}]}
    params = MagicMock()
    params.get.side_effect = {'LanguageSetting': 'main_it'}.get
    with patch('openpilot.selfdrive.navigation.navd.Api'), patch('openpilot.selfdrive.navigation.navd.load_mapbox_token', return_value='pk.x'), \
         patch('requests.get', side_effect=[refused, fine]) as get:
      engine = RouteEngine(MagicMock(), MagicMock(), params=params)
      engine.last_position, engine.last_bearing = (0, 0), 90
      engine.calculate_route((3000 * METER, 0))
      self.assertEqual(len(engine.route), 1)
      self.assertNotIn('annotations', get.call_args.kwargs['params'])
      self.assertEqual(engine.lp_limits, [])

  def test_no_guessing_without_gps_outside_a_tunnel(self):
    steps = [step(line((0, 0), (3000, 0)), 'depart', intersections=[{'location': [2300 * METER, 0], 'stop_sign': True}])]
    with tempfile.TemporaryDirectory() as d, patch('openpilot.selfdrive.navigation.navd.Api'), \
         patch('openpilot.selfdrive.navigation.navd.load_mapbox_token', return_value='pk.x'), \
         patch.object(route_zones, 'ZONES_PATH', str(Path(d) / 'zones.json')):
      engine = self.engine(d, steps, mapbox_route(steps))
      engine.last_position = (900 * METER, 0)
      engine.send_instruction()
      self.assertIsNone(route_zones.read_limit())
      engine.lp_gps_ok = False
      engine.sm.__getitem__.return_value.vEgo = 20.0
      start = time.monotonic()
      with patch('time.monotonic', return_value=start + 10):
        engine.send_instruction()
        zones, age = route_zones.read_zones(max_age=route_zones.HOLD_AGE_S)
      self.assertAlmostEqual(age, 10.0, delta=0.5)
      self.assertAlmostEqual(zones[0]['start'], 2295 - 900, delta=2)


if __name__ == '__main__':
  unittest.main()
