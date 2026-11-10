"""Layout of the onroad HUD when the view is only half of the screen (the navigation takes the other half).

Full width: set-speed box, then the speed-limit sign to its right, the current speed centered in the view.
Half width: the current speed stays centered; the speed-limit sign goes under the set-speed box, on the left, so that
neither can run into the other.
"""
COMPACT_BELOW_PX = 1500  # the full-width view is ~2160 px; the half with navigation ~1080
LEFT_MARGIN_PX = 60  # where the set-speed box starts
SIGN_GAP_PX = 24  # between the set-speed box and the sign under it


def is_compact(rect_width: float) -> bool:
  return rect_width < COMPACT_BELOW_PX


def speed_center_x(rect_x: float, rect_width: float, text_width: float = 0.0) -> float:
  """Horizontal center of the speed number (and its unit) in a view starting at rect_x: always the middle."""
  return rect_x + rect_width / 2


def speed_limit_sign_origin(rect_x: float, rect_y: float, rect_width: float, box_width: float, box_height: float) -> tuple[float, float]:
  """Top-left of the speed-limit sign, given the set-speed box's width and height."""
  if is_compact(rect_width):
    return rect_x + LEFT_MARGIN_PX - 6, rect_y + 45 + box_height + SIGN_GAP_PX
  return rect_x + LEFT_MARGIN_PX + box_width + 30 - 6, rect_y + 45 - 6
