"""Tesla cooperative steering (the TeslaPilot / dzid26 VTB way): the driver's torque becomes extra steering angle, the car is
steered with ANGLE_CONTROL only, and everything sent stays inside the panda's angle limits."""
import random
import unittest
from types import SimpleNamespace

from opendbc.car import structs
from opendbc.car.lateral import apply_steer_angle_limits_vm
from opendbc.car.tesla.carcontroller import CarController, get_safety_CP
from opendbc.car.tesla.teslacan import get_steer_ctrl_type
from opendbc.car.tesla.values import DBC, CarControllerParams, TeslaFlags
from opendbc.car.vehicle_model import VehicleModel
from opendbc.sunnypilot.car.tesla import coop_steering as coop
from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP

STEERING_CONTROL_ADDR = 0x488
MAX_RATE = CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE


class TestCooperativeSteering(unittest.TestCase):
  def setUp(self):
    self.VM = VehicleModel(get_safety_CP())
    self.coop = coop.CooperativeSteering()

  def step(self, target, torque, v=15.0, active=True, enabled=True, steering_angle=0.0):
    return self.coop.update(target, active, enabled, torque, v, v, steering_angle, self.VM)

  def settle(self, target, torque, steps, **kw):
    out = []
    for _ in range(steps):
      out.append(self.step(target, torque, **kw))
    return out

  def test_not_enabled_the_angle_is_returned_as_it_is(self):
    for torque in (0.0, 2.0, -4.0):
      self.assertEqual(self.step(7.5, torque, enabled=False), 7.5)
    self.assertEqual(self.coop.angle_override, 0.0)

  def test_without_a_push_the_requested_angle_is_sent(self):
    self.step(0.0, 0.0, active=False)
    out = self.settle(3.0, 0.3, 40)  # 0.3 Nm is inside the dead zone
    self.assertAlmostEqual(out[-1], 3.0, places=6)
    self.assertEqual(self.coop.angle_override, 0.0)

  def test_a_push_adds_angle_in_its_direction(self):
    self.step(0.0, 0.0, active=False)
    out = self.settle(0.0, 1.5, 120)
    expected = (1.5 - coop.STEER_OVERRIDE_MIN_TORQUE) * coop.get_override_torque_to_angle(15.0, self.VM, coop.STEER_OVERRIDE_MAX_LAT_ACCEL)
    self.assertGreater(out[-1], 0.0)
    self.assertAlmostEqual(out[-1], expected, delta=0.02 * expected)
    self.assertTrue(all(b >= a - 1e-9 for a, b in zip(out, out[1:], strict=False)), "the extra angle only grows while the push is held")

  def test_a_push_the_other_way_adds_angle_the_other_way(self):
    self.step(0.0, 0.0, active=False)
    up = self.settle(0.0, 1.5, 120)[-1]
    self.coop.reset(0.0)
    down = self.settle(0.0, -1.5, 120)[-1]
    self.assertAlmostEqual(up, -down, places=6)

  def test_letting_go_brings_the_wheel_back_to_the_path(self):
    self.step(0.0, 0.0, active=False)
    self.settle(0.0, 2.0, 120)
    out = self.settle(0.0, 0.0, 120)
    self.assertLess(abs(out[-1]), 0.05)
    self.assertLess(abs(self.coop.angle_override), 0.05)

  def test_the_extra_angle_is_added_to_the_requested_angle(self):
    self.step(0.0, 0.0, active=False)
    base = self.settle(4.0, 0.0, 40)[-1]
    self.coop.reset(0.0)
    self.coop.resume_limiter.reset(0.0)
    self.coop.resume_delta_limiter.reset(0.0)
    pushed = self.settle(4.0, 1.5, 160)[-1]
    self.assertGreater(pushed, base + 1.0)

  def test_engaging_does_not_make_the_wheel_jump(self):
    self.step(0.0, 0.0, active=False)
    first = self.step(25.0, 0.0)
    self.assertLessEqual(first, coop.STEER_RESUME_RATE_LIMIT_RAMP_RATE * coop.DT_LAT_CTRL ** 2 + 1e-9)
    out = [first] + self.settle(25.0, 0.0, 60)
    self.assertTrue(all(abs(b - a) <= MAX_RATE + 1e-9 for a, b in zip(out, out[1:], strict=False)))
    self.assertAlmostEqual(out[-1], 25.0, places=6)

  def test_not_active_forgets_the_push_and_follows_the_wheel(self):
    self.step(0.0, 0.0, active=False)
    self.settle(0.0, 2.0, 60)
    self.assertNotEqual(self.coop.angle_override, 0.0)
    self.assertEqual(self.step(12.0, 2.0, active=False), 12.0)
    self.assertEqual(self.coop.angle_override, 0.0)

  def test_everything_sent_stays_inside_the_panda_limits(self):
    for seed, v in enumerate((0.5, 3.0, 8.0, 15.0, 25.0, 35.0)):  # a drive that starts at each speed; the speed changes gradually
      rng = random.Random(seed)
      self.coop = coop.CooperativeSteering()
      self.step(0.0, 0.0, active=False)
      last, target = 0.0, 0.0
      for _ in range(1500):
        v = max(0.5, min(35.0, v + rng.uniform(-0.05, 0.05)))
        target += rng.uniform(-3.0, 3.0)
        target = max(-300.0, min(300.0, target))
        torque = rng.choice((0.0, 0.0, 1.0, 2.0, -2.0, 4.5, -4.5, rng.uniform(-5, 5)))
        out = self.step(target, torque, v=v, steering_angle=last)
        self.assertEqual(out, out)  # not NaN
        self.assertLessEqual(abs(out - last), MAX_RATE + 0.05, f"rate at {v:.1f} m/s")
        self.assertLessEqual(abs(out), CarControllerParams.ANGLE_LIMITS.STEER_ANGLE_MAX + 1e-9)
        # the same limiter the panda mirrors does not change what was sent
        again = apply_steer_angle_limits_vm(out, last, v, last, True, CarControllerParams, self.VM)
        self.assertAlmostEqual(again, out, places=6)
        self.assertLessEqual(abs(self.coop.angle_override), coop.STEER_OVERRIDE_TARGET_ANGLE_MAX + 1e-9)
        last = out

  def test_a_standstill_push_is_bounded(self):
    self.step(0.0, 0.0, active=False, v=0.0)
    out = self.settle(0.0, 2.5, 200, v=0.0)
    self.assertTrue(all(abs(b - a) <= MAX_RATE + 1e-9 for a, b in zip(out, out[1:], strict=False)))
    self.assertLessEqual(abs(out[-1]), coop.STEER_OVERRIDE_TARGET_ANGLE_MAX)


def decode_steering_control(can_sends):
  """(control type, steering angle in degrees) of the steering message in a list of CAN messages, or None."""
  for addr, dat, _bus in can_sends:
    if addr == STEERING_CONTROL_ADDR:
      raw = ((dat[0] & 0x7F) << 8) | dat[1]
      return dat[2] >> 5, -(raw - 16384) / 10.0  # the message carries the angle with the opposite sign
  return None


class TestTeslaCarControllerCooperative(unittest.TestCase):
  def make(self, coop_on, fsd14=False):
    CP = get_safety_CP()
    if fsd14:
      CP.flags = int(CP.flags) | int(TeslaFlags.FSD_14)
    CP_SP = structs.CarParamsSP()
    CP_SP.flags = TeslaFlagsSP.COOP_STEERING.value if coop_on else 0
    self.CP = CP
    return CarController(DBC["TESLA_MODEL_3"], CP, CP_SP)

  def drive(self, cc, frames, target=0.0, torque=0.0, v=15.0, steering_angle=0.0, lat_active=True, hands_on_level=0, disengage=False):
    sent = []
    for _ in range(frames):
      CC = structs.CarControl()
      CC.latActive = lat_active
      CC.actuators.steeringAngleDeg = target
      CS = SimpleNamespace(hands_on_level=hands_on_level, das_control={"DAS_controlCounter": 0},
                           out=SimpleNamespace(vEgo=v, vEgoRaw=v, steeringAngleDeg=steering_angle, steeringTorque=torque,
                                               steeringDisengage=disengage))
      _, can_sends = cc.update(CC.as_reader(), None, CS, 0)  # the car controller gets a reader, as in the car
      message = decode_steering_control(can_sends)
      if message is not None:
        sent.append(message)
    return sent

  def test_off_is_what_it_was_before(self):
    cc = self.make(False)
    sent = self.drive(cc, 200, target=6.0, torque=2.0)
    reference_last = 0.0
    VM = VehicleModel(get_safety_CP())
    for control_type, angle in sent:
      reference_last = apply_steer_angle_limits_vm(6.0, reference_last, 15.0, 0.0, True, CarControllerParams, VM)
      self.assertEqual(control_type, get_steer_ctrl_type(self.CP.flags, 1))
      self.assertAlmostEqual(angle, reference_last, delta=0.11)
    self.assertAlmostEqual(sent[-1][1], 6.0, delta=0.11)  # the driver's torque changes nothing

  def test_on_steers_with_angle_control_and_follows_the_push(self):
    cc = self.make(True)
    self.drive(cc, 4, target=0.0, torque=0.0, lat_active=False)
    sent = self.drive(cc, 300, target=0.0, torque=1.5)
    self.assertTrue(all(t == get_steer_ctrl_type(self.CP.flags, 1) for t, _ in sent), "ANGLE_CONTROL only, never lane keep assist")
    self.assertGreater(sent[-1][1], 5.0)
    self.assertTrue(all(abs(b[1] - a[1]) <= MAX_RATE + 0.11 for a, b in zip(sent, sent[1:], strict=False)))

  def test_on_never_uses_lane_keep_assist_on_the_new_firmware_either(self):
    for fsd14 in (False, True):
      cc = self.make(True, fsd14=fsd14)
      sent = self.drive(cc, 300, target=3.0, torque=2.0)
      expected = get_steer_ctrl_type(self.CP.flags, 1)
      self.assertEqual({t for t, _ in sent}, {expected})
      # and never the type that the other mapping would give for lane keep assist
      self.assertNotIn(get_steer_ctrl_type(self.CP.flags, 2), {t for t, _ in sent})

  def test_on_a_firm_grip_stops_the_steering(self):
    cc = self.make(True)
    self.drive(cc, 100, target=0.0, torque=1.0)
    sent = self.drive(cc, 10, target=0.0, torque=6.0)
    self.assertEqual({t for t, _ in sent}, {0})  # NONE: the car is not steered

  def test_on_hands_on_level_three_stops_the_steering(self):
    cc = self.make(True)
    sent = self.drive(cc, 10, target=0.0, torque=2.0, hands_on_level=3)
    self.assertEqual({t for t, _ in sent}, {0})

  def test_on_a_fast_override_stops_the_steering(self):
    cc = self.make(True)
    sent = self.drive(cc, 10, target=0.0, torque=1.0, disengage=True)
    self.assertEqual({t for t, _ in sent}, {0})

  def test_off_ignores_the_torque_limit(self):
    cc = self.make(False)
    sent = self.drive(cc, 10, target=0.0, torque=6.0)  # as before: only the EPS' hands-on level stops it
    self.assertEqual({t for t, _ in sent}, {get_steer_ctrl_type(self.CP.flags, 1)})

  def test_the_control_type_is_angle_control_with_the_option_on_and_off(self):
    CP_SP = structs.CarParamsSP()
    for flags in (0, TeslaFlagsSP.COOP_STEERING.value):
      CP_SP.flags = flags
      self.assertEqual(coop.CoopSteeringCarController.coop_steering_status_update(CP_SP).control_type, 1)


if __name__ == "__main__":
  unittest.main()
