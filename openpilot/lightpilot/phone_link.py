#!/usr/bin/env python3
"""Phone link: the navigation destination set from the phone app reaches the device, and survives reboots.

A small server (lightpilot/phone/server) keeps the destination chosen on the phone. This process, run by the manager,
asks it every few seconds (and reports the car's position and the route's progress back, so the phone can show the map), and:
  - puts a new destination into NavDestination;
  - puts it back after a boot or an offroad transition (both clear NavDestination), while it is still fresh;
  - removes it when it is cancelled from the phone;
  - tells the server when the destination was reached, so it is not armed again.
It only moves a destination. Nothing here touches driving.

Settings in /data/lightpilot/phone.json: {"url": "https://example.com/nav/api.php", "token": "<device token>"}.
Without them the process just idles, so the manager does not restart it in a loop.
"""
import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path

import requests

CONFIG_PATH = '/data/lightpilot/phone.json'
POLL_SECONDS = 5.0
LIVE_POLL_SECONDS = 2.0  # while navigating, the phone map follows the car
ROUTE_POINT_SPACING_M = 15.0  # the route sent to the phone is thinned to this spacing at least
ROUTE_MAX_POINTS = 600
RESEND_S = 8.0  # the route is not sent again sooner than this, whatever the server says
IDLE_SECONDS = 30.0
MAX_BACKOFF = 30.0
ARRIVED_WITHIN_M = 250.0  # NavDestination vanishing with less than this left on the route means "arrived"


def load_config(path: str = CONFIG_PATH) -> dict | None:
  try:
    cfg = json.loads(Path(path).read_text())
  except (OSError, ValueError):
    return None
  if not isinstance(cfg, dict) or not cfg.get('url') or not cfg.get('token'):
    return None
  return cfg


def thin_route(coords: list[tuple[float, float]], spacing_m: float = ROUTE_POINT_SPACING_M, max_points: int = ROUTE_MAX_POINTS) -> list[list[float]]:
  """The route as [[lat, lon], ...] with 5 decimals (~1 m), points closer than the spacing dropped, the last one always kept.
  Long routes get a wider spacing so that there are at most max_points."""
  if len(coords) < 2:
    return [[round(la, 5), round(lo, 5)] for la, lo in coords]

  def meters(a, b):
    return math.hypot((b[0] - a[0]) * 111_320.0, (b[1] - a[1]) * 111_320.0 * math.cos(math.radians(a[0])))

  total = sum(meters(a, b) for a, b in zip(coords[:-1], coords[1:], strict=True))
  spacing = max(spacing_m, total / max_points)
  kept = [coords[0]]
  for point in coords[1:-1]:
    if meters(kept[-1], point) >= spacing:
      kept.append(point)
  kept.append(coords[-1])
  return [[round(la, 5), round(lo, 5)] for la, lo in kept]


def route_key(coords: list[tuple[float, float]]) -> str:
  """Cheap identity of a route: changes when the route does."""
  if not coords:
    return ''
  return f'{len(coords)}:{coords[0][0]:.5f},{coords[0][1]:.5f}:{coords[-1][0]:.5f},{coords[-1][1]:.5f}'


class PhoneLink:
  """One sync step at a time; every outside effect (server, params, route state) is injected so it can be tested."""

  def __init__(self, sync: Callable[[dict], dict], get_dest: Callable[[], dict | None], set_dest: Callable[[dict], None],
               clear_dest: Callable[[], None], route_state: Callable[[], dict], live: Callable[[], dict] | None = None):
    self.sync = sync
    self.get_dest = get_dest
    self.set_dest = set_dest
    self.clear_dest = clear_dest
    self.route_state = route_state
    self.live = live  # the car's position and the route geometry for the phone map: {'pos', 'route_key', 'route_pts'}
    self.sent_route_key: str | None = None
    self.server_route_key: str | None = None  # the route the server says it holds (newer servers tell)
    self.sent_at = float('-inf')
    self.applied_seq: int | None = None  # the server destination this process has put into the params
    self.was_present = False
    self.last_remaining = float('inf')

  def step(self) -> None:
    present = self.get_dest() is not None
    route = self.route_state()
    if route.get('remaining_m') is not None:
      self.last_remaining = route['remaining_m']

    arrived = False
    # The destination vanished while it was ours: navd removes it on arrival, the manager on boot/offroad.
    if self.was_present and not present and self.applied_seq is not None and self.last_remaining < ARRIVED_WITHIN_M:
      arrived = True

    report = {'seq': self.applied_seq, 'has_dest': present, 'arrived': arrived, **route}
    live = self.live() if self.live is not None else {}
    points = live.get('route_pts')
    key = live.get('route_key') or ''
    if live.get('pos') is not None:
      report['pos'] = live['pos']
    report['route_key'] = key
    if not key:
      self.sent_route_key = self.server_route_key = None  # the route is gone: the same one may come back, and must be sent again
    held = self.server_route_key if self.server_route_key is not None else self.sent_route_key
    now = time.monotonic()
    if points and key != held and (key != self.sent_route_key or now - self.sent_at >= RESEND_S):
      report['route_pts'] = points  # only when the server lacks it: it is the big part of the message
      self.sent_at = now
    try:
      resp = self.sync(report)
    except requests.HTTPError as e:
      # An old server on the phone side refuses the big message (400): still do the destination part without the route.
      if e.response is None or e.response.status_code != 400 or 'route_pts' not in report:
        raise
      report.pop('route_pts')
      resp = self.sync(report)
    self.sent_route_key = key if 'route_pts' in report else self.sent_route_key
    seq, dest = resp.get('seq'), resp.get('dest')
    if 'route_key' in resp:
      self.server_route_key = str(resp['route_key'])

    if arrived:
      self.applied_seq = seq  # whatever the server holds now is already settled
      self.was_present = False
      self.last_remaining = float('inf')
      return

    if dest is not None:
      if self.applied_seq != seq:
        if present and self.applied_seq is None:
          self.applied_seq = seq  # already running (e.g. only this process restarted): leave the route alone
        else:
          self.set_dest(dest)  # new from the phone
          self.applied_seq = seq
          self.last_remaining = float('inf')
          present = True
      elif not present:
        self.set_dest(dest)  # cleared by a boot or an offroad transition: arm it again
        present = True
    elif self.applied_seq is not None and self.applied_seq != seq:
      if present:
        self.clear_dest()  # cancelled from the phone
      present = False
      self.applied_seq = seq
      self.last_remaining = float('inf')
    self.was_present = present


def _http_sync(cfg: dict) -> Callable[[dict], dict]:
  session = requests.Session()

  def sync(report: dict) -> dict:
    r = session.post(cfg['url'], params={'a': 'sync'}, json=report, headers={'X-Token': cfg['token']}, timeout=8)
    r.raise_for_status()
    return r.json()

  return sync


def main() -> None:  # pragma: no cover - needs the device
  import openpilot.cereal.messaging as messaging
  from openpilot.common.params import Params

  params = Params()
  from openpilot.lightpilot.nav_exit import read_advisory
  sm = messaging.SubMaster(['navInstruction', 'navRoute', 'gpsLocation', 'gpsLocationExternal'])

  def get_dest():
    return params.get('NavDestination')

  def set_dest(dest: dict):
    params.put('NavDestination', {'latitude': dest['lat'], 'longitude': dest['lon'], 'place_name': dest.get('name', '')})
    params.put_bool('NavigationEnabled', True)

  def route_state() -> dict:
    sm.update(0)
    if sm.valid['navInstruction'] and sm.alive['navInstruction']:
      nav = sm['navInstruction']
      advice = read_advisory(0.0)  # the zones are at most a second old: close enough for a phone that polls every 2 s
      return {'advice': None if advice is None else {'side': advice['side'], 'm': round(advice['distance'])},
              'remaining_m': float(nav.distanceRemaining), 'remaining_s': float(nav.timeRemaining),
              'next_m': float(nav.maneuverDistance), 'text': nav.maneuverPrimaryText,
              'modifier': nav.maneuverModifier, 'street': nav.maneuverSecondaryText,
              'limit_kmh': round(float(nav.speedLimit) * 3.6) if nav.speedLimit > 0 else None,
              'lanes': [{'d': [str(d) for d in lane.directions], 'a': bool(lane.active), 'ad': str(lane.activeDirection)}
                        for lane in nav.lanes]}
    return {'remaining_m': None}

  def live() -> dict:
    from openpilot.selfdrive.navigation.helpers import latest_gps_source
    out: dict = {'pos': None, 'route_key': '', 'route_pts': None}
    source = latest_gps_source(sm)
    if source is not None:
      gps = sm[source]
      out['pos'] = {'lat': round(gps.latitude, 6), 'lon': round(gps.longitude, 6), 'bearing': round(gps.bearingDeg, 1), 'speed': round(gps.speed, 1)}
    coords = [(c.latitude, c.longitude) for c in sm['navRoute'].coordinates] if sm.valid['navRoute'] else []
    if coords:
      out['route_key'] = route_key(coords)
      out['route_pts'] = thin_route(coords)
    return out

  link: PhoneLink | None = None
  loaded = None
  delay = POLL_SECONDS
  while True:
    cfg = load_config(os.environ.get('LP_PHONE_CONFIG', CONFIG_PATH))
    if cfg is None:
      link, loaded = None, None
      time.sleep(IDLE_SECONDS)
      continue
    if link is None or cfg != loaded:
      loaded = cfg
      link = PhoneLink(_http_sync(cfg), get_dest, set_dest, lambda: params.remove('NavDestination'), route_state, live)
    try:
      link.step()
      delay = LIVE_POLL_SECONDS if params.get('NavDestination') is not None else POLL_SECONDS
    except (requests.RequestException, ValueError):
      delay = min(MAX_BACKOFF, delay * 2)  # no network yet (just booted) or server down: keep what we have
    time.sleep(delay)


if __name__ == '__main__':
  main()
