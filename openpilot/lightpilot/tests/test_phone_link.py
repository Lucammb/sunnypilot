import json
import shutil
import subprocess
import time

import pytest
import requests

from openpilot.lightpilot.phone_link import PhoneLink, load_config

DEST = {'lat': 45.4, 'lon': 9.2, 'name': 'Casa'}


class Rig:
  """A fake server response, fake params and a fake route, wired into one PhoneLink."""

  def __init__(self):
    self.seq, self.dest, self.param, self.route, self.reports = 1, None, None, {'remaining_m': None}, []
    self.link = PhoneLink(self.sync, lambda: self.param, self.set_dest, lambda: setattr(self, 'param', None), lambda: self.route)

  def sync(self, report):
    self.reports.append(report)
    return {'seq': self.seq, 'dest': self.dest}

  def set_dest(self, d):
    self.param = d

  def phone_sets(self, dest=DEST):
    self.seq += 1
    self.dest = dest

  def phone_clears(self):
    self.seq += 1
    self.dest = None


def test_new_destination_is_applied():
  r = Rig()
  r.link.step()
  assert r.param is None
  r.phone_sets()
  r.link.step()
  assert r.param == DEST


def test_rearmed_after_boot_clear():
  r = Rig()
  r.phone_sets()
  r.link.step()
  r.param = None  # the manager cleared it on a boot or offroad transition
  r.route = {'remaining_m': 12000.0}
  r.link.step()
  assert r.param == DEST
  assert r.reports[-1]['arrived'] is False


def test_daemon_restart_keeps_running_route():
  r = Rig()
  r.phone_sets()
  r.param = {'latitude': 1, 'longitude': 2}  # already set, this process just started
  r.link.step()
  assert r.param == {'latitude': 1, 'longitude': 2}


def test_cancel_from_phone_removes_it():
  r = Rig()
  r.phone_sets()
  r.link.step()
  r.phone_clears()
  r.link.step()
  assert r.param is None


def test_does_not_touch_a_manual_destination():
  r = Rig()
  r.param = {'latitude': 1, 'longitude': 2}  # set over SSH, the phone never sent anything
  r.link.step()
  r.link.step()
  assert r.param == {'latitude': 1, 'longitude': 2}


def test_arrival_is_reported_and_not_rearmed():
  r = Rig()
  r.phone_sets()
  r.link.step()
  r.route = {'remaining_m': 900.0}
  r.link.step()
  r.route = {'remaining_m': 80.0}
  r.link.step()
  r.param = None  # navd removed it after arriving
  r.link.step()
  assert r.reports[-1]['arrived'] is True
  r.dest, r.seq = None, r.seq + 1  # the server settles it
  r.link.step()
  assert r.param is None


def test_new_destination_replaces_old():
  r = Rig()
  r.phone_sets()
  r.link.step()
  other = {'lat': 46.0, 'lon': 9.0, 'name': 'Altro'}
  r.phone_sets(other)
  r.link.step()
  assert r.param == other


def test_config(tmp_path):
  p = tmp_path / 'phone.json'
  assert load_config(str(p)) is None
  p.write_text('{"url": "x"}')
  assert load_config(str(p)) is None
  p.write_text(json.dumps({'url': 'https://e/api.php', 'token': 't'}))
  assert load_config(str(p))['token'] == 't'


@pytest.fixture
def server(tmp_path):
  if shutil.which('php') is None:
    pytest.skip('php not installed')
  import pathlib
  src = pathlib.Path(__file__).resolve().parents[1] / 'phone' / 'server'
  shutil.copytree(src, tmp_path / 'srv')
  (tmp_path / 'srv' / 'config.php').write_text("<?php return ['phone_token'=>'P'.str_repeat('a',20),'device_token'=>'D'.str_repeat('b',20),'ttl_hours'=>12];")
  port = 18765
  proc = subprocess.Popen(['php', '-S', f'127.0.0.1:{port}', '-t', str(tmp_path / 'srv')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  base = f'http://127.0.0.1:{port}/api.php'
  for _ in range(50):
    try:
      requests.get(base, timeout=0.2)
      break
    except requests.RequestException:
      time.sleep(0.1)
  yield base
  proc.terminate()


def test_server_end_to_end(server):
  phone, device = {'X-Token': 'P' + 'a' * 20}, {'X-Token': 'D' + 'b' * 20}
  assert requests.get(server, params={'a': 'state'}, headers={'X-Token': 'nope'}).status_code == 401
  assert requests.get(server, params={'a': 'state'}, headers=device).status_code == 400  # device cannot read the phone view
  assert requests.post(server, params={'a': 'set'}, headers=device, json=DEST).status_code == 400  # nor set a destination
  assert requests.post(server, params={'a': 'set'}, headers=phone, json={'lat': 999, 'lon': 0}).status_code == 400

  s = requests.get(server, params={'a': 'state'}, headers=phone).json()
  assert s['dest'] is None and s['device_online'] is False

  assert requests.post(server, params={'a': 'set'}, headers=phone, json=DEST).json() is not None
  d = requests.post(server, params={'a': 'sync'}, headers=device, json={'seq': None, 'has_dest': False}).json()
  assert d['dest']['lat'] == 45.4 and d['seq'] == 1

  requests.post(server, params={'a': 'sync'}, headers=device, json={'seq': 1, 'has_dest': True, 'remaining_m': 5000, 'remaining_s': 600, 'next_m': 300, 'text': 'Svolta a destra'})
  s = requests.get(server, params={'a': 'state'}, headers=phone).json()
  assert s['status'] == 'navigating' and s['device_online'] is True and s['route']['remaining_m'] == 5000

  requests.post(server, params={'a': 'sync'}, headers=device, json={'seq': 1, 'has_dest': False, 'arrived': True, 'remaining_m': 40})
  s = requests.get(server, params={'a': 'state'}, headers=phone).json()
  assert s['dest'] is None and s['status'] == 'arrived'
  assert requests.post(server, params={'a': 'sync'}, headers=device, json={}).json()['dest'] is None

  requests.post(server, params={'a': 'set'}, headers=phone, json=DEST)
  requests.post(server, params={'a': 'clear'}, headers=phone, json={})
  assert requests.post(server, params={'a': 'sync'}, headers=device, json={}).json()['dest'] is None
