"""Navigation panel for the full-size onroad screen: the map frames mapsd renders, with the next maneuver on top.

Adapted from the mici panel (selfdrive/ui/mici/onroad/navigation.py) to take any width, so it can fill half the screen.
"""
import datetime

import numpy as np
import pyray as rl

from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.constants import CV
from openpilot.selfdrive.navigation.helpers import latest_gps_source
from openpilot.lightpilot.nav_perspective import PIXELS_PER_METER, ego_position, perspective_strips
from openpilot.selfdrive.navigation.renderer import ROUTE_INTENSITY, SIZE
from openpilot.selfdrive.ui.onroad.cameraview import CONNECTION_RETRY_INTERVAL
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel, gui_label

STALE_SECONDS = 3
BACKGROUND_RGB = (10, 13, 16)
MAP_BACKGROUND = rl.Color(*BACKGROUND_RGB, 255)
BANNER_TOP = rl.Color(8, 11, 14, 245)  # the banner fades into the map
BANNER_BOTTOM = rl.Color(8, 11, 14, 150)
HEADER_HEIGHT = 190
LANES_HEIGHT = 84  # extra header height when the map has lane guidance for the next maneuver
FOOTER_HEIGHT = 96
LANE_COLOR_ACTIVE = rl.Color(255, 255, 255, 255)
LANE_COLOR_OTHER = rl.Color(120, 128, 134, 255)
PILL_COLOR = rl.Color(14, 18, 22, 235)
MINUTES_COLOR = rl.Color(72, 235, 132, 255)
EGO_HALO = rl.Color(48, 104, 186, 230)
LANE_MODIFIERS = {'slightLeft': 'slight left', 'slightRight': 'slight right'}  # capnp lane direction -> Mapbox modifier

# Frame gray level -> color. Black (nothing) becomes the map's own background, so the tilted view has no visible edge;
# roads (white) become a light slate grey, their antialiased edges blend toward it; the route's exact level is green.
ROAD_COLOR = (136, 144, 152)
ROUTE_COLOR = (72, 235, 132)
_BLEND = (np.arange(256) / 255.0)[:, None]
MAP_COLORS = np.round(np.array(BACKGROUND_RGB) * (1 - _BLEND) + np.array(ROAD_COLOR) * _BLEND).astype(np.uint8)
MAP_COLORS[ROUTE_INTENSITY - 1:ROUTE_INTENSITY + 2] = ROUTE_COLOR
HORIZON_FADE = 0.45  # the far part of the map fades into the background, from the top of the panel down to this fraction


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
  """Turn instructions around the map frames mapsd publishes for the model."""

  def __init__(self):
    super().__init__()
    self._client = VisionIpcClient('mapsd', VisionStreamType.VISION_STREAM_MAP, True)
    self._last_connect = 0.0
    self._frame_time = 0.0
    self._texture = None
    self._primary = UnifiedLabel('', 40, FontWeight.BOLD, rl.WHITE, scroll=True)
    self._secondary = UnifiedLabel('', 32, FontWeight.MEDIUM, rl.Color(155, 230, 185, 255), scroll=True)
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

  def _draw_arrow(self, x: float, y: float, direction: str, scale: float, color=None):
    color = color or self._accent
    points = [rl.Vector2(x + scale * px, y + scale * py) for px, py in arrow_shape(direction)]
    for a, b in zip(points[:-1], points[1:], strict=True):
      rl.draw_line_ex(a, b, scale * 5, color)
    for point in points:
      rl.draw_circle_v(point, scale * 2.5, color)

  def _draw_lanes(self, instruction, x: float, y: float, width: float):
    """One arrow per lane, the lanes to take bright and the others grey, centered in the row at y."""
    lanes = list(instruction.lanes)
    step = min(110.0, (width - 40) / max(1, len(lanes)))
    left = x + width / 2 - step * (len(lanes) - 1) / 2
    for i, lane in enumerate(lanes):
      directions = [str(d) for d in lane.directions]
      shown = str(lane.activeDirection) if lane.active and str(lane.activeDirection) != 'none' else (directions[0] if directions else 'none')
      self._draw_arrow(left + i * step, y, lane_arrow_key(shown), 2.2, LANE_COLOR_ACTIVE if lane.active else LANE_COLOR_OTHER)

  def _draw_footer(self, instruction, rect):
    """Pill with the arrival time, the minutes left (green) and the distance left."""
    pill = rl.Rectangle(rect.x + 16, rect.y + rect.height - FOOTER_HEIGHT + 10, rect.width - 32, FOOTER_HEIGHT - 20)
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

  def _draw_map(self, rect):
    """The map frame as a tilted view filling the panel, other roads grey and the route green, fading out toward the
    horizon, with the ego's arrow on a soft halo."""
    rl.draw_rectangle_rec(rect, MAP_BACKGROUND)
    strips = perspective_strips(int(rect.width), int(rect.height))
    for strip in strips:
      rl.draw_texture_pro(self._texture, rl.Rectangle(strip.sx, strip.sy, strip.sw, strip.sh),
                          rl.Rectangle(rect.x + strip.dx, rect.y + strip.dy, strip.dw, strip.dh), rl.Vector2(0, 0), 0, rl.WHITE)
    clear = rl.Color(*BACKGROUND_RGB, 0)
    rl.draw_rectangle_gradient_v(int(rect.x), int(rect.y), int(rect.width), int(rect.height * HORIZON_FADE), MAP_BACKGROUND, clear)
    ex, ey = ego_position(int(rect.width), int(rect.height))
    ex, ey = rect.x + ex, rect.y + ey
    scale = PIXELS_PER_METER * 0.9
    rl.draw_ellipse(int(ex), int(ey), scale * 9, scale * 7, EGO_HALO)
    tip, left, notch, right = (rl.Vector2(ex + scale * px, ey + scale * py * 0.75) for px, py in ((0, -9), (-7, 7), (0, 3), (7, 7)))
    rl.draw_triangle(tip, left, notch, rl.WHITE)
    rl.draw_triangle(tip, notch, right, rl.WHITE)

  def _render(self, rect):
    x, y = rect.x, rect.y
    instruction = ui_state.sm['navInstruction']
    has_lanes = self.routing and len(instruction.lanes) > 0
    header = HEADER_HEIGHT + (LANES_HEIGHT if has_lanes else 0)

    # The map fills the panel; the banner and the footer float over it.
    if self.map_ready:
      self._draw_map(rect)
    else:
      rl.draw_rectangle_rec(rect, MAP_BACKGROUND)
      self._draw_status(rl.Rectangle(x, y + header, rect.width, rect.height - header))

    if self.routing:
      rl.draw_rectangle_gradient_v(int(x), int(y), int(rect.width), int(header), BANNER_TOP, BANNER_BOTTOM)
      self._draw_arrow(x + 90, y + HEADER_HEIGHT / 2, instruction.maneuverModifier, 3.4, rl.WHITE)
      gui_label(rl.Rectangle(x + 180, y + 14, rect.width - 190, 80), format_distance(max(0, instruction.maneuverDistance)), 72, rl.WHITE,
                FontWeight.BOLD, alignment_vertical=TextAlignmentVertical.TOP)
      self._primary.set_text(instruction.maneuverPrimaryText)
      self._primary.render(rl.Rectangle(x + 180, y + 98, rect.width - 196, 48))
      self._secondary.set_text(instruction.maneuverSecondaryText)
      self._secondary.render(rl.Rectangle(x + 180, y + 144, rect.width - 196, 40))
      if has_lanes:
        self._draw_lanes(instruction, x, y + HEADER_HEIGHT + LANES_HEIGHT / 2 - 8, rect.width)
      self._draw_footer(instruction, rect)
    else:
      gui_label(rl.Rectangle(x + 24, y + 56, rect.width - 48, 70), 'Waiting for route', 56, rl.WHITE, FontWeight.BOLD,
                alignment_vertical=TextAlignmentVertical.TOP)

  def _unload_texture(self):
    if self._texture:
      rl.unload_texture(self._texture)
      self._texture = None

  def close(self):
    self._unload_texture()
