import json

from openpilot.lightpilot import route_zones
from openpilot.lightpilot.nav_exit import DEFAULTS, NavExit, advisory, load_config

KPH = 1 / 3.6


def ramp(start, length=300, modifier='slight right'):
  return {'kind': 'exit', 'start': start, 'end': start + length, 'modifier': modifier, 'ramp': True}


class Drive:
  """A car that follows the cap exactly, on a road with an exit `start` m ahead."""

  def __init__(self, tmp_path, start, v=130 * KPH, cruise=130 * KPH, config=None):
    self.zones, self.start, self.v, self.cruise, self.t = str(tmp_path / 'zones.json'), start, v, cruise, 100.0
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'nav_exit': config or {}}))
    self.exit = NavExit(cfg, self.zones)
    self.min_before_blinker = self.v
    self.max_decel = 0.0

  def step(self, blinker=False, left=False, dt=0.05):
    route_zones.write_zones([ramp(self.start)], self.zones, now=self.t)
    v_t, a_t = self.exit.update(self.v, self.cruise, 0.0, left, blinker, now=self.t)
    new_v = min(v_t, self.v + 1.0 * dt) if v_t >= self.v else max(v_t, self.v - 3.0 * dt)
    self.max_decel = max(self.max_decel, (self.v - new_v) / dt)
    self.v = new_v
    self.start -= self.v * dt
    self.t += dt
    return v_t, a_t

  def run_to(self, start, blinker=False):
    while self.start > start:
      self.step(blinker)


def test_config_never_allows_less_than_80_on_the_lanes(tmp_path):
  path = tmp_path / 'c.json'
  path.write_text(json.dumps({'nav_exit': {'approach_kph': 50, 'ramp_kph': 10, 'approach_m': 5}}))
  cfg = load_config(path)
  assert cfg['approach_kph'] == 80 and cfg['ramp_kph'] == 40 and cfg['approach_m'] == 200
  assert load_config(tmp_path / 'missing.json') == DEFAULTS


def test_far_from_the_exit_nothing_happens(tmp_path):
  d = Drive(tmp_path, 1400)
  d.run_to(700)
  assert abs(d.v - 130 * KPH) < 0.01 and d.exit.phase is None


def test_without_the_blinker_the_car_stays_at_80_and_is_released_after_the_exit(tmp_path):
  d = Drive(tmp_path, 1400)
  d.run_to(1.0)
  assert abs(d.v - 80 * KPH) < 0.3  # at the exit: 80, not less
  assert d.max_decel <= 1.05  # and gently
  d.run_to(-20.0)
  assert d.v >= 80 * KPH - 0.01 and d.exit.phase == 'approach'
  d.run_to(-60.0)
  v_t, _ = d.step()
  assert v_t == d.cruise and d.exit.phase is None  # stayed on the motorway: no cap any more


def test_blinker_near_the_exit_brings_the_ramp_speed(tmp_path):
  d = Drive(tmp_path, 1400)
  d.run_to(250.0)
  assert d.v >= 80 * KPH - 0.01
  d.run_to(-100.0, blinker=True)
  assert d.exit.armed and d.exit.phase == 'ramp'
  assert abs(d.v - 60 * KPH) < 0.3
  assert d.max_decel <= 1.25
  d.run_to(-250.0)  # held along the ramp, also after the blinker went off
  assert abs(d.v - 60 * KPH) < 0.3


def test_blinker_far_before_the_exit_or_on_the_other_side_does_not_count(tmp_path):
  d = Drive(tmp_path, 1400)
  d.run_to(500.0, blinker=True)  # e.g. returning to the right lane after overtaking
  assert not d.exit.armed
  while d.start > 100:
    d.step(left=True)
  assert not d.exit.armed and d.v >= 80 * KPH - 0.01


def test_slow_traffic_is_not_touched_and_forks_have_no_speed(tmp_path):
  d = Drive(tmp_path, 500, v=70 * KPH, cruise=70 * KPH)
  d.run_to(10.0)
  assert d.v == 70 * KPH
  zones = str(tmp_path / 'z.json')
  route_zones.write_zones([{'kind': 'exit', 'start': 100.0, 'end': 115.0, 'modifier': 'slight left'}], zones, now=5.0)
  assert NavExit(tmp_path / 'none.json', zones).update(36.0, 36.0, 0.0, False, True, now=5.1) == (36.0, 0.0)


def test_disabled_and_no_route(tmp_path):
  d = Drive(tmp_path, 300, config={'enabled': False})
  assert d.step(blinker=True)[0] == d.cruise
  assert NavExit(tmp_path / 'none.json', str(tmp_path / 'nozones.json')).update(30.0, 30.0, 0.1, False, True) == (30.0, 0.1)


def test_advisory():
  cfg = dict(DEFAULTS)
  assert advisory([ramp(1200)], 30.0, 0.0, cfg) == {'side': 'right', 'distance': 1200}
  assert advisory([ramp(1200, modifier='slight left')], 30.0, 1.0, cfg) == {'side': 'left', 'distance': 1170}
  assert advisory([{'kind': 'exit', 'start': 400.0, 'end': 415.0, 'modifier': 'slight right'}], 0.0, 0.0, cfg)['side'] == 'right'  # a fork too
  assert advisory([{'kind': 'signal', 'start': 100.0, 'end': 110.0}, ramp(900)], 0.0, 0.0, cfg) is not None
  assert advisory([{'kind': 'roundabout', 'start': 100.0, 'end': 140.0}, ramp(900)], 0.0, 0.0, cfg) is None  # another maneuver comes first
  assert advisory([ramp(-5)], 30.0, 0.0, cfg) is None  # already on the ramp
  assert advisory([ramp(1200)], 30.0, 0.0, {**cfg, 'advise_m': 1000.0}) is None
  assert advisory([ramp(500)], 30.0, 0.0, {**cfg, 'enabled': False}) is None


def test_off_ramp_zone_covers_the_ramp():
  step = lambda d, m: {'distance': d, 'maneuver': m, 'intersections': []}  # noqa: E731
  route = [step(1000, {'type': 'depart'}), step(650, {'type': 'off ramp', 'modifier': 'slight right'}), step(200, {'type': 'fork', 'modifier': 'left'})]
  zones = route_zones.extract_zones(route, [[], [], []])
  assert zones[0] == {'kind': 'exit', 'start': 1000, 'end': 1400.0, 'modifier': 'slight right', 'ramp': True}
  assert zones[1] == {'kind': 'exit', 'start': 1650, 'end': 1665.0, 'modifier': 'left'}
