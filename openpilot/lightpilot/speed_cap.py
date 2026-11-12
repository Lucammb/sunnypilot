"""Speed cap before roundabouts, turns and stop signs, from the navigation route.

The cap only ever lowers the target speed, never raises it, and the driver's gas pedal overrides it like any other
target. It follows a comfortable deceleration profile: the speed allowed at a distance d from the zone is
sqrt(v_target^2 + 2 * decel * max(d - offset, 0)), so the car starts slowing exactly early enough, and the cap is held
through the zone and released a few meters after it.

Settings live in /data/lightpilot/config.json (optional), so no params keys are involved:
  {"nav_speed_cap": {"enabled": true, "roundabout_kph": 30, "turn_kph": 30, "stop_kph": 25, "signal_kph": 0, "decel": 0.45}}
A speed of 0 turns that kind of zone off. Takes effect within a few seconds, no restart.
"""
import json
import math
import time
from pathlib import Path

from openpilot.common.constants import CV
from openpilot.common.swaglog import cloudlog
from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import HOLD_AGE_S, read_zones

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
CONFIG_REFRESH_S = 5.0
DEFAULTS = {'enabled': True, 'roundabout_kph': 30.0, 'turn_kph': 30.0, 'stop_kph': 25.0, 'signal_kph': 0.0, 'decel': 0.45}
KIND_KEYS = {'roundabout': 'roundabout_kph', 'turn': 'turn_kph', 'stop': 'stop_kph', 'signal': 'signal_kph'}  # 'exit' never caps
BRAKING_OFFSET_M = 8.0  # the target speed is reached this far before the zone (stops)
REACH_EARLY_M = 40.0  # roundabouts and turns: already at the target this far before, so the turn assist can act
EARLY_KINDS = ('roundabout', 'turn')
RELEASE_M = 5.0  # hold the cap this far past the end of the zone
MIN_DECEL, MAX_DECEL = 0.2, 1.0  # a config typo must not make the car brake hard or not at all


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
  return config


def allowed_speed(distance: float, target: float, decel: float, offset: float = BRAKING_OFFSET_M) -> float:
  """Highest speed (m/s) that still lets the car reach `target` `offset` m before the zone with `decel` (m/s^2)."""
  return math.sqrt(target * target + 2.0 * decel * max(distance - offset, 0.0))


def compute_cap(zones: list[dict], config: dict, v_ego: float, age: float) -> tuple[float, str] | None:
  """(speed cap in m/s, kind of the zone that sets it), or None when no zone limits the speed."""
  best = None
  for zone in zones:
    key = KIND_KEYS.get(zone['kind'])
    target = float(config.get(key, 0.0)) * CV.KPH_TO_MS if key else 0.0
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


class NavSpeedCap:
  def __init__(self, config_path: Path | None = None, zones_path: str | None = None):
    self.config_path = config_path
    self.zones_path = zones_path
    self.config = load_config(config_path)
    self.config_time = time.monotonic()
    self.kind: str | None = None

  def update(self, v_ego: float, v_target: float, a_target: float) -> tuple[float, float]:
    """Apply the cap to (v_target, a_target)."""
    if time.monotonic() - self.config_time > CONFIG_REFRESH_S:
      self.config, self.config_time = load_config(self.config_path), time.monotonic()
    self.kind = None
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
      self.kind = cap[1]
    return v_new, a_new
