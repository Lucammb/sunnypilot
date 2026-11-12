"""Stock or experimental longitudinal driving.

The base software always lets the driving model's own speed plan ("e2e": stops at red lights, slows for what it sees)
compete with the cruise and lead-car plans. Here that is a choice again:

- without a navigation route, the driver picks: "stock" (cruise and lead car only) or "experimental" (the model's plan
  too), with the button at the top right of the road view. The choice is kept in /data/lightpilot/drive_mode.
- with a navigation route, it is automatic: experimental from shortly before a roundabout, a turn, a stop sign or a
  traffic signal on the route until just past it, stock everywhere else.

The planner asks DriveMode.update() every cycle whether the model's plan takes part, and writes what it decided to a
small file in /dev/shm that the UI reads. No cereal or params-key changes are needed.

If the route data stops arriving for a few seconds (navd busy recomputing, slow network), the last automatic decision is kept
for up to 15 s instead of falling back to the driver's choice. Every decision is written to /data/lightpilot/mode.log:
  tail -f /data/lightpilot/mode.log

Settings (optional) in /data/lightpilot/config.json:
  {"drive_mode": {"auto_in_navigation": true, "ahead_s": 12, "min_ahead_m": 150}}
"""
import json
import os
import time
from pathlib import Path

from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import HOLD_AGE_S, read_zones

STOCK, EXPERIMENTAL = "stock", "experimental"
DEFAULT_CHOICE = EXPERIMENTAL  # what the base software does when nobody has chosen
CHOICE_FILE = LIGHTPILOT_DIR / "drive_mode"
CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
TRACE_FILE = LIGHTPILOT_DIR / "mode.log"
TRACE_MAX_BYTES = 200_000
TRACE_EVERY_S = 2.0  # while automatic: one line this often, besides every change
STATUS_PATH = os.environ.get("LIGHTPILOT_DRIVE_MODE", "/dev/shm/lightpilot_drive_mode.json")
STATUS_MAX_AGE_S = 3.0  # a status older than this is ignored by the UI: the planner is not running

DEFAULTS = {'auto_in_navigation': True, 'ahead_s': 12.0, 'min_ahead_m': 150.0}
AUTO_KINDS = ('roundabout', 'turn', 'stop', 'signal')
RELEASE_M = 15.0  # stay experimental this far past the end of the zone
HOLD_BELOW_MS = 8.0  # never drop the model's plan by itself below this speed while that plan is the one braking
REFRESH_S = 0.5  # how often the choice file is read and the status file written
CONFIG_REFRESH_S = 5.0
MIN_AHEAD_S, MAX_AHEAD_S = 5.0, 30.0
MIN_AHEAD_M, MAX_AHEAD_M = 50.0, 600.0


def read_choice(path: Path | None = None) -> str:
  try:
    value = (path or CHOICE_FILE).read_text().strip()
  except OSError:
    return DEFAULT_CHOICE
  return value if value in (STOCK, EXPERIMENTAL) else DEFAULT_CHOICE


def write_choice(mode: str, path: Path | None = None) -> bool:
  path = path or CHOICE_FILE
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(mode + "\n")
    os.replace(tmp, path)
    return True
  except OSError:
    return False


def toggle_choice(path: Path | None = None) -> str:
  """Switch the driver's choice and return the new one (the old one if it could not be saved)."""
  current = read_choice(path)
  new = STOCK if current == EXPERIMENTAL else EXPERIMENTAL
  return new if write_choice(new, path) else current


def load_config(path: Path | None = None) -> dict:
  config = dict(DEFAULTS)
  try:
    user = json.loads((path or CONFIG_FILE).read_text()).get('drive_mode', {})
    if isinstance(user.get('auto_in_navigation'), bool):
      config['auto_in_navigation'] = user['auto_in_navigation']
    for key in ('ahead_s', 'min_ahead_m'):
      value = user.get(key)
      if isinstance(value, (int, float)) and not isinstance(value, bool):
        config[key] = float(value)
  except (OSError, ValueError, AttributeError):
    pass
  config['ahead_s'] = min(max(config['ahead_s'], MIN_AHEAD_S), MAX_AHEAD_S)
  config['min_ahead_m'] = min(max(config['min_ahead_m'], MIN_AHEAD_M), MAX_AHEAD_M)
  return config


def zone_ahead(zones: list[dict], v_ego: float, age: float, config: dict) -> str | None:
  """Kind of the nearest zone that calls for experimental driving now, or None."""
  reach = max(config['min_ahead_m'], v_ego * config['ahead_s'])
  for zone in zones:  # sorted by start
    if zone.get('kind') not in AUTO_KINDS:
      continue
    start, end = zone['start'] - v_ego * age, zone['end'] - v_ego * age  # the file is up to a few seconds old
    if end >= -RELEASE_M and start <= reach:
      return zone['kind']
  return None


def describe_ahead(zones: list[dict], v_ego: float, age: float) -> str:
  """'roundabout@132m' for the nearest zone that matters to the mode (distance from the car now), or 'none'."""
  moved = v_ego * age
  for zone in zones:
    if zone.get('kind') in AUTO_KINDS and zone['end'] - moved >= -RELEASE_M:
      return f"{zone['kind']}@{zone['start'] - moved:.0f}m"
  return 'none'


def write_status(e2e: bool, auto: bool, reason: str | None, path: str | None = None, now: float | None = None) -> None:
  path = path or STATUS_PATH
  tmp = path + ".tmp"
  try:
    with open(tmp, "w") as f:
      json.dump({'t': time.monotonic() if now is None else now, 'e2e': e2e, 'auto': auto, 'reason': reason}, f)
    os.replace(tmp, path)
  except OSError:
    pass


def read_status(path: str | None = None, now: float | None = None) -> dict | None:
  """What the planner last decided ({'e2e', 'auto', 'reason'}), or None if it is missing, malformed or stale."""
  try:
    with open(path or STATUS_PATH) as f:
      data = json.load(f)
    age = (time.monotonic() if now is None else now) - float(data['t'])
    if not 0 <= age <= STATUS_MAX_AGE_S:
      return None
    return {'e2e': bool(data['e2e']), 'auto': bool(data['auto']), 'reason': data.get('reason')}
  except (OSError, ValueError, KeyError, TypeError):
    return None


class DriveMode:
  def __init__(self, choice_path: Path | None = None, config_path: Path | None = None, zones_path: str | None = None,
               status_path: str | None = None, trace_path: Path | None = None):
    self.choice_path, self.config_path, self.zones_path, self.status_path = choice_path, config_path, zones_path, status_path
    self.choice = read_choice(choice_path)
    self.config = load_config(config_path)
    self.e2e = self.choice == EXPERIMENTAL
    self.auto = False
    self.reason: str | None = None
    self._refresh_time = self._config_time = self._status_time = self._trace_time = float('-inf')
    self.trace_path = trace_path or TRACE_FILE
    self.detail = 'no route'

  def _trace(self, v_ego: float, now: float) -> None:
    """One line per decision change, and every couple of seconds while automatic. A log must never get in the way."""
    try:
      path = Path(self.trace_path)
      path.parent.mkdir(parents=True, exist_ok=True)
      mode, e2e = 'AUTO' if self.auto else 'manual', 'yes' if self.e2e else 'no'
      line = f"{time.strftime('%H:%M:%S')} v={v_ego * 3.6:.0f}km/h mode={mode} e2e={e2e} reason={self.reason} choice={self.choice} route_data={self.detail}"
      if path.exists() and path.stat().st_size > TRACE_MAX_BYTES:
        path.write_text('')
      with path.open('a') as f:
        f.write(line + '\n')
    except Exception:
      pass

  def _decide(self, v_ego: float, now: float) -> tuple[bool, bool, str | None, bool]:
    """(model plan takes part, automatic, reason, the driver just changed the choice)."""
    chosen = False
    if now - self._refresh_time >= REFRESH_S:
      self._refresh_time = now
      choice = read_choice(self.choice_path)
      chosen, self.choice = choice != self.choice, choice
    if now - self._config_time >= CONFIG_REFRESH_S:
      self._config_time, self.config = now, load_config(self.config_path)

    data = read_zones(self.zones_path, now, HOLD_AGE_S) if self.config['auto_in_navigation'] else None
    if data is None:  # no route: the driver's choice
      self.detail = 'no route' if self.config['auto_in_navigation'] else 'automatic off'
      return self.choice == EXPERIMENTAL, False, None, chosen
    self.detail = f"age={data[1]:.1f}s next={describe_ahead(data[0], v_ego, data[1])}"
    kind = zone_ahead(data[0], v_ego, data[1], self.config)
    return kind is not None, True, kind, False

  def update(self, v_ego: float, e2e_braking: bool, now: float | None = None) -> bool:
    """Whether the model's plan takes part in this cycle.

    e2e_braking: the model's plan is the one asking for the lowest acceleration, or for a stop."""
    now = time.monotonic() if now is None else now
    try:  # the planner must never fail because of this: on any problem, behave like the base software
      want, auto, reason, chosen = self._decide(v_ego, now)
    except Exception:
      want, auto, reason, chosen = True, False, None, False
    # Leaving experimental by itself (zone passed, route lost) while the model is holding the car at a red light would
    # make it drive off: wait until the car is moving or the model no longer brakes. A tap by the driver is immediate.
    if self.e2e and not want and not chosen and e2e_braking and v_ego < HOLD_BELOW_MS:
      want, reason = True, 'hold'
    changed = (want, auto, reason) != (self.e2e, self.auto, self.reason)
    self.e2e, self.auto, self.reason = want, auto, reason
    if changed or now - self._status_time >= REFRESH_S:
      self._status_time = now
      write_status(self.e2e, self.auto, self.reason, self.status_path, now)
    if changed or (self.auto and now - self._trace_time >= TRACE_EVERY_S):
      self._trace_time = now
      self._trace(v_ego, now)
    return self.e2e
