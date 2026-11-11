import json
import tempfile
import unittest
from pathlib import Path

from openpilot.lightpilot import drive_mode
from openpilot.lightpilot.drive_mode import (DEFAULTS, EXPERIMENTAL, STOCK, DriveMode, load_config, read_choice, read_status,
                                             toggle_choice, write_choice, zone_ahead)
from openpilot.lightpilot.hud_layout import (DRIVE_MODE_BADGE_PX, DRIVE_MODE_BUTTON_PX, NAV_CARD_HEIGHT, NAV_CARD_MARGIN,
                                             drive_mode_button, in_drive_mode_button)
from openpilot.lightpilot.route_zones import write_zones


class Files(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.dir.cleanup)
    root = Path(self.dir.name)
    self.choice, self.config, self.zones, self.status = root / "drive_mode", root / "config.json", str(root / "zones.json"), str(root / "status.json")

  def mode(self) -> DriveMode:
    return DriveMode(self.choice, self.config, self.zones, self.status)

  def route(self, zones, now=100.0):
    write_zones(zones, self.zones, now=now)


class TestChoice(Files):
  def test_default_is_what_the_base_software_does(self):
    self.assertEqual(read_choice(self.choice), EXPERIMENTAL)
    self.choice.write_text("nonsense")
    self.assertEqual(read_choice(self.choice), EXPERIMENTAL)

  def test_toggle_is_saved(self):
    self.assertEqual(toggle_choice(self.choice), STOCK)
    self.assertEqual(read_choice(self.choice), STOCK)
    self.assertEqual(toggle_choice(self.choice), EXPERIMENTAL)
    self.assertEqual(self.choice.read_text().strip(), EXPERIMENTAL)

  def test_toggle_that_cannot_be_saved_changes_nothing(self):
    blocked = Path(self.dir.name) / "file" / "drive_mode"
    (Path(self.dir.name) / "file").write_text("x")  # a file where the folder should be
    self.assertEqual(toggle_choice(blocked), EXPERIMENTAL)


class TestConfig(Files):
  def test_defaults_and_limits(self):
    self.assertEqual(load_config(self.config), DEFAULTS)
    self.config.write_text(json.dumps({'drive_mode': {'auto_in_navigation': False, 'ahead_s': 999, 'min_ahead_m': 1}}))
    config = load_config(self.config)
    self.assertFalse(config['auto_in_navigation'])
    self.assertEqual(config['ahead_s'], drive_mode.MAX_AHEAD_S)
    self.assertEqual(config['min_ahead_m'], drive_mode.MIN_AHEAD_M)
    self.config.write_text("{broken")
    self.assertEqual(load_config(self.config), DEFAULTS)


class TestZoneAhead(unittest.TestCase):
  def test_reach_grows_with_speed(self):
    signal = [{'kind': 'signal', 'start': 250.0, 'end': 260.0}]
    self.assertIsNone(zone_ahead(signal, 14.0, 0.0, DEFAULTS))  # 50 km/h: 168 m of reach
    self.assertEqual(zone_ahead(signal, 25.0, 0.0, DEFAULTS), 'signal')  # 90 km/h: 300 m
    self.assertEqual(zone_ahead([{'kind': 'turn', 'start': 140.0, 'end': 155.0}], 0.0, 0.0, DEFAULTS), 'turn')  # standing still

  def test_held_through_the_zone_and_released_after(self):
    def zone(start):
      return [{'kind': 'roundabout', 'start': start, 'end': start + 60.0}]

    self.assertEqual(zone_ahead(zone(-30.0), 8.0, 0.0, DEFAULTS), 'roundabout')  # inside
    self.assertEqual(zone_ahead(zone(-70.0), 8.0, 0.0, DEFAULTS), 'roundabout')  # 10 m past the end
    self.assertIsNone(zone_ahead(zone(-80.0), 8.0, 0.0, DEFAULTS))  # 20 m past
    self.assertIsNone(zone_ahead(zone(-60.0), 10.0, 2.0, DEFAULTS))  # the file is 2 s old: 20 m further on

  def test_exits_do_not_count(self):
    self.assertIsNone(zone_ahead([{'kind': 'exit', 'start': 50.0, 'end': 65.0}], 20.0, 0.0, DEFAULTS))


class TestDriveMode(Files):
  def test_without_route_the_driver_chooses(self):
    mode = self.mode()
    self.assertTrue(mode.update(20.0, False, now=100.0))  # nobody chose: as the base software
    write_choice(STOCK, self.choice)
    self.assertTrue(mode.update(20.0, False, now=100.2))  # the file is read twice a second
    self.assertFalse(mode.update(20.0, False, now=100.6))
    self.assertEqual(read_status(self.status, now=100.6), {'e2e': False, 'auto': False, 'reason': None})
    write_choice(EXPERIMENTAL, self.choice)
    self.assertTrue(mode.update(20.0, False, now=101.2))

  def test_a_tap_to_stock_is_immediate_even_while_the_model_brakes(self):
    mode = self.mode()
    self.assertTrue(mode.update(3.0, True, now=100.0))
    write_choice(STOCK, self.choice)
    self.assertFalse(mode.update(3.0, True, now=100.6))
    self.assertFalse(mode.update(3.0, True, now=100.7))

  def test_route_makes_it_automatic(self):
    write_choice(EXPERIMENTAL, self.choice)
    mode = self.mode()
    self.route([{'kind': 'signal', 'start': 400.0, 'end': 410.0}])
    self.assertFalse(mode.update(14.0, False, now=100.0))  # far: stock, whatever the driver chose
    self.assertEqual(read_status(self.status, now=100.0), {'e2e': False, 'auto': True, 'reason': None})
    self.route([{'kind': 'signal', 'start': 120.0, 'end': 130.0}], now=101.0)
    self.assertTrue(mode.update(14.0, False, now=101.0))
    self.assertEqual(read_status(self.status, now=101.0), {'e2e': True, 'auto': True, 'reason': 'signal'})
    self.route([{'kind': 'signal', 'start': -40.0, 'end': -30.0}], now=102.0)
    self.assertFalse(mode.update(14.0, False, now=102.0))  # passed

  def test_never_drives_off_a_red_light_by_itself(self):
    write_choice(STOCK, self.choice)
    mode = self.mode()
    self.route([{'kind': 'signal', 'start': 5.0, 'end': 15.0}])
    self.assertTrue(mode.update(0.0, True, now=100.0))  # stopped at the light, the model holds the car
    # the route is lost (stale file) while waiting: the driver's choice is stock, but the model still holds the car
    self.assertTrue(mode.update(0.0, True, now=110.0))
    self.assertEqual(read_status(self.status, now=110.0)['reason'], 'hold')
    self.assertFalse(mode.update(0.0, False, now=111.0))  # green: the model lets go, back to stock
    # the same past the end of a zone
    self.route([{'kind': 'stop', 'start': 2.0, 'end': 12.0}], now=120.0)
    self.assertTrue(mode.update(1.0, True, now=120.0))
    self.route([{'kind': 'stop', 'start': -40.0, 'end': -30.0}], now=121.0)
    self.assertTrue(mode.update(1.0, True, now=121.0))
    self.assertFalse(mode.update(9.0, True, now=121.5))  # moving again: released

  def test_automatic_can_be_turned_off(self):
    self.config.write_text(json.dumps({'drive_mode': {'auto_in_navigation': False}}))
    write_choice(STOCK, self.choice)
    mode = self.mode()
    self.route([{'kind': 'signal', 'start': 20.0, 'end': 30.0}])
    self.assertFalse(mode.update(14.0, False, now=100.0))
    self.assertFalse(read_status(self.status, now=100.0)['auto'])

  def test_broken_zones_file_means_no_route(self):
    Path(self.zones).write_text("{broken")
    write_choice(STOCK, self.choice)
    self.assertFalse(self.mode().update(14.0, False, now=100.0))

  def test_status_goes_stale(self):
    self.mode().update(14.0, False, now=100.0)
    self.assertIsNotNone(read_status(self.status, now=102.0))
    self.assertIsNone(read_status(self.status, now=104.0))
    self.assertIsNone(read_status(self.status + ".missing", now=100.0))


class TestButtonLayout(unittest.TestCase):
  def test_top_right_without_route(self):
    x, y, size = drive_mode_button(30.0, 30.0, 2100.0, navigation=False)
    self.assertEqual(size, DRIVE_MODE_BUTTON_PX)
    self.assertEqual(x + size, 30.0 + 2100.0 - 40)
    self.assertEqual(y, 75.0)

  def test_under_the_maneuver_card_with_route(self):
    x, y, size = drive_mode_button(30.0, 30.0, 2100.0, navigation=True)
    self.assertEqual(size, DRIVE_MODE_BADGE_PX)
    self.assertGreater(y, 30.0 + NAV_CARD_MARGIN + NAV_CARD_HEIGHT)
    self.assertEqual(x + size, 30.0 + 2100.0 - NAV_CARD_MARGIN)

  def test_touch(self):
    button = drive_mode_button(30.0, 30.0, 2100.0, navigation=False)
    x, y, size = button
    self.assertTrue(in_drive_mode_button(x + size / 2, y + size / 2, button))
    self.assertTrue(in_drive_mode_button(x - 10, y + size / 2, button))  # a little off: still the button
    self.assertFalse(in_drive_mode_button(x - 200, y + size / 2, button))
    self.assertFalse(in_drive_mode_button(1000.0, 600.0, button))  # the middle of the screen opens the sidebar


if __name__ == "__main__":
  unittest.main()
