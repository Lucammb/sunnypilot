"""Driving zones ahead on the Mapbox route (roundabouts, turns, exits, stop signs, traffic signals).

navd extracts the zones from the route and, once a second, writes the ones ahead of the car, as distances, to a small
JSON file in /dev/shm. The longitudinal planner reads it. No cereal or params-key changes are needed.

Mapbox: step i's `maneuver` happens where the step starts, and the step's `distance` runs to the next maneuver.
With `roundabout_exits=true` (navd asks for it) a roundabout step covers the ring from entry to exit, a natural speed zone;
its length is capped anyway, so that a route without it can never hold a long speed cap.
"""
import json
import os
import time

import numpy as np

from openpilot.selfdrive.navigation.route import cumulative_distance

ZONES_PATH = os.environ.get("LIGHTPILOT_ZONES", "/dev/shm/lightpilot_zones.json")
MAX_AGE_S = 3.0  # a zones file older than this is ignored: navd is gone or the route is lost
HOLD_AGE_S = 15.0  # the planner's caps and driving mode keep using a file this old (navd is busy recomputing the route, the network is slow)

ROUNDABOUT_TYPES = ("roundabout", "rotary", "roundabout turn")
TURN_TYPES = ("turn", "end of road")
CONTINUE_TURNS = ("left", "right", "sharp left", "sharp right", "uturn")  # "continue left": the road itself turns at the junction
EXIT_TYPES = ("off ramp", "fork")
TURN_ZONE_M = 15.0  # length of the crossing at an intersection
MAX_RAMP_M = 400.0  # the exit speed is held at most this far along the ramp
MAX_ROUNDABOUT_M = 100.0  # a ring is never longer; a longer step is a route that did not end it at the exit
POINT_ZONE_M = 10.0  # length around a stop sign or traffic signal
LOOKAHEAD_M = 1500.0
PASSED_M = 20.0  # keep a zone this long after its end, so that the consumer sees it finish
MAX_ZONES = 8


def step_starts(route) -> list[float]:
  """Distance along the route (m) at which each step starts, by the steps' own distances."""
  starts, total = [], 0.0
  for step in route:
    starts.append(total)
    total += step['distance']
  return starts


def extract_zones(route, route_geometry) -> list[dict]:
  """All zones on the route, with start/end as distances along the route (m), sorted by start."""
  starts = step_starts(route)
  zones = []
  for i, step in enumerate(route):
    maneuver = step.get('maneuver', {})
    mtype, modifier = maneuver.get('type'), maneuver.get('modifier', '')
    if mtype in ROUNDABOUT_TYPES:
      zones.append({'kind': 'roundabout', 'start': starts[i], 'end': starts[i] + min(step['distance'], MAX_ROUNDABOUT_M), 'exit': maneuver.get('exit')})
    elif (mtype in TURN_TYPES and modifier != 'straight') or (mtype == 'continue' and modifier in CONTINUE_TURNS):
      zones.append({'kind': 'turn', 'start': starts[i], 'end': starts[i] + TURN_ZONE_M, 'modifier': modifier})
    elif mtype == 'off ramp':  # the zone is the ramp itself: the exit speed is held along it (nav_exit.py)
      zones.append({'kind': 'exit', 'start': starts[i], 'end': starts[i] + min(max(step['distance'], TURN_ZONE_M), MAX_RAMP_M), 'modifier': modifier,
                    'ramp': True})
    elif mtype in EXIT_TYPES:
      zones.append({'kind': 'exit', 'start': starts[i], 'end': starts[i] + TURN_ZONE_M, 'modifier': modifier})

    along = None
    for intersection in step.get('intersections', []):
      kind = 'signal' if intersection.get('traffic_signal') else 'stop' if intersection.get('stop_sign') else None
      if kind is None:
        continue
      if along is None:
        geometry = route_geometry[i]
        along = cumulative_distance(geometry) if len(geometry) > 1 else np.zeros(1)
        scale = step['distance'] / along[-1] if along[-1] > 0 else 1.0  # Mapbox's distance is the reference
      offset = float(along[min(intersection.get('geometry_index', 0), len(along) - 1)]) * scale
      zones.append({'kind': kind, 'start': starts[i] + offset - POINT_ZONE_M / 2, 'end': starts[i] + offset + POINT_ZONE_M / 2})
  return sorted(zones, key=lambda z: z['start'])


def zones_ahead(zones: list[dict], progress: float) -> list[dict]:
  """The zones within the lookahead, as distances from the car (negative: already in or past)."""
  ahead = []
  for zone in zones:
    start, end = zone['start'] - progress, zone['end'] - progress
    if end >= -PASSED_M and start <= LOOKAHEAD_M:
      ahead.append({**zone, 'start': start, 'end': end})
  return ahead[:MAX_ZONES]


def write_zones(zones: list[dict], path: str | None = None, now: float | None = None, route: bool = True) -> None:
  """route=False: navd is running but there is no route (the consumers then see no data at all)."""
  path = path or ZONES_PATH
  tmp = path + ".tmp"
  try:
    with open(tmp, "w") as f:
      json.dump({'t': time.monotonic() if now is None else now, 'zones': zones, 'route': route}, f)
    os.replace(tmp, path)
  except OSError:
    pass


def read_zones(path: str | None = None, now: float | None = None, max_age: float = MAX_AGE_S) -> tuple[list[dict], float] | None:
  """(zones, age in seconds) from the file, or None if it is missing, malformed or older than max_age.

  The distances in the file were true `age` seconds ago: every consumer moves them by speed * age."""
  try:
    with open(path or ZONES_PATH) as f:
      data = json.load(f)
    age = (time.monotonic() if now is None else now) - float(data['t'])
    if not 0 <= age <= max_age or data.get('route', True) is False:
      return None
    return list(data['zones']), age
  except (OSError, ValueError, KeyError, TypeError):
    return None
