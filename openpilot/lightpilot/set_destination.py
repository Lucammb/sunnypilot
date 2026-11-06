#!/usr/bin/env python3
"""Set (or clear) the navigation destination from a shell, e.g. over SSH.

  python3 -m openpilot.lightpilot.set_destination 45.4064,9.1859
  python3 -m openpilot.lightpilot.set_destination "Via Roma 1, Milano"   # needs the Mapbox token
  python3 -m openpilot.lightpilot.set_destination --clear

NavDestination is cleared on every manager start and offroad transition, so set it after the device has booted.
"""
import argparse
import re
import sys
import urllib.parse

import requests

from openpilot.lightpilot.config import load_mapbox_token

COORDS_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$")
GEOCODE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"


def parse_coordinates(text: str) -> tuple[float, float] | None:
  """'lat,lon' -> (latitude, longitude); None if the text is not a coordinate pair; ValueError if out of range."""
  m = COORDS_RE.match(text)
  if m is None:
    return None
  lat, lon = float(m.group(1)), float(m.group(2))
  if not (-90 <= lat <= 90 and -180 <= lon <= 180):
    raise ValueError(f"coordinates out of range: {lat}, {lon}")
  return lat, lon


def geocode(query: str, token: str, country: str = "it", proximity: tuple[float, float] | None = None) -> tuple[float, float, str]:
  """Best Mapbox match for an address: (latitude, longitude, description)."""
  params = {"access_token": token, "limit": 1, "country": country, "language": "it"}
  if proximity is not None:
    params["proximity"] = f"{proximity[1]},{proximity[0]}"  # lon,lat
  resp = requests.get(GEOCODE_URL.format(query=urllib.parse.quote(query)), params=params, timeout=10)
  resp.raise_for_status()
  features = resp.json().get("features", [])
  if not features:
    raise LookupError(f"no match for '{query}'")
  lon, lat = features[0]["center"]
  return lat, lon, features[0].get("place_name", query)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("destination", nargs="?", help="'lat,lon' or an address")
  parser.add_argument("--clear", action="store_true", help="remove the destination")
  parser.add_argument("--dry-run", action="store_true", help="resolve and print, but do not set anything")
  args = parser.parse_args(argv)

  if args.clear == (args.destination is not None):
    parser.error("give a destination, or --clear")

  from openpilot.common.params import Params  # imported late: the parsing above is usable without the compiled params
  params = Params()

  if args.clear:
    params.remove("NavDestination")
    print("destination cleared")
    return 0

  try:
    coords = parse_coordinates(args.destination)
    if coords is not None:
      lat, lon, label = coords[0], coords[1], f"{coords[0]:.5f},{coords[1]:.5f}"
    else:
      token = load_mapbox_token()
      if token is None:
        print("no Mapbox token: put it in /data/lightpilot/mapbox_token (or $MAPBOX_TOKEN) to search by address", file=sys.stderr)
        return 2
      lat, lon, label = geocode(args.destination, token)
  except (ValueError, LookupError, requests.RequestException) as e:
    print(f"error: {e}", file=sys.stderr)
    return 1

  print(f"destination: {label}  ({lat:.5f}, {lon:.5f})")
  if args.dry_run:
    return 0
  params.put("NavDestination", {"latitude": lat, "longitude": lon})
  params.put_bool("NavigationEnabled", True)
  print("set. navigation is enabled; navd runs while the car is on.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
