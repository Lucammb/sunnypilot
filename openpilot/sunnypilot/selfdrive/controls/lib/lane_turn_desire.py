"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom

from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.lightpilot.nav_turn import NavTurn, LEFT, RIGHT

TurnDirection = custom.ModelDataV2SP.TurnDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS


class LaneTurnController:
  def __init__(self, desire_helper):
    self.DH = desire_helper
    self.turn_direction = TurnDirection.none
    self.params = Params()
    self.lane_turn_value = float(self.params.get("LaneTurnValue", return_default=True)) * CV.MPH_TO_MS
    self.param_read_counter = 0
    self.enabled = self.params.get_bool("LaneTurnDesire")
    self.nav_turn = NavTurn()  # lightpilot: turn from the navigation route (roundabout exits), see lightpilot/nav_turn.py
    self.from_route = False  # lightpilot: the turn direction comes from the route, not from the blinker

  def read_params(self):
    self.enabled = self.params.get_bool("LaneTurnDesire")
    value = float(self.params.get("LaneTurnValue", return_default=True)) * CV.MPH_TO_MS
    self.lane_turn_value = min(float(LANE_CHANGE_SPEED_MIN), value)

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def update_lane_turn(self, blindspot_left: bool, blindspot_right: bool, left_blinker: bool, right_blinker: bool, v_ego: float) -> None:
    # lightpilot: the route is read at any speed (so that turn.log can say why a turn was not applied); it only acts below the lane-turn speed
    # lightpilot: the route turn does not depend on the Lane Turn Desire setting; the blinker turns below still do
    nav_side = self.nav_turn.update(v_ego)
    nav_limit = max(self.lane_turn_value, self.nav_turn.max_speed)  # the route turn also works if the driver accelerates by hand
    reason = 'none' if nav_side is None else 'ok'
    self.from_route = False
    if self.enabled and left_blinker and not right_blinker and v_ego < self.lane_turn_value and not blindspot_left:
      self.turn_direction = TurnDirection.turnLeft
      reason = 'driver blinker left'
    elif self.enabled and right_blinker and not left_blinker and v_ego < self.lane_turn_value and not blindspot_right:
      self.turn_direction = TurnDirection.turnRight
      reason = 'driver blinker right'
    elif v_ego < nav_limit and ((not left_blinker and not right_blinker) or (right_blinker and not left_blinker and nav_side == RIGHT) or
                                (left_blinker and not right_blinker and nav_side == LEFT)):
      # lightpilot: no blinker, or the blinker on the route's own side: the route decides (a blinker the other way always wins)
      if nav_side == LEFT and not blindspot_left:
        self.turn_direction, self.from_route = TurnDirection.turnLeft, True
      elif nav_side == RIGHT and not blindspot_right:
        self.turn_direction, self.from_route = TurnDirection.turnRight, True
      else:
        self.turn_direction = TurnDirection.none
        if nav_side is not None:
          reason = 'blind spot'
    else:
      self.turn_direction = TurnDirection.none
      if nav_side is not None:
        reason = 'too fast' if v_ego >= nav_limit else 'blinker'
    self.nav_turn.trace(v_ego, nav_limit, reason)

  def get_turn_direction(self):
    if not self.enabled and not self.from_route:
      return TurnDirection.none
    return self.turn_direction
