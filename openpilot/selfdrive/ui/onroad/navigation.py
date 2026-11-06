"""Navigation panel for the full-size onroad screen: the map frames mapsd renders, with the next maneuver on top.

Adapted from the mici panel (selfdrive/ui/mici/onroad/navigation.py) to take any width, so it can fill half the screen.
"""
import numpy as np
import pyray as rl

from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.constants import CV
from openpilot.selfdrive.navigation.helpers import latest_gps_source
from openpilot.selfdrive.navigation.renderer import EGO, ROUTE_INTENSITY, SIZE
from openpilot.selfdrive.ui.onroad.cameraview import CONNECTION_RETRY_INTERVAL
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, TextAlignmentVertical
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel, gui_label

STALE_SECONDS = 3
MARKER_MARGIN = 40  # source pixels kept below the ego so its marker stays visible
HEADER_HEIGHT = 190
FOOTER_HEIGHT = 80

# Dim the white roads and make the route's grey pop; only the route's exact level (and its neighbors) is recolored.
MAP_COLORS = np.repeat(np.round(np.arange(256) * 0.45).astype(np.uint8)[:, None], 3, axis=1)
MAP_COLORS[ROUTE_INTENSITY - 1:ROUTE_INTENSITY + 2] = (72, 235, 132)


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


class NavigationPanel(Widget):
  """Turn instructions around the map frames mapsd publishes for the model."""

  def __init__(self):
    super().__init__()
    self._client = VisionIpcClient('mapsd', VisionStreamType.VISION_STREAM_MAP, True)
    self._last_connect = 0.0
    self._frame_time = 0.0
    self._texture = None
    self._primary = UnifiedLabel('', 40, FontWeight.BOLD, rl.WHITE, scroll=True)
    self._secondary = UnifiedLabel('', 32, FontWeight.MEDIUM, rl.Color(155, 230, 185, 255), scroll=True)
    self._eta_label = UnifiedLabel('', 44, FontWeight.BOLD, rl.WHITE, scroll=True)
    self._accent = rl.Color(155, 230, 185, 255)
    self._muted = rl.Color(162, 175, 185, 255)

  def _update_state(self):
    if not self._client.is_connected():
      if rl.get_time() - self._last_connect < CONNECTION_RETRY_INTERVAL:
        return
      self._last_connect = rl.get_time()
      if not self._client.connect(False) or not self._client.num_buffers:
        return
      self._unload_texture()
      self._texture = rl.load_texture_from_image(rl.Image(None, SIZE, SIZE, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8))
      rl.set_texture_filter(self._texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)  # the frame is magnified
    frame = self._client.recv(timeout_ms=0)
    if frame is not None:
      gray = np.frombuffer(frame.data, dtype=np.uint8, count=SIZE * SIZE)
      rl.update_texture(self._texture, rl.ffi.cast("void *", rl.ffi.from_buffer(MAP_COLORS[gray])))
      # Without a usable map, mapsd publishes black frames with a zero timestamp.
      self._frame_time = rl.get_time() if self._client.timestamp_sof else 0.0

  @property
  def routing(self):
    return ui_state.sm.valid['navInstruction'] and ui_state.sm.alive['navInstruction']

  @property
  def map_ready(self):
    return self._texture is not None and 0 <= rl.get_time() - self._frame_time < STALE_SECONDS

  def _draw_status(self, rect):
    """What the map is waiting on, in place of the map."""
    statuses = (
      ('GPS fix', latest_gps_source(ui_state.sm) is not None),
      ('Route', self.routing),
      ('Map', self.map_ready),
    )
    for i, (name, ok) in enumerate(statuses):
      gui_label(rl.Rectangle(rect.x + 24, rect.y + 24 + i * 56, rect.width - 48, 50), f'{name}: {"yes" if ok else "no"}', 40,
                self._accent if ok else self._muted, FontWeight.MEDIUM, alignment_vertical=TextAlignmentVertical.TOP)

  def _draw_arrow(self, x: float, y: float, direction: str, scale: float):
    points = [rl.Vector2(x + scale * px, y + scale * py) for px, py in arrow_shape(direction)]
    for a, b in zip(points[:-1], points[1:], strict=True):
      rl.draw_line_ex(a, b, scale * 5, self._accent)
    for point in points:
      rl.draw_circle_v(point, scale * 2.5, self._accent)

  def _render(self, rect):
    x, y = rect.x, rect.y
    rl.draw_rectangle_rec(rect, rl.Color(7, 13, 18, 255))
    instruction = ui_state.sm['navInstruction']
    if self.routing:
      self._draw_arrow(x + 90, y + HEADER_HEIGHT / 2, instruction.maneuverModifier, 3.4)
      gui_label(rl.Rectangle(x + 180, y + 14, rect.width - 190, 80), format_distance(max(0, instruction.maneuverDistance)), 72, rl.WHITE,
                FontWeight.BOLD, alignment_vertical=TextAlignmentVertical.TOP)
      self._primary.set_text(instruction.maneuverPrimaryText)
      self._primary.render(rl.Rectangle(x + 180, y + 98, rect.width - 196, 48))
      self._secondary.set_text(instruction.maneuverSecondaryText)
      self._secondary.render(rl.Rectangle(x + 180, y + 144, rect.width - 196, 40))
    else:
      gui_label(rl.Rectangle(x + 24, y + 56, rect.width - 48, 70), 'Waiting for route', 56, rl.WHITE, FontWeight.BOLD,
                alignment_vertical=TextAlignmentVertical.TOP)

    map_rect = rl.Rectangle(x + 8, y + HEADER_HEIGHT, rect.width - 16, rect.height - HEADER_HEIGHT - FOOTER_HEIGHT)
    rl.draw_rectangle_rec(map_rect, rl.BLACK)
    if self.map_ready:
      # The model's frame magnified to fill the map area, with the ego near the bottom.
      scale = max(map_rect.width, map_rect.height) / SIZE
      width, height = min(SIZE, map_rect.width / scale), min(SIZE, map_rect.height / scale)
      source = rl.Rectangle((SIZE - width) / 2, min(SIZE - height, max(0, EGO[1] + MARKER_MARGIN - height)), width, height)
      rl.draw_texture_pro(self._texture, source, map_rect, rl.Vector2(0, 0), 0, rl.WHITE)
    else:
      self._draw_status(map_rect)

    if self.routing:
      seconds = max(0, instruction.timeRemaining)
      remaining = max(0, instruction.distanceRemaining)
      self._eta_label.set_text(f'{max(1, round(seconds / 60))} min / {format_distance(remaining)}')
      self._eta_label.render(rl.Rectangle(x + 20, y + rect.height - FOOTER_HEIGHT + 14, rect.width - 40, 56))

  def _unload_texture(self):
    if self._texture:
      rl.unload_texture(self._texture)
      self._texture = None

  def close(self):
    self._unload_texture()
