"""Motorway and ring-road exits on the navigation route.

Two things, both from the route's "off ramp" maneuver (the point where the ramp leaves the road):

- an advisory, "next exit, keep right", for the screen and the phone (forks get the advisory too);
- the speed. The car never changes lane by itself. On the motorway lanes it only slows to the approach speed, which is
  never below 80 km/h, and only close to the exit. It slows further, to the ramp speed, only on the ramp itself (past the
  point where the ramp leaves the road) and only once the driver has shown that the car is leaving: the blinker on the
  exit side, close to the exit, for a couple of seconds (or still on when the ramp starts). Without the blinker the
  car stays at the approach speed and, once past the exit, the cap is dropped.

Like the other caps it only ever lowers the target speed, and the gas pedal overrides it.

Settings (optional) in /data/lightpilot/config.json:
  {"nav_exit": {"enabled": true, "approach_kph": 80, "approach_m": 600, "ramp_kph": 60, "advise_m": 1500}}
approach_kph cannot be set below 80.
"""
import json
import math
import time
from pathlib import Path

from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import HOLD_AGE_S, read_zones

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
CONFIG_REFRESH_S = 5.0
DEFAULTS = {'enabled': True, 'approach_kph': 80.0, 'approach_m': 600.0, 'ramp_kph': 60.0, 'advise_m': 1500.0}
MIN_APPROACH_KPH, MAX_APPROACH_KPH = 80.0, 110.0  # on the motorway lanes: never slower than 80
MIN_APPROACH_M, MAX_APPROACH_M = 200.0, 1000.0
MIN_RAMP_KPH, MAX_RAMP_KPH = 40.0, 80.0
MIN_ADVISE_M, MAX_ADVISE_M = 300.0, 1500.0
KPH = 1 / 3.6

MIN_DECEL = 0.3
MAX_APPROACH_DECEL = 1.0  # from a high cruise speed the slowdown starts earlier than approach_m rather than brake harder
RAMP_DECEL = 0.5
MAX_RAMP_DECEL = 1.2
ARM_WINDOW_M = 400.0  # the blinker counts from this far before the ramp
ARM_BLINK_S = 2.5  # blinker time that means "I am taking this exit"
RAMP_REACH_M = 120.0  # the ramp speed is reached this far into the ramp: all the braking below 80 is done on the ramp
MISSED_M = 30.0  # past the start of the ramp without the blinker: the car stayed on the motorway
NEW_ZONE_JUMP_M = 100.0

LEFT, RIGHT = 'left', 'right'
BLOCKING_KINDS = ('turn', 'roundabout')  # a maneuver before the exit: "keep right" would be about the wrong thing


def load_config(path: Path | None = None) -> dict:
  config = dict(DEFAULTS)
  try:
    user = json.loads((path or CONFIG_FILE).read_text()).get('nav_exit', {})
    if isinstance(user.get('enabled'), bool):
      config['enabled'] = user['enabled']
    for key in ('approach_kph', 'approach_m', 'ramp_kph', 'advise_m'):
      value = user.get(key)
      if isinstance(value, (int, float)) and not isinstance(value, bool):
        config[key] = float(value)
  except (OSError, ValueError, AttributeError):
    pass
  config['approach_kph'] = min(max(config['approach_kph'], MIN_APPROACH_KPH), MAX_APPROACH_KPH)
  config['approach_m'] = min(max(config['approach_m'], MIN_APPROACH_M), MAX_APPROACH_M)
  config['ramp_kph'] = min(max(config['ramp_kph'], MIN_RAMP_KPH), MAX_RAMP_KPH)
  config['advise_m'] = min(max(config['advise_m'], MIN_ADVISE_M), MAX_ADVISE_M)
  return config


def exit_side(zone: dict) -> str:
  return LEFT if 'left' in str(zone.get('modifier') or '') else RIGHT


def advisory(zones: list[dict], v_ego: float, age: float, config: dict) -> dict | None:
  """{'side', 'distance'} for the next exit or fork within advise_m, when it is the next maneuver; else None."""
  if not config['enabled']:
    return None
  moved = v_ego * age
  for zone in zones:  # sorted by start
    start = zone['start'] - moved
    if start <= 0.0:
      continue
    if zone['kind'] in BLOCKING_KINDS:
      return None
    if zone['kind'] == 'exit':
      return {'side': exit_side(zone), 'distance': start} if start <= config['advise_m'] else None
  return None


def read_advisory(v_ego: float, config: dict | None = None, zones_path: str | None = None, now: float | None = None) -> dict | None:
  """advisory() from the zones file; None on any problem (for the screen and the phone link)."""
  try:
    data = read_zones(zones_path, now)
    return None if data is None else advisory(data[0], v_ego, data[1], config or load_config())
  except Exception:
    return None


def profile(distance: float, target: float, decel: float) -> float:
  return math.sqrt(target * target + 2.0 * decel * max(distance, 0.0))


class NavExit:
  def __init__(self, config_path: Path | None = None, zones_path: str | None = None):
    self.config_path, self.zones_path = config_path, zones_path
    self.config = load_config(config_path)
    self._config_t = self._last_t = float('-inf')
    self.phase: str | None = None  # 'approach' or 'ramp' while this cap is the one lowering the speed
    self.decel = 0.0  # the deceleration it then asks for
    self._reset()

  def _reset(self) -> None:
    self.armed = False
    self.blink_s = 0.0
    self.last_start: float | None = None
    self.approach_decel: float | None = None
    self.ramp_decel: float | None = None

  def _cap(self, zones: list[dict], age: float, v_ego: float, v_target: float, left: bool, right: bool, dt: float) -> tuple[float, float, str] | None:
    """(cap m/s, deceleration, phase) or None."""
    moved = v_ego * age
    zone = next((z for z in zones if z['kind'] == 'exit' and z.get('ramp') and z['end'] - moved >= 0.0), None)
    if zone is None:
      self._reset()
      return None
    start = zone['start'] - moved
    if self.last_start is None or start > self.last_start + NEW_ZONE_JUMP_M:
      self._reset()
    self.last_start = start

    if start <= ARM_WINDOW_M and (left if exit_side(zone) == LEFT else right):
      self.blink_s += dt
    if self.blink_s >= ARM_BLINK_S or (start <= 0.0 and self.blink_s > 0.0 and (left if exit_side(zone) == LEFT else right)):
      self.armed = True
    if not self.armed and start < -MISSED_M:
      return None  # the driver did not take the exit

    approach, ramp = self.config['approach_kph'] * KPH, self.config['ramp_kph'] * KPH
    best = None
    if start <= 0.0:
      best = (approach, MIN_DECEL, 'approach')
    elif v_target > approach or self.approach_decel is not None:
      if self.approach_decel is None:
        needed = (v_target * v_target - approach * approach) / 2.0
        if start <= max(self.config['approach_m'], needed / MAX_APPROACH_DECEL):  # "close to the exit"
          self.approach_decel = min(max(needed / start, MIN_DECEL), MAX_APPROACH_DECEL)
      if self.approach_decel is not None:
        best = (profile(start, approach, self.approach_decel), self.approach_decel, 'approach')

    if self.armed and ramp < approach and start <= 0.0:  # never below the approach speed before the ramp has left the road
      to_target = max(start + RAMP_REACH_M, 0.0)
      if self.ramp_decel is None:
        needed = max(v_ego * v_ego - ramp * ramp, 0.0) / (2.0 * max(to_target, 30.0))
        self.ramp_decel = min(max(needed, RAMP_DECEL), MAX_RAMP_DECEL)
      cap = profile(to_target, ramp, self.ramp_decel)
      if best is None or cap < best[0]:
        best = (cap, self.ramp_decel, 'ramp')
    return best

  def update(self, v_ego: float, v_target: float, a_target: float, left_blinker: bool, right_blinker: bool,
             now: float | None = None) -> tuple[float, float]:
    now = time.monotonic() if now is None else now
    dt = min(max(now - self._last_t, 0.0), 0.5)
    self._last_t = now
    self.phase, self.decel = None, 0.0
    try:  # the planner must never fail because of this: on any problem, no cap
      if now - self._config_t >= CONFIG_REFRESH_S:
        self._config_t, self.config = now, load_config(self.config_path)
      if not self.config['enabled']:
        return v_target, a_target
      data = read_zones(self.zones_path, now, HOLD_AGE_S)
      if data is None:
        self._reset()
        return v_target, a_target
      cap = self._cap(data[0], data[1], v_ego, v_target, bool(left_blinker), bool(right_blinker), dt)
    except Exception:
      return v_target, a_target
    if cap is None or cap[0] >= v_target:
      return v_target, a_target
    self.phase, self.decel = cap[2], cap[1]
    return cap[0], min(a_target, -cap[1])
