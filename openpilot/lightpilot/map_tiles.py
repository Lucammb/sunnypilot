"""Map tiles for devices that are not registered with comma's servers (a Konik, for one).

comma's navigation fetches its basemap through a signed URL from api.comma.ai, which a device on another
backend cannot get. This is the same Protomaps basemap (OpenStreetMap data), read from the public daily
builds of Protomaps with the same byte range requests and disk cache, so the rest of the map code is unchanged.
"""
import time
from pathlib import Path

import requests

from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths
from openpilot.selfdrive.navigation.pmtiles import HEADER_LENGTH
from openpilot.selfdrive.navigation.tiles import RemoteTiles

BUILDS_URL = 'https://build-metadata.protomaps.dev/builds.json'
BUILD_URL = 'https://build.protomaps.com/{key}'
CACHE_DIR = '/data/lightpilot/tiles'


def latest_build_url(session: requests.Session) -> str:
  """URL of the newest daily planet build."""
  response = session.get(BUILDS_URL, timeout=10)
  response.raise_for_status()
  builds = response.json()
  key = max(b['key'] for b in builds if str(b.get('key', '')).endswith('.pmtiles'))
  return BUILD_URL.format(key=key)


class ProtomapsTiles(RemoteTiles):
  """RemoteTiles with a public URL instead of comma's: the parent's reads, prefetch and cache are reused."""

  def __init__(self):
    self.cache = Path(Paths.comma_home()) / 'lightpilot_tiles' if PC else Path(CACHE_DIR)
    self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.session = requests.Session()
    self.api = None
    self.etag = None
    self.size = 0
    self.url = latest_build_url(self.session)
    self.expires = time.time() + 10 * 365 * 24 * 3600  # a public URL does not expire
    version = self.cache / 'version'
    try:
      header = self._fetch(0, HEADER_LENGTH)  # learns the archive's ETag and size
      if not version.exists() or version.read_text() != f'{self.etag} {self.size}':
        for cached in self.cache.iterdir():  # a new build has new offsets
          cached.unlink()
        version.write_text(f'{self.etag} {self.size}')
      self._store(0, header)
    except Exception:
      self.close()
      raise
