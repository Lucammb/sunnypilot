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


def test_thin_route_keeps_ends_and_limits_points():
  from openpilot.lightpilot.phone_link import thin_route
  coords = [(45.0 + i * 0.00001, 9.0) for i in range(5000)]  # ~5.5 km, a point every 1.1 m
  thinned = thin_route(coords)
  assert thinned[0] == [45.0, 9.0] and thinned[-1] == [round(coords[-1][0], 5), 9.0]
  assert 300 < len(thinned) < 400  # about one point per 15 m
  long_route = [(45.0 + i * 0.0001, 9.0) for i in range(20000)]  # ~220 km
  assert len(thin_route(long_route)) <= 603


def test_route_is_sent_once_and_again_when_it_changes():
  r = Rig()
  state = {'pos': {'lat': 45.4, 'lon': 9.2, 'bearing': 90.0, 'speed': 8.0}, 'route_key': 'a', 'route_pts': [[45.4, 9.2], [45.41, 9.21]]}
  r.link.live = lambda: state
  r.link.step()
  r.link.step()
  assert 'route_pts' in r.reports[0] and 'route_pts' not in r.reports[1]
  assert r.reports[1]['pos'] == state['pos'] and r.reports[1]['route_key'] == 'a'
  state['route_key'] = 'b'
  r.link.step()
  assert 'route_pts' in r.reports[2]


def test_old_server_refusing_the_route_does_not_break_the_destination():
  r = Rig()
  r.phone_sets()
  calls = []

  def picky_sync(report):
    calls.append(dict(report))
    if 'route_pts' in report:
      resp = requests.Response()
      resp.status_code = 400
      raise requests.HTTPError(response=resp)
    return {'seq': r.seq, 'dest': r.dest}

  r.link.sync = picky_sync
  r.link.live = lambda: {'pos': None, 'route_key': 'a', 'route_pts': [[45.4, 9.2], [45.41, 9.21]]}
  r.link.step()
  assert r.param == DEST  # the destination still got through
  assert 'route_pts' in calls[0] and 'route_pts' not in calls[1]


def test_route_is_sent_again_when_it_comes_back_or_the_server_lost_it(monkeypatch):
  from openpilot.lightpilot import phone_link
  clock = [1000.0]
  monkeypatch.setattr(phone_link.time, 'monotonic', lambda: clock[0])
  sent, server = [], {'key': ''}

  def sync(report):
    if 'route_pts' in report:
      sent.append(report['route_pts'])
      server['key'] = report['route_key']
    if not report['route_key']:
      server['key'] = ''  # like api.php: no route on the car, no route on the server
    return {'seq': 1, 'dest': None, 'route_key': server['key']}

  live = {'pos': None, 'route_key': 'K', 'route_pts': [[1.0, 2.0], [1.1, 2.1]]}
  link = phone_link.PhoneLink(sync, lambda: None, lambda d: None, lambda: None, lambda: {'remaining_m': None}, lambda: live)
  link.step()
  clock[0] += 10
  link.step()
  assert len(sent) == 1  # the server holds it
  server['key'] = ''  # the phone cleared the destination, the server dropped the route
  clock[0] += 10
  link.step()  # the answer tells the car that the server lost it
  clock[0] += 10
  link.step()
  assert len(sent) == 2
  live.update(route_key='', route_pts=None)  # route cleared on the car, then the same one again
  clock[0] += 10
  link.step()
  live.update(route_key='K', route_pts=[[1.0, 2.0], [1.1, 2.1]])
  server['key'] = ''
  clock[0] += 10
  link.step()
  assert len(sent) == 3


def test_diary_merges_the_logs_by_time(tmp_path):
  from openpilot.lightpilot.phone_link import diary
  mode, turn = tmp_path / 'mode.log', tmp_path / 'turn.log'
  mode.write_text('10:00:01 experimental auto reason=slow\n10:00:09 stock auto\n\n10:00:20 experimental auto reason=roundabout\n')
  turn.write_text('10:00:05 v=30km/h zone=roundabout:2\n')
  files = ((str(mode), 'M'), (str(turn), 'T'), (str(tmp_path / 'missing.log'), 'X'))
  assert diary(files) == ['M 10:00:01 experimental auto reason=slow', 'T 10:00:05 v=30km/h zone=roundabout:2', 'M 10:00:09 stock auto',
                          'M 10:00:20 experimental auto reason=roundabout']
  assert diary(files, max_lines=2) == ['M 10:00:09 stock auto', 'M 10:00:20 experimental auto reason=roundabout']
  mode.write_text(''.join(f'11:{i // 60:02d}:{i % 60:02d} line {i} {"x" * 300}\n' for i in range(400)))  # long file, long lines
  lines = diary(files)
  assert 0 < len(lines) <= 80 and all(len(line) <= 202 for line in lines) and lines[-1].startswith('M 11:06:39 line 399')
  assert not any('line 0 ' in line for line in lines)


def test_diary_is_sent_only_when_it_changed():
  r = Rig()
  log = {'lines': ['M 10:00:01 a']}
  r.link.live = lambda: {'pos': None, 'route_key': '', 'route_pts': None, 'log': list(log['lines'])}
  r.link.step()
  r.link.step()
  log['lines'].append('M 10:00:05 b')
  r.link.step()
  assert [rep.get('log') for rep in r.reports] == [['M 10:00:01 a'], None, ['M 10:00:01 a', 'M 10:00:05 b']]


def test_server_keeps_the_diary_and_the_queue(server):
  phone, device = {'X-Token': 'P' + 'a' * 20}, {'X-Token': 'D' + 'b' * 20}
  assert requests.get(server, params={'a': 'log'}, headers=phone).json() == {'at': 0, 'lines': []}
  assert requests.get(server, params={'a': 'log'}, headers=device).status_code == 400  # the diary is for the phone
  requests.post(server, params={'a': 'set'}, headers=phone, json=DEST)
  requests.post(server, params={'a': 'sync'}, headers=device, json={'seq': 1, 'has_dest': True, 'remaining_m': 5000, 'jam_m': 820,
                                                                    'log': ['M 10:00:01 perché', 7, 'x' * 500]})
  got = requests.get(server, params={'a': 'log'}, headers=phone).json()
  assert got['lines'] == ['M 10:00:01 perché', 'x' * 240] and got['at'] > 0
  assert requests.get(server, params={'a': 'state'}, headers=phone).json()['route']['jam_m'] == 820
  requests.post(server, params={'a': 'sync'}, headers=device, json={'seq': 1, 'has_dest': True, 'remaining_m': 4000})  # no diary this time
  assert requests.get(server, params={'a': 'log'}, headers=phone).json()['lines'][0] == 'M 10:00:01 perché'  # kept
