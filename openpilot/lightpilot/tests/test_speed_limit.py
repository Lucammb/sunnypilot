import json
import tempfile
import unittest
from pathlib import Path

from openpilot.lightpilot.route_zones import write_zones
from openpilot.lightpilot.speed_limit import DEFAULTS, FIXED, FOLLOW, OFF, LimitHold, load_config, read_status, write_tap

KPH = 1 / 3.6


class TestLimitHold(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.dir.cleanup)
    root = Path(self.dir.name)
    self.tap, self.status, self.config, self.zones = str(root / 'tap.json'), str(root / 'limit.json'), root / 'config.json', str(root / 'zones.json')

  def hold(self) -> LimitHold:
    return LimitHold(self.tap, self.status, self.config, self.zones)

  def test_nothing_by_itself(self):
    hold = self.hold()
    for i in range(50):
      self.assertIsNone(hold.update(130.0, 50 * KPH, False, now=100.0 + i * 0.05))
    self.assertIsNone(read_status(self.status, now=102.0))

  def test_tap_makes_the_limit_the_speed_and_a_second_tap_turns_it_off(self):
    hold = self.hold()
    self.assertIsNone(hold.update(90.0, 70 * KPH, False, now=100.0))
    write_tap(self.tap, now=100.3)
    self.assertAlmostEqual(hold.update(90.0, 70 * KPH, False, now=100.4), 70 * KPH)
    self.assertEqual(read_status(self.status, now=100.5), {'mode': FIXED, 'kph': 70.0})  # shown at once
    self.assertAlmostEqual(hold.update(90.0, 50 * KPH, False, now=110.0), 70 * KPH)  # the limit changed: not followed
    write_tap(self.tap, now=120.0)
    self.assertAlmostEqual(hold.update(90.0, 50 * KPH, False, now=120.3), 50 * KPH)  # a tap takes the new limit
    write_tap(self.tap, now=130.0)
    self.assertIsNone(hold.update(90.0, 50 * KPH, False, now=130.3))  # same limit: off
    self.assertIsNone(read_status(self.status, now=130.4))

  def test_tap_without_a_limit_or_an_old_tap_does_nothing(self):
    write_tap(self.tap, now=50.0)
    hold = self.hold()  # a tap from before the start
    self.assertIsNone(hold.update(90.0, 70 * KPH, False, now=50.5))
    write_tap(self.tap, now=60.0)
    self.assertIsNone(hold.update(90.0, 0.0, False, now=60.3))  # no limit on the sign
    write_tap(self.tap, now=70.0)
    self.assertIsNone(hold.update(90.0, 70 * KPH, False, now=80.0))  # read 10 s late (the planner was not running)

  def test_changing_the_set_speed_on_the_car_turns_it_off(self):
    hold = self.hold()
    hold.update(90.0, 70 * KPH, False, now=100.0)
    write_tap(self.tap, now=100.3)
    self.assertIsNotNone(hold.update(90.0, 70 * KPH, False, now=100.4))
    self.assertIsNotNone(hold.update(255.0, 70 * KPH, False, now=101.0))  # cruise not set: says nothing
    self.assertIsNotNone(hold.update(40.0, 70 * KPH, False, enabled=False, now=101.5))  # nor does the value while it is off
    self.assertIsNotNone(hold.update(90.0, 70 * KPH, False, now=102.0))
    self.assertIsNone(hold.update(95.0, 70 * KPH, False, now=103.0))
    self.assertEqual(hold.mode, OFF)

  def test_after_a_motorway_exit_the_limit_is_followed(self):
    hold = self.hold()
    self.assertIsNone(hold.update(130.0, 60 * KPH, False, now=100.0))
    self.assertAlmostEqual(hold.update(130.0, 60 * KPH, True, now=100.1), 60 * KPH)
    self.assertEqual(hold.mode, FOLLOW)
    self.assertAlmostEqual(hold.update(130.0, 90 * KPH, False, now=110.0), 90 * KPH)
    self.assertAlmostEqual(hold.update(130.0, 0.0, False, now=111.0), 90 * KPH)  # the sign is lost: the last one
    self.assertAlmostEqual(hold.update(130.0, 50 * KPH, False, now=112.0), 50 * KPH)
    self.assertEqual(read_status(self.status, now=112.1), {'mode': FOLLOW, 'kph': 50.0})
    write_tap(self.tap, now=113.0)
    self.assertIsNone(hold.update(130.0, 50 * KPH, False, now=113.3))  # a tap ends it

  def test_exit_with_no_limit_known_and_the_switches(self):
    hold = self.hold()
    self.assertAlmostEqual(hold.update(130.0, 0.0, True, now=100.0), 90 * KPH)
    self.config.write_text(json.dumps({'speed_limit': {'after_exit': False, 'tap': False, 'unknown_kph': 20}}))
    self.assertEqual(load_config(self.config), {'tap': False, 'after_exit': False, 'unknown_kph': 50.0})
    hold = self.hold()
    self.assertIsNone(hold.update(130.0, 60 * KPH, True, now=100.0))
    write_tap(self.tap, now=101.0)
    self.assertIsNone(hold.update(130.0, 60 * KPH, False, now=101.3))
    self.assertEqual(load_config(Path(self.dir.name) / 'missing.json'), DEFAULTS)

  def test_map_limit(self):
    hold = self.hold()
    self.assertEqual(hold.map_speed_limit(now=100.0), 0.0)
    write_zones([], self.zones, now=100.0, limit_kph=70)
    self.assertAlmostEqual(hold.map_speed_limit(now=100.5), 70 * KPH)
    write_zones([], self.zones, now=101.0, route=False, limit_kph=70)
    self.assertEqual(hold.map_speed_limit(now=101.5), 0.0)
    write_zones([], self.zones, now=102.0, limit_kph=True)
    self.assertEqual(hold.map_speed_limit(now=102.5), 0.0)


if __name__ == '__main__':
  unittest.main()
