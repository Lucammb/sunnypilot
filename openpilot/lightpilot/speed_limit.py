"""The speed limit as the target speed, when the driver asks for it.

The set speed is the car's own (the stalk); the software cannot change that number. What it can do is drive slower than
it. So:

- a tap on the speed-limit sign on the screen makes the limit shown there the speed to drive at ("fixed"). It stays that
  value, also when the limit changes later; a tap when the limit has changed takes the new one, a tap on the same limit
  turns it off;
- after a motorway or ring-road exit taken with the blinker (the ramp speed of nav_exit.py was in use), the limit is
  followed by itself ("follow"), so that the car does not go back to the motorway's set speed on an ordinary road. While
  no limit is known it is unknown_kph;
- changing the set speed on the car turns either off: the driver has decided.

It only ever lowers the target speed: a limit above the car's set speed changes nothing (raise the set speed on the car).
The limit is the one on the sign: the car's own reading, or the navigation map's when the car has none, with the offset
set in the speed-limit settings.

The UI writes a tap to a small file in /dev/shm and reads the state from another; no cereal or params-key changes.

Settings (optional) in /data/lightpilot/config.json:
  {"speed_limit": {"tap": true, "after_exit": true, "unknown_kph": 90}}
"""
import json
import os
import time
from pathlib import Path

from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import HOLD_AGE_S, read_limit

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
TAP_PATH = os.environ.get("LIGHTPILOT_LIMIT_TAP", "/dev/shm/lightpilot_limit_tap.json")
STATUS_PATH = os.environ.get("LIGHTPILOT_LIMIT", "/dev/shm/lightpilot_limit.json")
OFF, FIXED, FOLLOW = "off", "fixed", "follow"
DEFAULTS = {'tap': True, 'after_exit': True, 'unknown_kph': 90.0}
MIN_UNKNOWN_KPH, MAX_UNKNOWN_KPH = 50.0, 130.0
KPH = 1 / 3.6
TAP_MAX_AGE_S = 3.0
REFRESH_S = 0.2  # how often the tap file and the map's limit are read
STATUS_PERIOD_S = 0.5
STATUS_MAX_AGE_S = 3.0
CONFIG_REFRESH_S = 5.0
SET_SPEED_CHANGE_KPH = 0.9  # the car's set speed moves in whole km/h (or mph)
VALID_SET_KPH = (1.0, 250.0)  # outside: the cruise is not set


def load_config(path: Path | None = None) -> dict:
  config = dict(DEFAULTS)
  try:
    user = json.loads((path or CONFIG_FILE).read_text()).get('speed_limit', {})
    for key in ('tap', 'after_exit'):
      if isinstance(user.get(key), bool):
        config[key] = user[key]
    value = user.get('unknown_kph')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
      config['unknown_kph'] = float(value)
  except (OSError, ValueError, AttributeError):
    pass
  config['unknown_kph'] = min(max(config['unknown_kph'], MIN_UNKNOWN_KPH), MAX_UNKNOWN_KPH)
  return config


def _write(path: str, data: dict) -> None:
  tmp = path + ".tmp"
  try:
    with open(tmp, "w") as f:
      json.dump(data, f)
    os.replace(tmp, path)
  except OSError:
    pass


def _read(path: str) -> dict | None:
  try:
    with open(path) as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except (OSError, ValueError):
    return None


def write_tap(path: str | None = None, now: float | None = None) -> None:
  """The driver tapped the speed-limit sign."""
  _write(path or TAP_PATH, {'t': time.monotonic() if now is None else now})


def read_status(path: str | None = None, now: float | None = None) -> dict | None:
  """{'mode': 'fixed' | 'follow', 'kph': speed driven to} while the limit is the target speed, else None."""
  data = _read(path or STATUS_PATH)
  try:
    if data is None or not 0 <= (time.monotonic() if now is None else now) - float(data['t']) <= STATUS_MAX_AGE_S:
      return None
    if data.get('mode') not in (FIXED, FOLLOW):
      return None
    return {'mode': data['mode'], 'kph': float(data['kph'])}
  except (KeyError, TypeError, ValueError):
    return None


class LimitHold:
  """Cheap to call every planner cycle: the files are read a few times a second."""

  def __init__(self, tap_path: str | None = None, status_path: str | None = None, config_path: Path | None = None, zones_path: str | None = None):
    self.tap_path, self.status_path = tap_path or TAP_PATH, status_path or STATUS_PATH
    self.config_path, self.zones_path = config_path, zones_path
    self.config = load_config(config_path)
    self.mode = OFF
    self.value = 0.0  # m/s
    self.set_kph: float | None = None  # the car's set speed when last seen set
    self.map_limit: float | None = None  # m/s
    seen = _read(self.tap_path)
    self._tap_seen = seen.get('t') if seen else None  # a tap from before the start is not a tap now
    self._refresh_time = self._status_time = self._config_time = float('-inf')
    self._state: tuple[str, int] = (OFF, 0)

  def map_speed_limit(self, now: float | None = None) -> float:
    """The navigation map's limit (m/s) where the car is, 0 when unknown."""
    self._refresh(time.monotonic() if now is None else now)
    return self.map_limit or 0.0

  def _refresh(self, now: float) -> bool:
    """Read the files when due. True: the sign was tapped."""
    if now - self._config_time >= CONFIG_REFRESH_S:
      self._config_time, self.config = now, load_config(self.config_path)
    if now - self._refresh_time < REFRESH_S:
      return False
    self._refresh_time = now
    limit = read_limit(self.zones_path, now, HOLD_AGE_S)
    self.map_limit = None if limit is None else limit * KPH
    tap = _read(self.tap_path)
    t = tap.get('t') if tap else None
    if t is None or t == self._tap_seen:
      return False
    self._tap_seen = t
    return isinstance(t, (int, float)) and 0 <= now - t <= TAP_MAX_AGE_S

  def update(self, v_set_kph: float, limit: float, exited: bool, enabled: bool = True, now: float | None = None) -> float | None:
    """v_set_kph: the car's set speed; limit: the limit on the sign (m/s, 0: none); exited: a motorway exit was just
    taken; enabled: the cruise is on (the set speed means nothing otherwise). Returns the speed (m/s) to drive at most,
    or None."""
    now = time.monotonic() if now is None else now
    try:
      tapped = self._refresh(now)
      if enabled and VALID_SET_KPH[0] <= v_set_kph <= VALID_SET_KPH[1]:
        if self.set_kph is not None and abs(v_set_kph - self.set_kph) >= SET_SPEED_CHANGE_KPH:
          self.mode = OFF  # the driver changed the set speed: that is the speed wanted
        self.set_kph = v_set_kph
      if tapped and self.config['tap']:
        if limit > 0 and (self.mode == OFF or round(limit / KPH) != round(self.value / KPH)):
          self.mode, self.value = FIXED, limit
        else:
          self.mode = OFF
      elif exited and self.config['after_exit'] and self.mode == OFF:
        self.mode, self.value = FOLLOW, limit if limit > 0 else self.config['unknown_kph'] * KPH
      if self.mode == FOLLOW and limit > 0:
        self.value = limit
      state = (self.mode, round(self.value / KPH))
      if now - self._status_time >= STATUS_PERIOD_S or state != self._state:  # a tap shows on the screen at once
        self._status_time, self._state = now, state
        _write(self.status_path, {'t': now, 'mode': self.mode, 'kph': round(self.value / KPH)})
      return self.value if self.mode != OFF else None
    except Exception:
      return None  # the planner must never fail because of this
