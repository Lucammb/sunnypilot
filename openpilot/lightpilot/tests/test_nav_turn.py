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

  def test_roundabout_can_be_turned_off(self):
    assert turn_side([roundabout(10, 1)], CFG, 5, 0) is None

  def test_first_exit_is_right_from_before_the_entry_until_out(self):
    for start in (20, 10, 0, -15, -38, -44):  # 40 m of ring: approaching, entering, inside, leaving, 4 m past the exit
      assert turn_side([roundabout(start, 1)], RA_CFG, 5, 0) == RIGHT, start
    assert turn_side([roundabout(40, 1)], RA_CFG, 5, 0) is None  # still far
    assert turn_side([roundabout(-50, 1)], RA_CFG, 5, 0) is None  # 10 m past the exit: done

  def test_later_exit_is_right_only_past_the_junction_before_it(self):
    ring = {**roundabout(10, 2, length=60), 'exit_run': 22.0}  # the junction before the route's exit is 22 m before it
    assert turn_side([ring], RA_CFG, 5, 0) is None  # at the entry: the model's own
    assert turn_side([{**ring, 'start': -20, 'end': 40}], RA_CFG, 5, 0) is None  # in the ring, the other exit still ahead
    assert turn_side([{**ring, 'start': -37, 'end': 23}], RA_CFG, 5, 0) is None  # at that exit: not this one
    assert turn_side([{**ring, 'start': -43, 'end': 17}], RA_CFG, 5, 0) == RIGHT  # past it: now
    assert turn_side([{**ring, 'start': -63, 'end': -3}], RA_CFG, 5, 0) == RIGHT
    assert turn_side([{**ring, 'start': -70, 'end': -10}], RA_CFG, 5, 0) is None

  def test_later_exit_never_left_and_blind_without_the_junction(self):
    for exit_number in (2, 3, 4):
      assert turn_side([roundabout(5, exit_number)], RA_CFG, 5, 0) is None
      assert turn_side([roundabout(-25, exit_number)], RA_CFG, 5, 0) is None  # 15 m before the exit
      assert turn_side([roundabout(-30, exit_number)], RA_CFG, 5, 0) == RIGHT  # the last 12 m
    far = {**roundabout(-20, 3, length=100), 'exit_run': 70.0}  # a long last stretch: not the whole of it
    assert turn_side([far], RA_CFG, 5, 0) is None
    assert turn_side([{**far, 'start': -76, 'end': 24}], RA_CFG, 5, 0) == RIGHT

  def test_mini_roundabout_without_exit_number(self):
    assert turn_side([{**roundabout(10, None), 'modifier': 'right'}], RA_CFG, 5, 0) == RIGHT
    assert turn_side([{**roundabout(10, None), 'modifier': 'left'}], RA_CFG, 5, 0) is None
    assert turn_side([roundabout(10, None)], RA_CFG, 5, 0) is None

  def test_intersection_turns_need_their_own_switch(self):
    assert turn_side([turn(20)], {**RA_CFG, 'enabled': False}, 5, 0) is None
    assert turn_side([roundabout(10, 1)], {**RA_CFG, 'enabled': False}, 5, 0) == RIGHT

  def test_the_age_of_the_data_moves_the_car_on(self):
    ring = {**roundabout(-30, 2, length=60), 'exit_run': 22.0}  # 30 m to the exit when the position was measured
    assert turn_side([ring], RA_CFG, 6.0, 0.0) is None
    assert turn_side([ring], RA_CFG, 6.0, 2.5) == RIGHT  # 2.5 s later at 6 m/s: 15 m to the exit


class TestConfig:
  def test_defaults_off(self, tmp_path):
    config = load_config(tmp_path / "missing.json")
    assert config['enabled'] is False and config['roundabout'] is True  # intersection turns off, roundabout exits on

  def test_user_values_and_clamp(self, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({'nav_turn': {'enabled': True, 'window_m': 500, 'roundabout': 'yes'}}))
    config = load_config(path)
    assert config['enabled'] is True and config['window_m'] == nav_turn.MAX_WINDOW_M and config['roundabout'] is True  # not a boolean: the default

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
    assert self.run(self.controller(tmp_path), v=50 / 3.6) == TurnDirection.none  # above the 45 km/h route-turn limit

  def test_route_turn_works_above_lane_turn_speed(self, tmp_path):
    # the driver accelerates by hand past the 30 km/h cap: the roundabout/turn must still be made
    assert self.run(self.controller(tmp_path), v=40 / 3.6) == TurnDirection.turnRight

  def test_driver_blinker_keeps_the_lane_turn_speed(self, tmp_path):
    assert self.run(self.controller(tmp_path), v=40 / 3.6, left=True) == TurnDirection.none

  def test_blindspot_blocks(self, tmp_path):
    assert self.run(self.controller(tmp_path), br=True) == TurnDirection.none

  def test_route_turn_does_not_need_the_lane_turn_desire_setting(self, tmp_path):
    c = self.controller(tmp_path, enabled=False)
    assert self.run(c) == TurnDirection.turnRight
    assert self.run(c, left=True) == TurnDirection.none  # the setting is off: no blinker turns, and a blinker the other way stops the route's
    assert self.run(c, right=True) == TurnDirection.turnRight  # signalling the exit does not cancel it

  def test_blinker_on_the_routes_side_keeps_it(self, tmp_path):
    assert self.run(self.controller(tmp_path), v=40 / 3.6, right=True) == TurnDirection.turnRight

  def test_nav_turn_off(self, tmp_path):
    assert self.run(self.controller(tmp_path, nav_enabled=False)) == TurnDirection.none

  def test_no_zones(self, tmp_path):
    assert self.run(self.controller(tmp_path, zones=[])) == TurnDirection.none


class TestTrace:
  def _turn(self, tmp_path):
    from openpilot.lightpilot.nav_turn import NavTurn
    t = NavTurn(config_path=tmp_path / 'c.json')
    t.trace_path = tmp_path / 'turn.log'
    return t

  def test_logs_only_near_a_zone(self, tmp_path):
    t = self._turn(tmp_path)
    t.zones, t.zones_age = [{'kind': 'roundabout', 'start': 300.0, 'end': 360.0, 'exit': 1}], 0.0
    t.trace(8.0, 8.5, 'none', now=0.0)
    assert not (tmp_path / 'turn.log').exists()
    t.zones = [{'kind': 'roundabout', 'start': 40.0, 'end': 100.0, 'exit': 1}]
    t.side = 'right'
    t.trace(10.0, 8.5, 'too fast', now=1.0)
    line = (tmp_path / 'turn.log').read_text()
    assert 'zone=roundabout:1' in line and 'route_says=right' in line and 'result=too fast' in line and 'v=36km/h' in line

  def test_rate_limited_but_logs_changes(self, tmp_path):
    t = self._turn(tmp_path)
    t.zones, t.zones_age, t.side = [{'kind': 'turn', 'start': 10.0, 'end': 40.0, 'modifier': 'right'}], 0.0, 'right'
    t.trace(5.0, 8.5, 'ok', now=0.0)
    t.trace(5.0, 8.5, 'ok', now=0.3)
    t.trace(5.0, 8.5, 'blind spot', now=0.4)
    assert len((tmp_path / 'turn.log').read_text().splitlines()) == 2



class TestMaxSpeed:
  def test_default_and_clamp(self, tmp_path):
    assert load_config(tmp_path / "missing.json")['max_kph'] == 45.0
    for given, expected in ((200, 60.0), (5, 20.0), (35, 35.0)):
      path = tmp_path / "c.json"
      path.write_text(json.dumps({'nav_turn': {'max_kph': given}}))
      assert load_config(path)['max_kph'] == expected
