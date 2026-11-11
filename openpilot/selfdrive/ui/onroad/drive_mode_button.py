"""lightpilot: the stock / experimental button of the road view.

Without a navigation route it is the round button at the top right: a tap switches between stock and experimental
driving. With a route the choice is automatic, and the button becomes a smaller badge under the next-maneuver card
that lights up while experimental driving is active. See lightpilot/drive_mode.py.
"""
import pyray as rl

from openpilot.lightpilot.drive_mode import EXPERIMENTAL, read_choice, read_status, toggle_choice
from openpilot.lightpilot.hud_layout import drive_mode_button, in_drive_mode_button
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

REFRESH_S = 0.5
BACKGROUND = rl.Color(0, 0, 0, 166)
STOCK_COLOR = rl.Color(255, 255, 255, 235)
EXPERIMENTAL_COLOR = rl.Color(255, 155, 63, 255)
IDLE_COLOR = rl.Color(166, 166, 166, 255)


class DriveModeButton(Widget):
  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.BOLD)
    self._choice = read_choice()
    self._status: dict | None = None
    self._refresh_time = 0.0
    self._navigation = False
    self._button: tuple[float, float, float] | None = None

  def set_navigation(self, navigation: bool) -> None:
    self._navigation = navigation

  @property
  def automatic(self) -> bool:
    return bool(self._status and self._status['auto'])

  def _update_state(self) -> None:
    now = rl.get_time()
    if now - self._refresh_time >= REFRESH_S:
      self._refresh_time = now
      self._status = read_status()
      self._choice = read_choice()

  def press(self, mouse_pos) -> bool:
    """Handle a touch: True if it was on the button (the road view then does nothing else with it)."""
    if self._button is None or not in_drive_mode_button(mouse_pos.x, mouse_pos.y, self._button):
      return False
    if not self.automatic:
      self._choice = toggle_choice()
    return True

  def _render(self, rect: rl.Rectangle) -> None:
    self._button = None
    if not ui_state.has_longitudinal_control:  # the mode only matters when openpilot controls the speed
      return
    auto = self.automatic
    active = self._status['e2e'] if auto and self._status else self._choice == EXPERIMENTAL
    self._button = x, y, size = drive_mode_button(rect.x, rect.y, rect.width, self._navigation)
    scale = size / 150
    color = EXPERIMENTAL_COLOR if active else (IDLE_COLOR if auto else STOCK_COLOR)
    center = rl.Vector2(x + size / 2, y + size / 2)
    rl.draw_circle_v(center, size / 2, BACKGROUND)
    rl.draw_ring(center, size / 2 - 6 * scale, size / 2, 0, 360, 48, color if active else rl.Color(255, 255, 255, 75))

    # steering wheel
    wheel = rl.Vector2(center.x, center.y - 16 * scale)
    outer, rim = 40 * scale, 8 * scale
    rl.draw_ring(wheel, outer - rim, outer, 0, 360, 48, color)
    rl.draw_circle_v(wheel, 9 * scale, color)
    for dx, dy in ((-1, 0), (1, 0), (0, 1)):
      rl.draw_line_ex(wheel, rl.Vector2(wheel.x + dx * (outer - rim / 2), wheel.y + dy * (outer - rim / 2)), 7 * scale, color)

    text = 'AUTO' if auto else ('EXP' if active else 'STOCK')
    font_size = int(30 * scale)
    width = measure_text_cached(self._font, text, font_size).x
    rl.draw_text_ex(self._font, text, rl.Vector2(center.x - width / 2, center.y + 30 * scale), font_size, 0, color)
