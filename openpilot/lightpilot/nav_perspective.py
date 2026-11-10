"""Tilted ("3D") view of the map frame mapsd publishes.

The frame is a top-down, heading-up picture at 1 m per pixel with the ego at EGO. A ground plane seen by a pitched
camera turns every horizontal line of the frame into a horizontal line on the screen, only scaled, so the tilted view
is a stack of thin horizontal strips: each strip shows a band of the frame, as wide as the camera sees at that
distance, stretched over the full width of the view. No 3D engine needed; ~100 textured rectangles per frame.
"""
import math
from dataclasses import dataclass
from functools import lru_cache

SIZE = 256
EGO = (128.0, 192.0)  # the ego's pixel in the frame (selfdrive/navigation/renderer.py)
EGO_ROW = 0.80  # the ego's vertical position in the view, from the top
PIXELS_PER_METER = 5.0  # at the ego's row, in screen pixels
PITCH_DEG = 52.0  # camera pitch below the horizon
STRIP_PX = 6


@dataclass(frozen=True)
class Strip:
  dx: float  # destination, relative to the view
  dy: float
  dw: float
  dh: float
  sx: float  # source, in frame pixels
  sy: float
  sw: float
  sh: float


@lru_cache(maxsize=8)
def perspective_strips(width: int, height: int, ego_row: float = EGO_ROW, ppm: float = PIXELS_PER_METER,
                       pitch_deg: float = PITCH_DEG, strip_px: int = STRIP_PX) -> tuple[Strip, ...]:
  pitch = math.radians(pitch_deg)
  sin, cos = math.sin(pitch), math.cos(pitch)
  focal = width * 1.1
  cy = height / 2  # principal point
  ye = height * ego_row - cy
  camera_height = (focal * sin + ye * cos) / ppm  # so the ego's row has ppm pixels per meter

  def depth(ys):
    return focal * sin + ys * cos

  def ground_z(ys):  # forward distance from the camera's foot to the ground at screen offset ys (down is positive)
    return camera_height * (focal * cos - ys * sin) / depth(ys)

  ze = ground_z(ye)
  strips = []
  y = 0
  while y < height:
    y2 = min(y + strip_px, height)
    ys_top, ys_bottom = y - cy, y2 - cy
    if depth(ys_top) > 1e-6:
      src_top = EGO[1] - (ground_z(ys_top) - ze)
      src_bottom = EGO[1] - (ground_z(ys_bottom) - ze)
      if src_top >= 0 and src_bottom <= SIZE:
        half = camera_height / depth((ys_top + ys_bottom) / 2) * width / 2  # meters seen to each side
        x0, x1 = EGO[0] - half, EGO[0] + half
        c0, c1 = max(0.0, x0), min(float(SIZE), x1)
        if c1 > c0:
          strips.append(Strip((c0 - x0) / (x1 - x0) * width, y, (c1 - c0) / (x1 - x0) * width, y2 - y, c0, src_top, c1 - c0, src_bottom - src_top))
    y = y2
  return tuple(strips)


def ego_position(width: int, height: int, ego_row: float = EGO_ROW) -> tuple[float, float]:
  """Where the ego is in the view."""
  return width / 2, height * ego_row
