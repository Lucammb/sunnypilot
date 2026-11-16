"""Turn desire from the navigation route: which way the driving model should steer where the route leaves the road ahead.

Roundabouts (on by default): the model alone drives "straight on" through a roundabout, whatever the route says. Leaving
a roundabout is always a turn to the right, so:
- 1st exit: right from just before the entry until the exit is behind;
- 2nd exit or later: nothing at the entry and in the ring (the model follows the ring), then right for the route's exit,
  from just past the junction before it (Mapbox lists the junctions of the ring), so that an earlier exit is not taken.
Never "left": in a roundabout that would point against the traffic.

Intersection turns (off by default): at a turn of the route the model is told the side, as if the driver had put the
blinker on.

The driver's own blinker always wins, a car in the blind spot blocks it, and it only acts below max_kph, so the navigation
speed cap (see speed_cap.py) must have slowed the car first. It does not need the Lane Turn Desire setting.

Settings in /data/lightpilot/config.json (optional, no params keys):
  {"nav_turn": {"roundabout": true, "enabled": false, "window_m": 25, "max_kph": 45}}
"enabled" is for the intersection turns.
"""
import json
import time
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import read_zones

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
TRACE_FILE = LIGHTPILOT_DIR / "turn.log"
TRACE_NEAR_M = 80.0  # log while a zone is this close ahead
TRACE_MAX_BYTES = 200_000
CONFIG_REFRESH_S = 5.0
READ_INTERVAL_S = 0.2
DEFAULTS = {'enabled': False, 'window_m': 25.0, 'roundabout': True, 'max_kph': 45.0}
MIN_WINDOW_M, MAX_WINDOW_M = 5.0, 60.0
MIN_MAX_KPH, MAX_MAX_KPH = 20.0, 60.0  # the route turn speed limit; the driver can be above the cap with the gas pedal
AFTER_END_M = 5.0  # keep the desire this far past the end of the crossing, so that the turn is finished
PAST_JUNCTION_M = 4.0  # in a ring, the desire for the route's exit starts this far past the junction before it
BLIND_EXIT_M = 12.0  # ... or this far before the exit when the map does not say where that junction is
MAX_EXIT_RUN_M = 30.0

LEFT, RIGHT = 'left', 'right'


def load_config(path: Path | None = None) -> dict:
  config = dict(DEFAULTS)
  try:
    user = json.loads((path or CONFIG_FILE).read_text()).get('nav_turn', {})
    for key, default in DEFAULTS.items():
      value = user.get(key)
      if isinstance(default, bool):
        if isinstance(value, bool):
          config[key] = value
      elif isinstance(value, (int, float)) and not isinstance(value, bool):
        config[key] = float(value)
  except (OSError, ValueError, AttributeError):
    pass
  config['window_m'] = min(max(config['window_m'], MIN_WINDOW_M), MAX_WINDOW_M)
  config['max_kph'] = min(max(config['max_kph'], MIN_MAX_KPH), MAX_MAX_KPH)
  return config


def modifier_side(modifier: str | None) -> str | None:
  """left/right for a left/right modifier (slight and sharp included); None for straight, u-turns and unknowns."""
  if modifier in ('left', 'slight left', 'sharp left'):
    return LEFT
  if modifier in ('right', 'slight right', 'sharp right'):
    return RIGHT
  return None


def roundabout_side(zone: dict, start: float, end: float) -> str | None:
  """Right when it is time to leave the ring; start/end: meters to the entry and to the route's exit."""
  exit_number = zone.get('exit')
  if not isinstance(exit_number, int) or isinstance(exit_number, bool):
    return RIGHT if modifier_side(zone.get('modifier')) == RIGHT else None  # a mini roundabout driven as a right turn
  if exit_number <= 1:
    return RIGHT
  if start > 0:
    return None  # not in the ring yet: the entry is the model's
  run = zone.get('exit_run')
  known = isinstance(run, (int, float)) and not isinstance(run, bool) and run > 0
  return RIGHT if end <= (min(run, MAX_EXIT_RUN_M) - PAST_JUNCTION_M if known else BLIND_EXIT_M) else None


def turn_side(zones: list[dict], config: dict, v_ego: float, age: float) -> str | None:
  """Side to turn now, from the nearest zone that is within the window, or None. Zone distances were written `age` s ago."""
  moved = v_ego * age
  for zone in zones:  # sorted by start: the nearest zone decides
    start, end = zone['start'] - moved, zone['end'] - moved
    if start > config['window_m'] or end < -AFTER_END_M:
      continue
    if zone['kind'] == 'turn' and config['enabled']:
      return modifier_side(zone.get('modifier'))
    if zone['kind'] == 'roundabout' and config['roundabout']:
      side = roundabout_side(zone, start, end)
      if side is not None:
        return side
      continue
    # exits, stop signs and signals: not ours, but they do not hide a turn right behind them either
  return None


class NavTurn:
  """Cheap to call every model frame: the file and the config are re-read only a few times a second."""

  def __init__(self, zones_path: str | None = None, config_path: Path | None = None):
    self.zones_path, self.config_path = zones_path, config_path
    self.config = dict(DEFAULTS)
    self.config_t = self.read_t = -1e9
    self.side: str | None = None
    self.zones: list[dict] = []
    self.zones_age = 0.0
    self.trace_path = TRACE_FILE
    self.trace_t = -1e9
    self.trace_last: str | None = None

  def trace(self, v_ego: float, threshold: float, reason: str, now: float | None = None) -> None:
    """One line a second (and on every change) while a zone is near: what the route says and why a turn was or was not applied.
    Read it with: tail -f /data/lightpilot/turn.log"""
    now = time.monotonic() if now is None else now
    try:
      moved = v_ego * self.zones_age
      near = [z for z in self.zones if z['start'] - moved <= TRACE_NEAR_M and z['end'] - moved >= -10.0]
      if not near:
        self.trace_last = None
        return
      z = near[0]
      detail = z.get('exit') if z['kind'] == 'roundabout' else z.get('modifier')
      line = (f"{time.strftime('%H:%M:%S')} v={v_ego * 3.6:.0f}km/h limit={threshold * 3.6:.0f}km/h zone={z['kind']}:{detail} "
              f"start={z['start'] - moved:.0f}m end={z['end'] - moved:.0f}m route_says={self.side} result={reason}")
      key = f"{z['kind']}{z['start']}{self.side}{reason}"
      if key == self.trace_last and now - self.trace_t < 1.0:
        return
      self.trace_last, self.trace_t = key, now
      path = Path(self.trace_path)
      if path.exists() and path.stat().st_size > TRACE_MAX_BYTES:
        path.write_text('')
      with path.open('a') as f:
        f.write(line + '\n')
    except Exception:
      pass  # a log must never get in the way

  @property
  def max_speed(self) -> float:
    """Speed (m/s) up to which the route turn is applied."""
    return self.config['max_kph'] / 3.6

  def update(self, v_ego: float, now: float | None = None) -> str | None:
    now = time.monotonic() if now is None else now
    try:
      if now - self.config_t >= CONFIG_REFRESH_S:
        self.config, self.config_t = load_config(self.config_path), now
      if not (self.config['enabled'] or self.config['roundabout']):
        self.side = None
        return None
      if now - self.read_t >= READ_INTERVAL_S:
        self.read_t = now
        data = read_zones(self.zones_path, now)
        self.zones, self.zones_age = ([], 0.0) if data is None else (data[0], data[1])
        self.side = None if data is None else turn_side(data[0], self.config, v_ego, data[1])
    except Exception:
      cloudlog.exception("lightpilot: nav turn failed")
      self.side = None
    return self.side
