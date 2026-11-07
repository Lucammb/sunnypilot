"""Where the current speed goes when the onroad view is only half of the screen (the navigation takes the other half).

The speed-limit sign sits right of the set-speed box (x 60..~490 from the left edge), and the current speed is centered
in the view. In a half-width view the two meet, so there the speed moves to the right edge instead.
"""
COMPACT_BELOW_PX = 1500  # the full-width view is ~2160 px; the half with navigation ~1080
RIGHT_MARGIN_PX = 60


def speed_center_x(rect_x: float, rect_width: float, text_width: float) -> float:
  """Horizontal center of the speed number (and its unit) in a view starting at rect_x."""
  if rect_width >= COMPACT_BELOW_PX:
    return rect_x + rect_width / 2
  return rect_x + rect_width - RIGHT_MARGIN_PX - text_width / 2
