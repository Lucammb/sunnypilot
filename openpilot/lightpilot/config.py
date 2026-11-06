"""Fork configuration that lives outside the params store (no compiled-key changes needed)."""
import os
from pathlib import Path

LIGHTPILOT_DIR = Path(os.environ.get("LIGHTPILOT_DIR", "/data/lightpilot"))
MAPBOX_TOKEN_FILE = LIGHTPILOT_DIR / "mapbox_token"


def load_mapbox_token(path: Path | None = None) -> str | None:
  """Mapbox token from $MAPBOX_TOKEN, else the first non-empty line of the token file; None if neither is set."""
  token = os.environ.get("MAPBOX_TOKEN", "").strip()
  if token:
    return token
  try:
    for line in (path or MAPBOX_TOKEN_FILE).read_text().splitlines():
      line = line.strip()
      if line and not line.startswith("#"):
        return line
  except OSError:
    pass
  return None
