<?php
// lightpilot phone link: keeps ONE destination and the device's progress. No database, one JSON file.
// phone  (X-Token = phone_token):  GET ?a=state   POST ?a=set {lat,lon,name}   POST ?a=clear
// phone also:  GET ?a=route -> {key, pts} (the route geometry, fetched when route_key changes)
//              GET ?a=cfg   -> {mapbox} (public pk. token for the map, kept here and not in the page)
// device (X-Token = device_token): POST ?a=sync {seq,has_dest,arrived,remaining_m,remaining_s,next_m,text,modifier,street,
//                                                  limit_kmh,lanes[{d:[dir..],a,ad}],
//                                                  pos{lat,lon,bearing,speed},route_key,route_pts[[lat,lon]..]} -> {seq,dest}
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$cfg = is_file(__DIR__ . '/config.php') ? require __DIR__ . '/config.php' : null;
if (!is_array($cfg) || strlen((string)($cfg['phone_token'] ?? '')) < 16 || strlen((string)($cfg['device_token'] ?? '')) < 16
    || str_starts_with((string)$cfg['phone_token'], 'CHANGE_ME')) {
  respond(500, ['error' => 'not configured']);
}
$ttl = (int)($cfg['ttl_hours'] ?? 12) * 3600;
$file = __DIR__ . '/data/state.json';
$routeFile = __DIR__ . '/data/route.json';
if (!is_dir(dirname($file))) { @mkdir(dirname($file), 0700, true); @file_put_contents(dirname($file) . '/.htaccess', "Require all denied\n"); }

function respond(int $code, array $body): never { http_response_code($code); echo json_encode($body, JSON_UNESCAPED_UNICODE); exit; }

$given = $_SERVER['HTTP_X_TOKEN'] ?? '';
$role = hash_equals($cfg['phone_token'], $given) ? 'phone' : (hash_equals($cfg['device_token'], $given) ? 'device' : null);
if ($role === null) { usleep(300000); respond(401, ['error' => 'unauthorized']); }

$body = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $limit = ($_GET['a'] ?? '') === 'sync' ? 262144 : 8192; // the device sends the route geometry
  $raw = file_get_contents('php://input', false, null, 0, $limit);
  $body = json_decode($raw ?: '[]', true);
  if (!is_array($body)) { respond(400, ['error' => 'bad json']); }
}

// Read-modify-write under one lock.
$fh = fopen($file, 'c+');
if (!$fh || !flock($fh, LOCK_EX)) { respond(500, ['error' => 'storage']); }
$state = json_decode((string)stream_get_contents($fh), true);
if (!is_array($state)) { $state = ['seq' => 0, 'dest' => null, 'set_at' => 0, 'status' => 'idle', 'device' => ['last_seen' => 0]]; }
$now = time();
$dirty = false;
$action = $_GET['a'] ?? '';

function num(mixed $v): ?float { return (is_int($v) || is_float($v)) && is_finite((float)$v) ? (float)$v : null; }
function lanes(mixed $v): array {
  $ok = ['none', 'left', 'right', 'straight', 'slightLeft', 'slightRight'];
  $out = [];
  foreach (is_array($v) ? array_slice($v, 0, 8) : [] as $l) {
    if (!is_array($l)) { continue; }
    $d = array_values(array_filter(array_slice(is_array($l['d'] ?? null) ? $l['d'] : [], 0, 4), fn($x) => in_array($x, $ok, true)));
    $ad = in_array($l['ad'] ?? '', $ok, true) ? $l['ad'] : 'none';
    $out[] = ['d' => $d, 'a' => !empty($l['a']), 'ad' => $ad];
  }
  return $out;
}

if ($role === 'phone' && $action === 'set') {
  $lat = num($body['lat'] ?? null); $lon = num($body['lon'] ?? null);
  if ($lat === null || $lon === null || abs($lat) > 90 || abs($lon) > 180) { respond(400, ['error' => 'bad coordinates']); }
  $name = mb_substr(trim((string)($body['name'] ?? '')), 0, 160);
  $state['seq']++; $state['dest'] = ['lat' => round($lat, 6), 'lon' => round($lon, 6), 'name' => $name];
  $state['set_at'] = $now; $state['status'] = 'sent'; $state['device']['route'] = null; $state['device']['route_key'] = ''; @unlink($routeFile); $dirty = true;
} elseif ($role === 'phone' && $action === 'clear') {
  if ($state['dest'] !== null) { $state['seq']++; $state['dest'] = null; $state['status'] = 'cleared'; $state['device']['route'] = null; $state['device']['route_key'] = ''; @unlink($routeFile); $dirty = true; }
} elseif ($role === 'device' && $action === 'sync') {
  $state['device']['last_seen'] = $now;
  $route = null;
  if (num($body['remaining_m'] ?? null) !== null) {
    $route = ['remaining_m' => num($body['remaining_m']), 'remaining_s' => num($body['remaining_s'] ?? null),
              'next_m' => num($body['next_m'] ?? null), 'text' => mb_substr((string)($body['text'] ?? ''), 0, 120),
              'modifier' => mb_substr((string)($body['modifier'] ?? ''), 0, 24), 'street' => mb_substr((string)($body['street'] ?? ''), 0, 120),
              'limit_kmh' => num($body['limit_kmh'] ?? null), 'lanes' => lanes($body['lanes'] ?? null), 'at' => $now];
  }
  $state['device']['route'] = $route;
  $pos = $body['pos'] ?? null;
  if (is_array($pos) && num($pos['lat'] ?? null) !== null && num($pos['lon'] ?? null) !== null) {
    $state['device']['pos'] = ['lat' => num($pos['lat']), 'lon' => num($pos['lon']), 'bearing' => num($pos['bearing'] ?? null) ?? 0.0,
                               'speed' => max(0.0, num($pos['speed'] ?? null) ?? 0.0), 'at' => $now];
  }
  $key = mb_substr((string)($body['route_key'] ?? ''), 0, 80);
  $pts = $body['route_pts'] ?? null;
  if ($key === '') { $state['device']['route_key'] = ''; @unlink($routeFile); }
  elseif (is_array($pts) && count($pts) >= 2 && count($pts) <= 1500) {
    $clean = [];
    foreach ($pts as $p) { if (is_array($p) && count($p) === 2 && num($p[0]) !== null && num($p[1]) !== null) { $clean[] = [round((float)$p[0], 5), round((float)$p[1], 5)]; } }
    if (count($clean) >= 2) { file_put_contents($routeFile, json_encode(['key' => $key, 'pts' => $clean]), LOCK_EX); $state['device']['route_key'] = $key; }
  }
  if (!empty($body['arrived']) && $state['dest'] !== null) {
    $state['seq']++; $state['dest'] = null; $state['status'] = 'arrived';
  } elseif ($state['dest'] !== null && !empty($body['has_dest']) && ($body['seq'] ?? null) === $state['seq']) {
    $state['status'] = 'navigating';
  }
  $dirty = true;
} elseif ($role === 'phone' && $action === 'route') {
  $r = is_file($routeFile) ? json_decode((string)file_get_contents($routeFile), true) : null;
  flock($fh, LOCK_UN); fclose($fh);
  respond(200, is_array($r) ? $r : ['key' => '', 'pts' => []]);
} elseif ($role === 'phone' && $action === 'cfg') {
  flock($fh, LOCK_UN); fclose($fh);
  $tok = (string)($cfg['mapbox_token'] ?? '');
  respond(200, ['mapbox' => str_starts_with($tok, 'pk.') ? $tok : '']); // only a public token ever leaves this file
} elseif (!($role === 'phone' && $action === 'state')) {
  respond(400, ['error' => 'bad request']);
}

if ($dirty) { rewind($fh); ftruncate($fh, 0); fwrite($fh, json_encode($state)); fflush($fh); }
flock($fh, LOCK_UN); fclose($fh);

if ($role === 'device') {
  $live = $state['dest'] !== null && ($now - (int)$state['set_at']) < $ttl;
  respond(200, ['seq' => $state['seq'], 'dest' => $live ? $state['dest'] : null]);
}
$device = $state['device'];
respond(200, [
  'seq' => $state['seq'], 'dest' => $state['dest'], 'status' => $state['status'], 'set_at' => $state['set_at'],
  'expired' => $state['dest'] !== null && ($now - (int)$state['set_at']) >= $ttl,
  'device_online' => ($now - (int)($device['last_seen'] ?? 0)) < 25, 'device_seen' => (int)($device['last_seen'] ?? 0),
  'route' => $device['route'] ?? null, 'route_key' => (string)($device['route_key'] ?? ''),
  'pos' => (isset($device['pos']) && ($now - (int)$device['pos']['at']) < 30) ? $device['pos'] : null, 'now' => $now,
]);
