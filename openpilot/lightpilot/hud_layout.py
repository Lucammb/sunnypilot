"""Layout of the onroad HUD when the view is only half of the screen (the navigation takes the other half).

Full width: set-speed box, then the speed-limit sign to its right, the current speed centered in the view.
Half width: the current speed stays centered; the speed-limit sign is smaller and sits in the top right corner, so that
nothing can run into anything else.
"""
COMPACT_BELOW_PX = 1500  # the full-width view is ~2160 px; the half with navigation ~1080
LEFT_MARGIN_PX = 60  # where the set-speed box starts
RIGHT_MARGIN_PX = 40  # from the right edge of the view to the sign
COMPACT_SIGN_SCALE = 0.7  # size of the sign in the half view


def is_compact(rect_width: float) -> bool:
  return rect_width < COMPACT_BELOW_PX


def speed_center_x(rect_x: float, rect_width: float, text_width: float = 0.0) -> float:
  """Horizontal center of the speed number (and its unit) in a view starting at rect_x: always the middle."""
  return rect_x + rect_width / 2


def speed_limit_sign_scale(rect_width: float) -> float:
  return COMPACT_SIGN_SCALE if is_compact(rect_width) else 1.0


def speed_limit_sign_origin(rect_x: float, rect_y: float, rect_width: float, box_width: float, box_height: float) -> tuple[float, float]:
  """Top-left of the speed-limit sign (before scaling), given the set-speed box's width and height.

  The sign is drawn 9 px larger than its rectangle on every side (the Vienna round sign), scaled with it."""
  scale = speed_limit_sign_scale(rect_width)
  if is_compact(rect_width):
    return rect_x + rect_width - RIGHT_MARGIN_PX - 9 * scale - box_width * scale, rect_y + 45 - 6
  return rect_x + LEFT_MARGIN_PX + box_width + 30 - 6, rect_y + 45 - 6
