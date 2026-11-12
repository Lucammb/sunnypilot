"""lightpilot: the stock / experimental button of the road view.

Without a navigation route it is the round button at the top right: a tap switches between stock and experimental
driving. With a route the choice is automatic, and the button becomes a smaller badge under the next-maneuver card
that lights up while experimental driving is active. See lightpilot/drive_mode.py.
"""
import pyray as rl

from openpilot.lightpilot.drive_mode import EXPERIMENTAL, read_choice, read_status, toggle_choice
from openpilot.lightpilot.hud_layout import DRIVE_MODE_BUTTON_PX, drive_mode_button, in_drive_mode_button
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

REFRESH_S = 0.5
BACKGROUND = rl.Color(0, 0, 0, 166)
STOCK_COLOR = rl.Color(255, 255, 255, 235)
EXPERIMENTAL_COLOR = rl.Color(255, 155, 63, 255)
AUTO_COLOR = rl.Color(40, 200, 95, 255)
ICON_FRACTION = 0.72  # the wheel's size in the button


class DriveModeButton(Widget):
  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.BOLD)
    self._wheel = gui_app.texture('icons/chffr_wheel.png', int(DRIVE_MODE_BUTTON_PX * ICON_FRACTION), int(DRIVE_MODE_BUTTON_PX * ICON_FRACTION))
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
    color = AUTO_COLOR if auto else (EXPERIMENTAL_COLOR if active else STOCK_COLOR)
    center = rl.Vector2(x + size / 2, y + size / 2)
    rl.draw_circle_v(center, size / 2, BACKGROUND)
    if auto and active:  # automatic, and the model's plan is the one driving right now
      rl.draw_ring(center, size / 2 - 5, size / 2, 0, 360, 48, EXPERIMENTAL_COLOR)

    # the stock openpilot wheel; the icon is white, so the tint is the color
    icon = size * ICON_FRACTION
    src = rl.Rectangle(0, 0, self._wheel.width, self._wheel.height)
    dst = rl.Rectangle(center.x - icon / 2, center.y - icon / 2, icon, icon)
    rl.draw_texture_pro(self._wheel, src, dst, rl.Vector2(0, 0), 0.0, color)

    # automatic: say which one is driving right now, so that nobody has to guess it from the camera view (the wide camera
    # at low speed is about speed, not about the mode)
    text = ('AUTO EXP' if active else 'AUTO STOCK') if auto else ('EXP' if active else 'STOCK')
    label_color = EXPERIMENTAL_COLOR if auto and active else color
    font_size = int(size * (0.17 if auto else 0.2))
    width = measure_text_cached(self._font, text, font_size).x
    rl.draw_text_ex(self._font, text, rl.Vector2(center.x - width / 2, y + size + 4), font_size, 0, label_color)
