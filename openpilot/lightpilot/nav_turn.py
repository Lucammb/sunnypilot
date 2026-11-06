"""Automatic turn desire from the navigation route (off by default).

At an intersection turn on the route, this tells the lane-turn controller which way to turn, as if the driver had put the
blinker on. The driver's own blinker always wins, and the controller only acts below the lane-turn speed (LaneTurnValue)
and with LaneTurnDesire enabled, so the navigation speed cap (see speed_cap.py) must have slowed the car first.

Roundabouts (optional, off by default) follow the exit number: 1st exit = right, 2nd = straight (no desire), 3rd or later = left.

Settings in /data/lightpilot/config.json (optional, no params keys):
  {"nav_turn": {"enabled": true, "window_m": 25, "roundabout": false}}
"""
import json
import time
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.lightpilot.config import LIGHTPILOT_DIR
from openpilot.lightpilot.route_zones import read_zones

CONFIG_FILE = LIGHTPILOT_DIR / "config.json"
CONFIG_REFRESH_S = 5.0
READ_INTERVAL_S = 0.2
DEFAULTS = {'enabled': False, 'window_m': 25.0, 'roundabout': False}
MIN_WINDOW_M, MAX_WINDOW_M = 5.0, 60.0
AFTER_END_M = 5.0  # keep the desire this far past the end of the crossing, so that the turn is finished

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
  return config


def modifier_side(modifier: str | None) -> str | None:
  """left/right for a left/right modifier (slight and sharp included); None for straight, u-turns and unknowns."""
  if modifier in ('left', 'slight left', 'sharp left'):
    return LEFT
  if modifier in ('right', 'slight right', 'sharp right'):
    return RIGHT
  return None


def roundabout_side(exit_number) -> str | None:
  if not isinstance(exit_number, int) or isinstance(exit_number, bool):
    return None
  if exit_number == 1:
    return RIGHT
  if exit_number >= 3:
    return LEFT
  return None


def turn_side(zones: list[dict], config: dict, v_ego: float, age: float) -> str | None:
  """Side to turn now, from the nearest zone that is within the window, or None. Zone distances were written `age` s ago."""
  moved = v_ego * age
  for zone in zones:  # sorted by start: the nearest zone decides
    start, end = zone['start'] - moved, zone['end'] - moved
    if start > config['window_m'] or end < -AFTER_END_M:
      continue
    if zone['kind'] == 'turn':
      return modifier_side(zone.get('modifier'))
    if zone['kind'] == 'roundabout' and config['roundabout']:
      # only the entry, not the whole ring: the zone ends at the exit
      if start + 15.0 >= 0:
        return roundabout_side(zone.get('exit'))
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

  def update(self, v_ego: float, now: float | None = None) -> str | None:
    now = time.monotonic() if now is None else now
    try:
      if now - self.config_t >= CONFIG_REFRESH_S:
        self.config, self.config_t = load_config(self.config_path), now
      if not self.config['enabled']:
        self.side = None
        return None
      if now - self.read_t >= READ_INTERVAL_S:
        self.read_t = now
        data = read_zones(self.zones_path, now)
        self.side = None if data is None else turn_side(data[0], self.config, v_ego, data[1])
    except Exception:
      cloudlog.exception("lightpilot: nav turn failed")
      self.side = None
    return self.side
