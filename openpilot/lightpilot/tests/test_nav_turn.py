import json

from openpilot.cereal import custom
from openpilot.lightpilot import nav_turn
from openpilot.lightpilot.nav_turn import NavTurn, turn_side, load_config, LEFT, RIGHT
from openpilot.lightpilot.route_zones import write_zones
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib import lane_turn_desire
from openpilot.sunnypilot.selfdrive.controls.lib.lane_turn_desire import LaneTurnController, LANE_CHANGE_SPEED_MIN

TurnDirection = custom.ModelDataV2SP.TurnDirection
CFG = {'enabled': True, 'window_m': 25.0, 'roundabout': False}
RA_CFG = {**CFG, 'roundabout': True}


def turn(start, modifier='right'):
  return {'kind': 'turn', 'start': start, 'end': start + 15, 'modifier': modifier}


def roundabout(start, exit_number, length=40):
  return {'kind': 'roundabout', 'start': start, 'end': start + length, 'exit': exit_number}


class TestTurnSide:
  def test_within_window(self):
    assert turn_side([turn(20)], CFG, 5, 0) == RIGHT
    assert turn_side([turn(20, 'slight left')], CFG, 5, 0) == LEFT

  def test_far_away_or_passed(self):
    assert turn_side([turn(60)], CFG, 5, 0) is None
    assert turn_side([turn(-30)], CFG, 5, 0) is None

  def test_inside_crossing(self):
    assert turn_side([turn(-8)], CFG, 5, 0) == RIGHT

  def test_straight_and_uturn_ignored(self):
    assert turn_side([turn(10, 'straight')], CFG, 5, 0) is None
    assert turn_side([turn(10, 'uturn')], CFG, 5, 0) is None

  def test_age_compensation(self):
    # 30 m away when written, 1 s old at 6 m/s: now 24 m
    assert turn_side([turn(30)], CFG, 6, 1.0) == RIGHT
    assert turn_side([turn(30)], CFG, 6, 0.0) is None

  def test_nearest_zone_wins_and_exit_does_not_hide(self):
    exit_zone = {'kind': 'exit', 'start': 5, 'end': 20, 'modifier': 'right'}
    assert turn_side([exit_zone, turn(18, 'left')], CFG, 5, 0) == LEFT
    assert turn_side([{'kind': 'stop', 'start': 2, 'end': 12}], CFG, 5, 0) is None

  def test_roundabout_off_by_default(self):
    assert turn_side([roundabout(10, 1)], CFG, 5, 0) is None

  def test_roundabout_exits(self):
    assert turn_side([roundabout(10, 1)], RA_CFG, 5, 0) == RIGHT
    assert turn_side([roundabout(10, 2)], RA_CFG, 5, 0) is None
    assert turn_side([roundabout(10, 3)], RA_CFG, 5, 0) == LEFT
    assert turn_side([roundabout(10, None)], RA_CFG, 5, 0) is None

  def test_roundabout_only_at_entry(self):
    assert turn_side([roundabout(-25, 1)], RA_CFG, 5, 0) is None


class TestConfig:
  def test_defaults_off(self, tmp_path):
    assert load_config(tmp_path / "missing.json")['enabled'] is False

  def test_user_values_and_clamp(self, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({'nav_turn': {'enabled': True, 'window_m': 500, 'roundabout': 'yes'}}))
    config = load_config(path)
    assert config['enabled'] is True and config['window_m'] == nav_turn.MAX_WINDOW_M and config['roundabout'] is False

  def test_garbage(self, tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{nope")
    assert load_config(path) == nav_turn.DEFAULTS


class TestNavTurn:
  def make(self, tmp_path, enabled=True, zones=None, now=100.0):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({'nav_turn': {'enabled': enabled}}))
    zpath = str(tmp_path / "z.json")
    write_zones(zones if zones is not None else [turn(15)], zpath, now=now)
    return NavTurn(zpath, cfg)

  def test_reads_file(self, tmp_path):
    assert self.make(tmp_path).update(5, now=100.1) == RIGHT

  def test_disabled(self, tmp_path):
    assert self.make(tmp_path, enabled=False).update(5, now=100.1) is None

  def test_stale_file(self, tmp_path):
    assert self.make(tmp_path).update(5, now=110.0) is None


class FakeParams:
  def get(self, key, return_default=False):
    return 19.0

  def get_bool(self, key):
    return False


class TestController:
  def controller(self, tmp_path, enabled=True, nav_enabled=True, zones=None, monkeypatch=None):
    lane_turn_desire.Params = FakeParams
    controller = LaneTurnController(None)
    controller.enabled = enabled
    controller.lane_turn_value = LANE_CHANGE_SPEED_MIN
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({'nav_turn': {'enabled': nav_enabled}}))
    zpath = str(tmp_path / "z.json")
    write_zones(zones if zones is not None else [turn(15)], zpath)
    controller.nav_turn = NavTurn(zpath, cfg)
    return controller

  def run(self, c, v=5.0, left=False, right=False, bl=False, br=False):
    c.update_lane_turn(bl, br, left, right, v)
    return c.get_turn_direction()

  def test_nav_turns_without_blinker(self, tmp_path):
    assert self.run(self.controller(tmp_path)) == TurnDirection.turnRight

  def test_blinker_wins(self, tmp_path):
    assert self.run(self.controller(tmp_path), left=True) == TurnDirection.turnLeft

  def test_too_fast(self, tmp_path):
    assert self.run(self.controller(tmp_path), v=LANE_CHANGE_SPEED_MIN + 1) == TurnDirection.none

  def test_blindspot_blocks(self, tmp_path):
    assert self.run(self.controller(tmp_path), br=True) == TurnDirection.none

  def test_lane_turn_desire_off(self, tmp_path):
    assert self.run(self.controller(tmp_path, enabled=False)) == TurnDirection.none

  def test_nav_turn_off(self, tmp_path):
    assert self.run(self.controller(tmp_path, nav_enabled=False)) == TurnDirection.none

  def test_no_zones(self, tmp_path):
    assert self.run(self.controller(tmp_path, zones=[])) == TurnDirection.none
