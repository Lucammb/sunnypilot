"""Navigation overlay for the full-size onroad screen: the next maneuver at the top right and a bar at the bottom with
the arrival time, the minutes and the distance left. The full map is shown on the phone (lightpilot/phone).
"""
import datetime

import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel, gui_label

CARD_COLOR = rl.Color(14, 18, 22, 215)
PILL_COLOR = rl.Color(14, 18, 22, 215)
MINUTES_COLOR = rl.Color(72, 235, 132, 255)
CARD_WIDTH = 520
CARD_HEIGHT = 150
CARD_MARGIN = 28
PILL_WIDTH = 760
PILL_HEIGHT = 84
PILL_BOTTOM_MARGIN = 30
LANE_MODIFIERS = {'slightLeft': 'slight left', 'slightRight': 'slight right'}  # capnp lane direction -> Mapbox modifier


def format_distance(meters: float) -> str:
  if ui_state.is_metric:
    return f'{round(meters / 10) * 10} m' if meters < 1000 else f'{meters / 1000:.1f} km'
  return f'{round(meters * CV.METER_TO_FOOT / 50) * 50} ft' if meters < 300 else f'{meters * CV.METER_TO_MILE:.1f} mi'


def arrow_shape(direction: str) -> list[tuple[float, float]]:
  """Maneuver arrow as a polyline around (0, 0), y down, for a Mapbox maneuver modifier."""
  flip = -1 if 'left' in direction else 1
  if direction in ('straight', ''):
    return [(0, 14), (0, -12), (-6, -5), (0, -12), (6, -5)]
  if 'slight' in direction:
    return [(-flip * 6, 14), (-flip * 6, 2), (flip * 5, -10), (flip * 5, -2), (flip * 5, -10), (-flip * 1, -10)]
  if direction == 'uturn':
    return [(7, 14), (7, -6), (4, -12), (-4, -12), (-7, -6), (-7, 2), (-11, -3), (-7, 2), (-3, -3)]
  return [(-flip * 9, 14), (-flip * 9, -4), (-flip * 2, -9), (flip * 9, -9), (flip * 3, -15), (flip * 9, -9), (flip * 3, -3)]


def arrival_time(seconds: float) -> str:
  """Clock time of the arrival, local time, 24 h."""
  return (datetime.datetime.now() + datetime.timedelta(seconds=max(0, seconds))).strftime('%H:%M')


def lane_arrow_key(direction: str) -> str:
  """arrow_shape() key of a lane direction from navInstruction ('none' is drawn straight)."""
  return LANE_MODIFIERS.get(direction, direction if direction != 'none' else 'straight')


class NavigationPanel(Widget):
  """Next maneuver (top right) and trip bar (bottom) over the road view."""

  def __init__(self):
    super().__init__()
    self._primary = UnifiedLabel('', 34, FontWeight.MEDIUM, rl.Color(200, 210, 218, 255), scroll=True)

  @property
  def routing(self):
    return ui_state.sm.valid['navInstruction'] and ui_state.sm.alive['navInstruction']

  def _draw_arrow(self, x: float, y: float, direction: str, scale: float, color=rl.WHITE):
    points = [rl.Vector2(x + scale * px, y + scale * py) for px, py in arrow_shape(direction)]
    for a, b in zip(points[:-1], points[1:], strict=True):
      rl.draw_line_ex(a, b, scale * 5, color)
    for point in points:
      rl.draw_circle_v(point, scale * 2.5, color)

  def _draw_maneuver(self, instruction, rect):
    card = rl.Rectangle(rect.x + rect.width - CARD_WIDTH - CARD_MARGIN, rect.y + CARD_MARGIN, CARD_WIDTH, CARD_HEIGHT)
    rl.draw_rectangle_rounded(card, 0.25, 12, CARD_COLOR)
    self._draw_arrow(card.x + 70, card.y + CARD_HEIGHT / 2, instruction.maneuverModifier, 3.0)
    gui_label(rl.Rectangle(card.x + 140, card.y + 14, CARD_WIDTH - 156, 70), format_distance(max(0, instruction.maneuverDistance)), 64, rl.WHITE,
              FontWeight.BOLD, alignment_vertical=TextAlignmentVertical.TOP)
    self._primary.set_text(instruction.maneuverPrimaryText)
    self._primary.render(rl.Rectangle(card.x + 140, card.y + 92, CARD_WIDTH - 156, 44))

  def _draw_footer(self, instruction, rect):
    """Pill with the arrival time, the minutes left (green) and the distance left."""
    width = min(PILL_WIDTH, rect.width - 2 * CARD_MARGIN)
    pill = rl.Rectangle(rect.x + (rect.width - width) / 2, rect.y + rect.height - PILL_HEIGHT - PILL_BOTTOM_MARGIN, width, PILL_HEIGHT)
    rl.draw_rectangle_rounded(pill, 0.5, 12, PILL_COLOR)
    minutes = max(1, round(max(0, instruction.timeRemaining) / 60))
    cells = (
      (arrival_time(instruction.timeRemaining), rl.WHITE),
      (f'{minutes} min', MINUTES_COLOR),
      (format_distance(max(0, instruction.distanceRemaining)), rl.WHITE),
    )
    cell_width = pill.width / len(cells)
    for i, (text, color) in enumerate(cells):
      gui_label(rl.Rectangle(pill.x + i * cell_width, pill.y, cell_width, pill.height), text, 44, color, FontWeight.BOLD,
                alignment=TextAlignment.CENTER)

  def _render(self, rect):
    if not self.routing:
      gui_label(rl.Rectangle(rect.x + rect.width - CARD_WIDTH - CARD_MARGIN, rect.y + CARD_MARGIN, CARD_WIDTH, 60), 'Waiting for route', 40,
                rl.Color(200, 210, 218, 255), FontWeight.MEDIUM, alignment=TextAlignment.RIGHT, alignment_vertical=TextAlignmentVertical.TOP)
      return
    instruction = ui_state.sm['navInstruction']
    self._draw_maneuver(instruction, rect)
    self._draw_footer(instruction, rect)
