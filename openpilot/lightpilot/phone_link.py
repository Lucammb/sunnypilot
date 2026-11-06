#!/usr/bin/env python3
"""Phone link: the navigation destination set from the phone app reaches the device, and survives reboots.

A small server (lightpilot/phone/server) keeps the destination chosen on the phone. This process, run by the manager,
asks it every few seconds (and reports the route's progress back, so the phone can show it), and:
  - puts a new destination into NavDestination;
  - puts it back after a boot or an offroad transition (both clear NavDestination), while it is still fresh;
  - removes it when it is cancelled from the phone;
  - tells the server when the destination was reached, so it is not armed again.
It only moves a destination. Nothing here touches driving.

Settings in /data/lightpilot/phone.json: {"url": "https://example.com/nav/api.php", "token": "<device token>"}.
Without them the process just idles, so the manager does not restart it in a loop.
"""
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import requests

CONFIG_PATH = '/data/lightpilot/phone.json'
POLL_SECONDS = 5.0
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


class PhoneLink:
  """One sync step at a time; every outside effect (server, params, route state) is injected so it can be tested."""

  def __init__(self, sync: Callable[[dict], dict], get_dest: Callable[[], dict | None], set_dest: Callable[[dict], None],
               clear_dest: Callable[[], None], route_state: Callable[[], dict]):
    self.sync = sync
    self.get_dest = get_dest
    self.set_dest = set_dest
    self.clear_dest = clear_dest
    self.route_state = route_state
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
    resp = self.sync(report)
    seq, dest = resp.get('seq'), resp.get('dest')

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
  sm = messaging.SubMaster(['navInstruction'])

  def get_dest():
    return params.get('NavDestination')

  def set_dest(dest: dict):
    params.put('NavDestination', {'latitude': dest['lat'], 'longitude': dest['lon'], 'place_name': dest.get('name', '')})
    params.put_bool('NavigationEnabled', True)

  def route_state() -> dict:
    sm.update(0)
    if sm.valid['navInstruction'] and sm.alive['navInstruction']:
      nav = sm['navInstruction']
      return {'remaining_m': float(nav.distanceRemaining), 'remaining_s': float(nav.timeRemaining),
              'next_m': float(nav.maneuverDistance), 'text': nav.maneuverPrimaryText}
    return {'remaining_m': None}

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
      link = PhoneLink(_http_sync(cfg), get_dest, set_dest, lambda: params.remove('NavDestination'), route_state)
    try:
      link.step()
      delay = POLL_SECONDS
    except (requests.RequestException, ValueError):
      delay = min(MAX_BACKOFF, delay * 2)  # no network yet (just booted) or server down: keep what we have
    time.sleep(delay)


if __name__ == '__main__':
  main()
