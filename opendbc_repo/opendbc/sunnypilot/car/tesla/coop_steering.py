"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

lightpilot: Tesla cooperative steering, the way TeslaPilot does it (dzid26's VTB method, "vtb-sla").

The car is always steered with ANGLE_CONTROL, the same request that works without this option. The driver's torque on the
wheel is turned into an extra steering angle that is added to the angle openpilot asks for, so a light push moves the car
the way the driver pushes, and letting go brings the wheel back to the path. A firm grip still disengages (hands-on level 3
of the EPS, or more than 5 Nm).

Why not the LANE_KEEP_ASSIST control type, which this file used before: that changed the control type of the request, and on
the car this was built for the steering stopped with it. Now the steering message is the same one that is sent without this
option: only the angle in it differs, and always inside the panda's angle limits. So the control type cannot be what stops
the steering, and nothing in the panda changes either.
"""
import math
from collections import namedtuple

import numpy as np

from opendbc.car import structs, rate_limit, DT_CTRL
from opendbc.car.lateral import apply_steer_angle_limits_vm
from opendbc.car.tesla.values import CarControllerParams
from opendbc.car.vehicle_model import VehicleModel
from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP

CoopSteeringDataSP = namedtuple("CoopSteeringDataSP",
                                ["control_type"])

ANGLE_CONTROL = 1  # logical control type: TeslaCAN maps it to what the car's firmware expects

DT_LAT_CTRL = DT_CTRL * CarControllerParams.STEER_STEP  # the angle is sent at 50 Hz

STEER_DISENGAGE_TORQUE = 5.0  # Nm, instant torsion bar torque that disengages (the EPS' own hands-on level is checked too)

# when steering is resumed the wheel may not accelerate faster than this: no jump when it engages
STEER_RESUME_RATE_LIMIT_RAMP_RATE = 300  # deg/s^2

# driver torque -> extra angle
STEER_OVERRIDE_MIN_TORQUE = 0.5  # Nm, steering bias and noise (dead zone)
STEER_OVERRIDE_MAX_TORQUE = 2.5  # Nm, about the torque at which the EPS reports hands-on level 3
STEER_OVERRIDE_TORQUE_RANGE = STEER_OVERRIDE_MAX_TORQUE - STEER_OVERRIDE_MIN_TORQUE
STEER_OVERRIDE_MAX_LAT_ACCEL = 2.0  # m/s^2, the extra angle at full torque asks this much lateral acceleration
STEER_OVERRIDE_TARGET_ANGLE_MAX = CarControllerParams.ANGLE_LIMITS.STEER_ANGLE_MAX  # deg

# how fast the extra angle may move (per Nm of torque that is not yet balanced)
STEER_OVERRIDE_DELTA_GAIN_LIMIT = 125  # deg/s/Nm
STEER_OVERRIDE_DELTA_GAIN_LIMIT_CENTERING = CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE / DT_LAT_CTRL / STEER_OVERRIDE_TORQUE_RANGE


def get_steer_from_lat_accel(lat_accel: float, v_ego: float, VM: VehicleModel) -> float:
  """Steering wheel angle (deg) that gives a lateral acceleration at a speed."""
  curvature = lat_accel / (max(1.0, v_ego) ** 2)  # 1/m
  return math.degrees(VM.get_steer_from_curvature(curvature, v_ego, 0))


def apply_bounds(signal: float, limit: float) -> float:
  return float(np.clip(signal, -limit, limit))


def apply_deadzone(signal: float, deadzone: float) -> float:
  return signal - apply_bounds(signal, deadzone)


def get_override_torque_to_angle(v_ego: float, VM: VehicleModel, lat_accel: float) -> float:
  """Degrees of extra angle per Nm of (dead-zoned) driver torque."""
  angle = apply_bounds(get_steer_from_lat_accel(lat_accel, v_ego, VM), STEER_OVERRIDE_TARGET_ANGLE_MAX)
  return angle / STEER_OVERRIDE_TORQUE_RANGE


def calc_override_angle_delta_limit(torque: float, gain_limit: float) -> float:
  """Per-step limit of the extra angle's change, from the torque that is not yet balanced."""
  delta_gain_limit_max = CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE / DT_LAT_CTRL / STEER_OVERRIDE_TORQUE_RANGE
  return torque * min(gain_limit, delta_gain_limit_max) * DT_LAT_CTRL


class SteerRateLimiter:
  def __init__(self):
    self._last = 0.0

  def reset(self, angle: float) -> None:
    self._last = angle

  def update(self, angle: float, angle_delta_lim: float) -> float:
    self._last = rate_limit(angle, self._last, -angle_delta_lim, angle_delta_lim)
    return self._last


class CooperativeSteering:
  """Adds the driver's push to the requested steering angle. One step per angle command (50 Hz)."""

  def __init__(self):
    self.target_last = 0.0  # the angle openpilot asked for at the previous step
    self.sent_last = 0.0  # the angle that was sent at the previous step: what the panda compares the next one with
    self.angle_override = 0.0  # the extra angle that comes from the driver's torque
    self.resume_delta_limiter = SteerRateLimiter()
    self.resume_limiter = SteerRateLimiter()

  def reset(self, angle: float) -> None:
    self.target_last = angle
    self.sent_last = angle
    self.angle_override = 0.0

  def _resume_limit(self, lat_active: bool, apply_angle: float) -> float:
    if not lat_active:
      self.resume_delta_limiter.reset(0)
      self.resume_limiter.reset(apply_angle)
      return apply_angle
    delta_lim = self.resume_delta_limiter.update(CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE,
                                                 STEER_RESUME_RATE_LIMIT_RAMP_RATE * DT_LAT_CTRL ** 2)
    return self.resume_limiter.update(apply_angle, delta_lim)

  def _update_override(self, apply_angle_delta: float, driver_torque: float, v_ego: float, VM: VehicleModel) -> float:
    torque = apply_deadzone(driver_torque, STEER_OVERRIDE_MIN_TORQUE)
    torque_to_angle = get_override_torque_to_angle(v_ego, VM, STEER_OVERRIDE_MAX_LAT_ACCEL)
    target = torque * torque_to_angle
    target_error = target - self.angle_override

    # the torque that the extra angle already balances: the driver pushes against what is left
    holding_torque = self.angle_override / torque_to_angle if abs(v_ego) > 0.1 else 0.0
    hold_torque_delta = torque - holding_torque

    limit_away = calc_override_angle_delta_limit(abs(hold_torque_delta), STEER_OVERRIDE_DELTA_GAIN_LIMIT)
    limit_center = calc_override_angle_delta_limit(abs(hold_torque_delta), STEER_OVERRIDE_DELTA_GAIN_LIMIT_CENTERING)
    down_step = limit_center if self.angle_override > 0 else limit_away
    up_step = limit_center if self.angle_override < 0 else limit_away

    delta = float(np.clip(target_error, -down_step, up_step))

    # openpilot already turns the wheel the same way: that part does not need to come from the driver as well
    if delta * apply_angle_delta > 0:
      delta -= apply_bounds(apply_angle_delta, abs(delta))

    self.angle_override += delta
    return self.angle_override

  def update(self, apply_angle: float, lat_active: bool, enabled: bool, driver_torque: float, v_ego: float, v_ego_raw: float,
             steering_angle: float, VM: VehicleModel) -> float:
    """The angle to send. Not enabled: the angle is returned as it is."""
    if not enabled:
      return apply_angle

    apply_angle = self._resume_limit(lat_active, apply_angle)  # no sudden rotation on engagement

    if not lat_active:
      self.reset(apply_angle)
      return apply_angle

    delta = apply_angle - self.target_last
    self.target_last = apply_angle
    apply_angle += self._update_override(delta, driver_torque, v_ego, VM)

    # the final limits, the same as in the panda safety: against the angle that was sent last
    self.sent_last = apply_steer_angle_limits_vm(apply_angle, self.sent_last, v_ego_raw, steering_angle, lat_active,
                                                 CarControllerParams, VM)
    saturation = apply_angle - self.sent_last
    if self.angle_override * saturation > 0:  # anti windup: the extra angle that could not be sent is not kept
      self.angle_override -= apply_bounds(saturation, abs(self.angle_override))

    return self.sent_last


class CoopSteeringCarController:
  def __init__(self):
    self.coop_steering = CoopSteeringDataSP(ANGLE_CONTROL)
    self.coop_enabled = False
    self.cooperative = CooperativeSteering()

  @staticmethod
  def coop_steering_status_update(CP_SP: structs.CarParamsSP) -> CoopSteeringDataSP:
    # always ANGLE_CONTROL: the cooperative part is in the angle, not in the control type (see the top of this file)
    return CoopSteeringDataSP(ANGLE_CONTROL)

  def update(self, CP_SP: structs.CarParamsSP) -> None:
    self.coop_steering = self.coop_steering_status_update(CP_SP)
    self.coop_enabled = bool(CP_SP.flags & TeslaFlagsSP.COOP_STEERING.value)

  def coop_lat_active(self, lat_active: bool, CS) -> bool:
    """With cooperative steering a firm grip (more than 5 Nm) or a fast override also stops the steering."""
    if not self.coop_enabled:
      return lat_active
    return bool(lat_active and not CS.out.steeringDisengage and abs(CS.out.steeringTorque) <= STEER_DISENGAGE_TORQUE)

  def coop_apply_angle(self, apply_angle: float, lat_active: bool, CS, VM: VehicleModel) -> float:
    return self.cooperative.update(apply_angle, lat_active, self.coop_enabled, CS.out.steeringTorque, CS.out.vEgo, CS.out.vEgoRaw,
                                   CS.out.steeringAngleDeg, VM)
