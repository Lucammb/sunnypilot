"""What else the Mapbox route knows about the road: speed limits, tunnels, traffic jams, and its shape (curves, the radius
of a roundabout, the angle of a turn).

Everything here is a pure function of the Directions response, computed once per route by navd. Distances are meters along
the route, on the same scale as route_zones.step_starts (the steps' own distances).
"""
import numpy as np

from openpilot.selfdrive.navigation.route import cumulative_distance, local_meters

KPH_PER_UNIT = {'km/h': 1.0, 'mph': 1.609344}
JAM_LEVELS = ('heavy', 'severe')
MIN_JAM_M = 200.0  # a shorter stretch of slow traffic is a junction, not a queue
JAM_GAP_M = 150.0  # stretches closer than this are one queue

SAMPLE_M = 8.0  # the route is resampled this finely to measure its shape
CURVE_WINDOW_M = 16.0  # the heading is compared this far behind and ahead: a single badly drawn point is averaged out
MAX_CURVE_RADIUS_M = 120.0  # wider bends are left to the driving model
MIN_CURVE_TURN_DEG = 30.0  # a bend must change the direction by this much: map noise and lane shifts do not
CURVE_GAP_M = 24.0
MIN_RING_RADIUS_M, MAX_RING_RADIUS_M = 6.0, 120.0


def route_distances(coordinates: np.ndarray, total_m: float) -> np.ndarray:
  """Meters along the route at each coordinate of its full geometry, scaled so that the end is at total_m."""
  along = cumulative_distance(coordinates) if len(coordinates) > 1 else np.zeros(1)
  return along * (total_m / along[-1]) if along[-1] > 0 else along


def _runs(values: list, wanted) -> list[tuple[int, int]]:
  """[first, last] index ranges of consecutive values for which wanted(value) is true."""
  runs, start = [], None
  for i, value in enumerate(values):
    if wanted(value):
      start = i if start is None else start
    elif start is not None:
      runs.append((start, i - 1))
      start = None
  if start is not None:
    runs.append((start, len(values) - 1))
  return runs


def _annotation(mapbox_route: dict, name: str) -> tuple[list, np.ndarray] | None:
  """(the annotation, meters along the route at each coordinate), or None if the route does not have it."""
  try:
    values = mapbox_route['legs'][0]['annotation'][name]
    coordinates = np.array(mapbox_route['geometry']['coordinates'], dtype=np.float64).reshape((-1, 2))
    total = sum(step['distance'] for step in mapbox_route['legs'][0]['steps'])
  except (KeyError, IndexError, TypeError, ValueError):
    return None
  if not isinstance(values, list) or len(values) != len(coordinates) - 1 or len(values) == 0:
    return None
  return values, route_distances(coordinates, total)


def limit_kph(entry) -> float | None:
  """km/h of one maxspeed annotation entry; None for unknown, and for no limit at all."""
  if not isinstance(entry, dict) or entry.get('unknown') or entry.get('none'):
    return None
  speed, unit = entry.get('speed'), KPH_PER_UNIT.get(entry.get('unit', 'km/h'))
  if not isinstance(speed, (int, float)) or isinstance(speed, bool) or unit is None or speed <= 0:
    return None
  return float(round(speed * unit))


def speed_limits(mapbox_route: dict) -> list[tuple[float, float, float]]:
  """(start, end, km/h) stretches of the route with a known speed limit, in order."""
  data = _annotation(mapbox_route, 'maxspeed')
  if data is None:
    return []
  values, along = data
  kph = [limit_kph(v) for v in values]
  limits = []
  for first, last in _runs(kph, lambda v: v is not None):
    i = first
    while i <= last:  # split the run where the limit changes
      j = i
      while j < last and kph[j + 1] == kph[i]:
        j += 1
      limits.append((float(along[i]), float(along[j + 1]), kph[i]))
      i = j + 1
  return limits


def limit_at(limits: list[tuple[float, float, float]], progress: float) -> float | None:
  for start, end, kph in limits:
    if start <= progress < end:
      return kph
  return None


def jam_zones(mapbox_route: dict) -> list[dict]:
  """Queues on the route (heavy or severe traffic over a real distance), as zones of kind 'jam'."""
  data = _annotation(mapbox_route, 'congestion')
  if data is None:
    return []
  values, along = data
  merged: list[list[float]] = []
  for first, last in _runs(values, lambda v: v in JAM_LEVELS):
    start, end = float(along[first]), float(along[last + 1])
    if merged and start - merged[-1][1] <= JAM_GAP_M:
      merged[-1][1] = end
    else:
      merged.append([start, end])
  return [{'kind': 'jam', 'start': start, 'end': end} for start, end in merged if end - start >= MIN_JAM_M]


def tunnels(route: list[dict], route_geometry: list[np.ndarray], starts: list[float]) -> list[tuple[float, float]]:
  """(start, end) of the tunnels on the route: Mapbox marks the road after an intersection as 'tunnel'."""
  marks: list[tuple[float, bool]] = []  # (meters along the route, the road from here on is in a tunnel)
  for i, step in enumerate(route):
    for intersection in step.get('intersections', []):
      offset = locate(intersection, route_geometry[i], step['distance'])
      if offset is not None:
        marks.append((starts[i] + offset, 'tunnel' in (intersection.get('classes') or [])))
  marks.sort()
  total = starts[-1] + route[-1]['distance'] if route else 0.0
  found: list[tuple[float, float]] = []
  for (at, inside), (following, _) in zip(marks, marks[1:] + [(total, False)], strict=True):
    if inside and following > at:
      if found and at - found[-1][1] < 1.0:
        found[-1] = (found[-1][0], following)
      else:
        found.append((at, following))
  return found


def locate(intersection: dict, geometry: np.ndarray, step_distance: float) -> float | None:
  """Meters from the start of the step to an intersection, by its own coordinates (not by its index, which counts
  from the start of the whole route)."""
  geometry = np.asarray(geometry, dtype=np.float64).reshape((-1, 2))
  location = intersection.get('location')
  if not isinstance(location, (list, tuple)) or len(location) != 2 or len(geometry) < 2:
    return 0.0 if isinstance(location, (list, tuple)) and len(geometry) < 2 else None
  local, point = local_meters(geometry, geometry[0]), local_meters(location, geometry[0])
  a, b = local[:-1], local[1:]
  ab = b - a
  t = np.clip(((point - a) * ab).sum(axis=1) / np.maximum((ab * ab).sum(axis=1), 1e-12), 0, 1)
  d = np.linalg.norm(a + t[:, None] * ab - point, axis=1)
  k = int(d.argmin())
  along = cumulative_distance(geometry)
  offset = along[k] + t[k] * (along[k + 1] - along[k])
  return float(offset * (step_distance / along[-1])) if along[-1] > 0 else 0.0


def ring_radius(geometry: np.ndarray) -> float | None:
  """Radius (m) of the circle through a roundabout step's points, or None when they do not show one."""
  geometry = np.asarray(geometry, dtype=np.float64).reshape((-1, 2))
  if len(geometry) < 3:
    return None
  p = local_meters(geometry, geometry[0])
  if np.ptp(p[:, 0]) + np.ptp(p[:, 1]) < 2.0:
    return None
  # least squares circle: x^2 + y^2 = 2 a x + 2 b y + c
  A = np.c_[2 * p, np.ones(len(p))]
  try:
    (a, b, c), _, rank, _ = np.linalg.lstsq(A, (p ** 2).sum(axis=1), rcond=None)
  except np.linalg.LinAlgError:
    return None
  if rank < 3 or c + a * a + b * b <= 0:
    return None
  radius = float(np.sqrt(c + a * a + b * b))
  return radius if MIN_RING_RADIUS_M <= radius <= MAX_RING_RADIUS_M else None


def turn_angle(maneuver: dict) -> float | None:
  """Degrees the road turns by at a maneuver (0: straight on, 180: back), from Mapbox's bearings."""
  before, after = maneuver.get('bearing_before'), maneuver.get('bearing_after')
  if not all(isinstance(b, (int, float)) and not isinstance(b, bool) for b in (before, after)):
    return None
  return float(abs((after - before + 180.0) % 360.0 - 180.0))


def curve_zones(route_geometry: list[np.ndarray], starts: list[float], distances: list[float]) -> list[dict]:
  """Tight bends of the route as zones of kind 'curve', with their radius."""
  points, along = [], []
  for geometry, start, distance in zip(route_geometry, starts, distances, strict=True):
    geometry = np.asarray(geometry, dtype=np.float64).reshape((-1, 2))
    if len(geometry) < 2 or distance <= 0:
      continue
    d = cumulative_distance(geometry)
    if d[-1] <= 0:
      continue
    points.append(geometry)
    along.append(start + d * (distance / d[-1]))
  if not points:
    return []
  coords, s = np.concatenate(points), np.concatenate(along)
  keep = np.r_[True, np.diff(s) > 1e-6]  # the steps share their end points
  coords, s = coords[keep], s[keep]
  if len(s) < 3 or s[-1] - s[0] < 4 * CURVE_WINDOW_M:
    return []
  local = local_meters(coords, coords[0])
  grid = np.arange(s[0], s[-1], SAMPLE_M)
  x, y = np.interp(grid, s, local[:, 0]), np.interp(grid, s, local[:, 1])
  heading = np.unwrap(np.arctan2(np.gradient(y), np.gradient(x)))
  w = max(1, round(CURVE_WINDOW_M / SAMPLE_M))
  turn = np.zeros(len(grid))  # radians per meter, over the window
  turn[w:-w] = (heading[2 * w:] - heading[:-2 * w]) / (2 * w * SAMPLE_M)
  tight = np.abs(turn) > 1.0 / MAX_CURVE_RADIUS_M
  zones: list[dict] = []
  for first, last in _runs(list(tight), bool):
    if zones and grid[first] - zones[-1]['end'] <= CURVE_GAP_M and np.sign(turn[first]) == zones[-1]['_sign']:
      first = zones.pop()['_first']
    change = abs(heading[min(last + w, len(grid) - 1)] - heading[max(first - w, 0)])
    zones.append({'kind': 'curve', 'start': float(grid[first]), 'end': float(grid[last]) + SAMPLE_M,
                  'radius': float(1.0 / np.abs(turn[first:last + 1]).max()), '_sign': float(np.sign(turn[first])), '_first': first,
                  '_turn': float(np.degrees(change))})
  return [{k: v for k, v in zone.items() if not k.startswith('_')} for zone in zones if zone['_turn'] >= MIN_CURVE_TURN_DEG]
