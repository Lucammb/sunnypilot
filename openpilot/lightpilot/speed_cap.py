"""Speed cap before roundabouts, turns, tight curves, stop and yield signs, toll booths and level crossings, from the
navigation route.

The cap only ever lowers the target speed, never raises it, and the driver's gas pedal overrides it like any other
target. It follows a comfortable deceleration profile: the speed allowed at a distance d from the zone is
sqrt(v_target^2 + 2 * decel * max(d - offset, 0)), so the car starts slowing exactly early enough, and the cap is held
through the zone and released a few meters after it.

Settings live in /data/lightpilot/config.json (optional), so no params keys are involved:
  {"nav_speed_cap": {"enabled": true, "roundabout_kph": 30, "turn_kph": 30, "slight_turn_kph": 50, "stop_kph": 25, "signal_kph": 0,
                     "yield_kph": 30, "toll_kph": 30, "rail_kph": 30, "curve_kph": 60, "shape": true, "lat_accel": 2.3,
                     "decel": 0.45, "resume_accel": 1.0}}
A speed of 0 turns that kind of zone off. slight_turn_kph is for "bear left/right" turns. resume_accel (m/s^2) is how fast
the target speed comes back after any navigation cap (roundabout, turn, exit). Takes effect within a few seconds, no restart.

With "shape" on, the speed comes from the road's own shape when the map shows it: sqrt(lat_accel * radius) in a roundabout
(20 to 40 km/h) and in a tight curve (20 km/h to curve_kph), and by the angle at a turn (20 km/h for a hairpin turn,
45 for a gentle one). Without the shape, or with "shape" off, the fixed speeds above apply; curves are then not capped.
"""
import json
import math
import time
from pathlib import Path

import numpy as np

from openpilot.common.constants import CV
from openpilot.common.swaglog import cloudlog
from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import HOLD_AGE_S, read_zones

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
CONFIG_REFRESH_S = 5.0
DEFAULTS = {'enabled': True, 'roundabout_kph': 30.0, 'turn_kph': 30.0, 'slight_turn_kph': 50.0, 'stop_kph': 25.0, 'signal_kph': 0.0,
            'yield_kph': 30.0, 'toll_kph': 30.0, 'rail_kph': 30.0, 'curve_kph': 60.0, 'shape': True, 'lat_accel': 2.3, 'decel': 0.45,
            'resume_accel': 1.0}
KIND_KEYS = {'roundabout': 'roundabout_kph', 'turn': 'turn_kph', 'stop': 'stop_kph', 'signal': 'signal_kph', 'yield': 'yield_kph',
             'toll': 'toll_kph', 'rail': 'rail_kph', 'curve': 'curve_kph'}  # 'exit', 'merge' and 'jam' never cap
RING_KPH = (20.0, 40.0)  # a roundabout's speed from its radius stays within these
MIN_SHAPE_KPH = 20.0
TURN_ANGLES, TURN_KPH = (30.0, 60.0, 90.0, 120.0), (45.0, 32.0, 26.0, 20.0)  # degrees the road turns by -> km/h
MIN_LAT_ACCEL, MAX_LAT_ACCEL = 1.5, 3.5
BRAKING_OFFSET_M = 8.0  # the target speed is reached this far before the zone (stops)
REACH_EARLY_M = 40.0  # roundabouts and turns: already at the target this far before, so the turn assist can act
EARLY_KINDS = ('roundabout', 'turn', 'toll')
RELEASE_M = 5.0  # hold the cap this far past the end of the zone
MIN_DECEL, MAX_DECEL = 0.2, 1.0  # a config typo must not make the car brake hard or not at all
MIN_RESUME, MAX_RESUME = 0.5, 2.0  # m/s^2; 2.0 is what the base software does


def zone_key(zone: dict) -> str | None:
  """The config key with the speed for this zone."""
  if zone['kind'] == 'turn' and 'slight' in str(zone.get('modifier') or ''):
    return 'slight_turn_kph'
  return KIND_KEYS.get(zone['kind'])


def zone_target(zone: dict, config: dict) -> float:
  """Speed (m/s) to drive this zone at; 0: this kind of zone does not cap."""
  key = zone_key(zone)
  fixed = float(config.get(key, 0.0)) if key else 0.0
  if fixed <= 0.0:
    return 0.0
  kph, kind = fixed, zone['kind']
  if config.get('shape', True):
    radius, angle = zone.get('radius'), zone.get('angle')
    by_radius = math.sqrt(config['lat_accel'] * radius) * CV.MS_TO_KPH if isinstance(radius, (int, float)) and radius > 0 else None
    if kind == 'roundabout' and by_radius is not None:
      kph = min(max(by_radius, RING_KPH[0]), RING_KPH[1])
    elif kind == 'curve':
      kph = min(max(by_radius, MIN_SHAPE_KPH), fixed) if by_radius is not None else 0.0
    elif kind == 'turn' and isinstance(angle, (int, float)):
      kph = float(np.interp(angle, TURN_ANGLES, TURN_KPH))
  elif kind == 'curve':
    kph = 0.0
  return kph * CV.KPH_TO_MS


def load_config(path: Path | None = None) -> dict:
  config = dict(DEFAULTS)
  try:
    user = json.loads((path or CONFIG_FILE).read_text()).get('nav_speed_cap', {})
    for key, default in DEFAULTS.items():
      value = user.get(key)
      if isinstance(default, bool):
        if isinstance(value, bool):
          config[key] = value
      elif isinstance(value, (int, float)) and not isinstance(value, bool):
        config[key] = float(value)
  except (OSError, ValueError, AttributeError):
    pass
  config['decel'] = min(max(float(config['decel']), MIN_DECEL), MAX_DECEL)
  config['resume_accel'] = min(max(float(config['resume_accel']), MIN_RESUME), MAX_RESUME)
  config['lat_accel'] = min(max(float(config['lat_accel']), MIN_LAT_ACCEL), MAX_LAT_ACCEL)
  return config


def allowed_speed(distance: float, target: float, decel: float, offset: float = BRAKING_OFFSET_M) -> float:
  """Highest speed (m/s) that still lets the car reach `target` `offset` m before the zone with `decel` (m/s^2)."""
  return math.sqrt(target * target + 2.0 * decel * max(distance - offset, 0.0))


def compute_cap(zones: list[dict], config: dict, v_ego: float, age: float) -> tuple[float, str] | None:
  """(speed cap in m/s, kind of the zone that sets it), or None when no zone limits the speed."""
  best = None
  for zone in zones:
    target = zone_target(zone, config)
    if target <= 0.0:
      continue
    start, end = zone['start'] - v_ego * age, zone['end'] - v_ego * age  # the file is up to a few seconds old
    if end < -RELEASE_M:
      continue
    offset = REACH_EARLY_M if zone['kind'] in EARLY_KINDS else BRAKING_OFFSET_M
    cap = allowed_speed(start, target, config['decel'], offset) if start > 0.0 else target
    if best is None or cap < best[0]:
      best = (cap, zone['kind'])
  return best


def apply_cap(v_target: float, a_target: float, cap: tuple[float, str] | None, decel: float) -> tuple[float, float]:
  """Lower (v_target, a_target) to the cap, never raise them."""
  if cap is None or cap[0] >= v_target:
    return v_target, a_target
  return cap[0], min(a_target, -decel)


class Resume:
  """After a navigation cap ends, the target speed comes back at a comfortable rate, not at full acceleration."""

  def __init__(self):
    self.v: float | None = None
    self.t = 0.0

  def update(self, v_ego: float, v_target: float, v_capped: float, accel: float, now: float | None = None) -> float:
    """v_target: without the caps; v_capped: with them. Returns the target speed to drive to."""
    now = time.monotonic() if now is None else now
    if v_capped < v_target:  # a cap is at work: remember where the resume starts from
      self.v, self.t = v_capped, now
      return v_capped
    if self.v is None:
      return v_target
    dt, self.t = min(max(now - self.t, 0.0), 0.5), now
    self.v = max(self.v + accel * dt, v_ego)  # never below the car's own speed: the driver's gas pedal is not fought
    if self.v >= v_target:
      self.v = None
      return v_target
    return self.v


class NavSpeedCap:
  def __init__(self, config_path: Path | None = None, zones_path: str | None = None):
    self.config_path = config_path
    self.zones_path = zones_path
    self.config = load_config(config_path)
    self.config_time = time.monotonic()
    self.kind: str | None = None
    self.decel = 0.0  # the deceleration this cap asks for while it is the one lowering the speed, else 0

  def update(self, v_ego: float, v_target: float, a_target: float) -> tuple[float, float]:
    """Apply the cap to (v_target, a_target)."""
    if time.monotonic() - self.config_time > CONFIG_REFRESH_S:
      self.config, self.config_time = load_config(self.config_path), time.monotonic()
    self.kind, self.decel = None, 0.0
    if not self.config['enabled']:
      return v_target, a_target
    try:  # the planner must never fail because of the cap: on any problem, no cap
      data = read_zones(self.zones_path, max_age=HOLD_AGE_S)
      if data is None:
        return v_target, a_target
      cap = compute_cap(data[0], self.config, v_ego, data[1])
      v_new, a_new = apply_cap(v_target, a_target, cap, self.config['decel'])
    except Exception:
      cloudlog.exception("lightpilot: speed cap failed")
      return v_target, a_target
    if cap is not None and v_new < v_target:
      self.kind, self.decel = cap[1], self.config['decel']
    return v_new, a_new
