<?php
// Copy to config.php (same folder) and put two long random strings: openssl rand -hex 24
return [
  'phone_token'  => 'CHANGE_ME_phone',   // used by the phone app
  'device_token' => 'CHANGE_ME_device',  // used by the Konik
  'ttl_hours'    => 12,                  // a destination older than this is not armed again after a boot
];
