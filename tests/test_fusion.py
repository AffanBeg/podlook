"""Behavioral tests for latency-aligned correction and loss/recovery semantics."""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fusion', ROOT / 'windows/fusion.py')
fusion = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fusion
SPEC.loader.exec_module(fusion)


def feed(engine, begin, end, air=(0, 0, 0), camera=(0, 0, 0), camera_valid=True):
    result = None
    for step in range(round(begin * 100), round(end * 100) + 1):
        t = step / 100
        engine.push_airpods(air(t) if callable(air) else air, t)
        if step % 5 == 0:
            engine.push_webcam(camera(t) if callable(camera) else camera, t, valid=camera_valid)
        result = engine.sample(t)
    return result


class FusionTests(unittest.TestCase):
    def hybrid(self, **options):
        e = fusion.FusionEngine(mode='hybrid', **options)
        feed(e, 0, 1)
        self.assertTrue(e.calibrate(1))
        return e

    def test_airpods_default_is_unmodified_and_camera_cannot_influence_it(self):
        e = fusion.FusionEngine()
        result = feed(e, 0, 5, air=(12, -15, 8), camera=(-20, 0, 0))
        self.assertFalse(e.auto_correction)
        self.assertEqual(result.pose, (12, -15, 8))
        self.assertFalse(result.webcam_used)
        self.assertEqual(result.bias_degrees, 0)

    def test_manual_calibration_captures_independent_stream_neutrals(self):
        e = fusion.FusionEngine(mode='hybrid')
        feed(e, 0, 1, air=(17, 2, -3), camera=(5, -1, 2))
        self.assertTrue(e.calibrate(1))
        result = feed(e, 1.01, 2, air=(27, 7, -3), camera=(15, 4, 2))
        self.assertEqual(result.pose, (10, 5, 0))
        self.assertAlmostEqual(result.bias_degrees, 0)

    def test_drift_is_corrected_slowly_with_hard_rate_bound(self):
        e = self.hybrid(correction_tau=1, correction_rate=0.75)
        previous = 0
        for i in range(101, 1001):
            t = i / 100
            e.push_airpods((10, 3, -4), t)
            if i % 5 == 0:
                e.push_webcam((0, 3, -4), t)
            r = e.sample(t)
            self.assertLessEqual(abs(r.bias_degrees - previous), 0.007500001)
            previous = r.bias_degrees
        self.assertLess(r.bias_degrees, -5)
        self.assertGreater(r.pose[0], 0)
        self.assertEqual(r.pose[1:], (3, -4))  # No camera-driven pitch/roll drift.

    def test_latency_alignment_does_not_mistake_motion_for_drift(self):
        e = self.hybrid(webcam_latency=0.2, correction_tau=1)
        # Slow motion passes the stationary gate. The camera delivers this same
        # motion 200ms later; an unaligned implementation would invent a bias.
        trajectory = lambda t: max(0, t - 1) * 2
        result = feed(e, 1.01, 5,
                      air=lambda t: (trajectory(t), 0, 0),
                      camera=lambda t: (trajectory(t - 0.2), 0, 0))
        self.assertTrue(result.webcam_used)
        self.assertAlmostEqual(result.bias_degrees, 0, places=8)

    def test_camera_loss_freezes_bias_and_motion_continues(self):
        e = self.hybrid(correction_tau=1)
        before = feed(e, 1.01, 3, air=(8, 0, 0))
        self.assertLess(before.bias_degrees, 0)
        self.assertTrue(e.push_webcam(None, 3.01, valid=False))
        after = feed(e, 3.01, 4, air=(12, 2, 1), camera_valid=False)
        self.assertEqual(after.bias_degrees, before.bias_degrees)
        self.assertAlmostEqual(after.pose[0], 12 + before.bias_degrees)
        self.assertEqual(after.status, 'webcam_lost_airpods_fallback')
        self.assertFalse(after.webcam_used)

    def test_camera_stale_freezes_and_recovery_does_not_snap(self):
        e = self.hybrid(correction_tau=1)
        feed(e, 1.01, 2, air=(8, 0, 0))
        for i in range(201, 301):
            e.push_airpods((8, 0, 0), i / 100)
            stale = e.sample(i / 100)
        bias = stale.bias_degrees
        self.assertEqual(stale.status, 'webcam_lost_airpods_fallback')
        recovered = feed(e, 3.01, 3.02, air=(8, 0, 0))
        self.assertEqual(recovered.bias_degrees, bias)
        resumed = feed(e, 3.03, 4, air=(8, 0, 0))
        self.assertLess(resumed.bias_degrees, bias)
        self.assertLessEqual(abs(resumed.bias_degrees - bias), 0.75)

    def test_airpods_loss_latches_neutral_until_manual_reset(self):
        e = self.hybrid()
        feed(e, 1.01, 2, air=(4, 1, 2))
        lost = e.sample(2.5)
        self.assertEqual(lost.pose, (0, 0, 0))
        self.assertFalse(lost.calibrated)
        self.assertEqual(lost.status, 'airpods_lost_reset_required')
        recovered = feed(e, 2.51, 3.5, air=(4, 1, 2))
        self.assertEqual(recovered.pose, (0, 0, 0))
        self.assertTrue(e.calibrate(3.5))
        self.assertEqual(e.sample(3.5).pose, (0, 0, 0))
        self.assertTrue(e.sample(3.5).calibrated)

    def test_gap_detected_even_without_sampling_during_disconnect(self):
        e = fusion.FusionEngine()
        e.push_airpods((10, 0, 0), 1)
        e.sample(1)
        e.push_airpods((11, 0, 0), 2)
        self.assertEqual(e.sample(2).status, 'airpods_lost_reset_required')

    def test_invalid_camera_samples_fail_closed(self):
        e = self.hybrid()
        self.assertFalse(e.push_webcam((float('nan'), 0, 0), 1.01))
        e.push_airpods((0, 0, 0), 1.01)
        self.assertEqual(e.sample(1.01).status, 'webcam_lost_airpods_fallback')

    def test_malformed_and_out_of_order_airpods_cannot_replace_pose(self):
        e = fusion.FusionEngine()
        self.assertTrue(e.push_airpods((20, 4, 3), 1))
        for bad in [(float('nan'), 0, 0), (0, float('inf'), 0), (0, 0, -181), (), None]:
            self.assertFalse(e.push_airpods(bad, 1.01))
        self.assertFalse(e.push_airpods((0, 0, 0), 0.9))
        self.assertFalse(e.push_airpods((0, 0, 0), 1))
        self.assertEqual(e.sample(1.01).pose, (20, 4, 3))

    def test_wrap_at_neutral_does_not_create_full_turn(self):
        e = fusion.FusionEngine()
        feed(e, 0, 1, air=(179, 0, 0))
        self.assertTrue(e.calibrate(1))
        e.push_airpods((-179, 0, 0), 1.01)
        self.assertEqual(e.sample(1.01).pose, (2, 0, 0))

    def test_unstable_or_far_from_forward_camera_never_corrects(self):
        e = self.hybrid()
        r = feed(e, 1.01, 3, air=(30, 0, 0), camera=(40, 0, 0))
        self.assertEqual(r.bias_degrees, 0)
        self.assertFalse(r.webcam_used)
        r = feed(e, 3.01, 5, air=(5, 0, 0),
                 camera=lambda t: (10 if round(t * 20) % 2 else -10, 0, 0))
        self.assertEqual(r.bias_degrees, 0)
        self.assertFalse(r.webcam_used)

    def test_huge_camera_outlier_cannot_steer_camera(self):
        e = self.hybrid()
        r = feed(e, 1.01, 3, air=(-10, 0, 0), camera=(15, 0, 0))
        self.assertEqual(r.bias_degrees, 0)
        self.assertEqual(r.status, 'motion_gate_airpods_fallback')

    def test_disabling_correction_preserves_continuity(self):
        e = self.hybrid(correction_tau=1)
        before = feed(e, 1.01, 3, air=(8, 0, 0))
        e.set_auto_correction(False)
        after = feed(e, 3.01, 4, air=(8, 0, 0))
        self.assertEqual(after.pose, before.pose)
        self.assertEqual(after.bias_degrees, before.bias_degrees)
        self.assertEqual(after.status, 'correction_off')

    def test_optional_center_assist_requires_calibration_and_neutral_hold(self):
        e = fusion.FusionEngine(center_assist=True)
        before = feed(e, 0, 1, air=(3, 0, 0))
        self.assertEqual(before.bias_degrees, 0)
        self.assertTrue(e.calibrate(1))
        after = feed(e, 1.01, 6, air=(6, 0, 0))
        self.assertLess(after.bias_degrees, 0)
        self.assertGreater(after.bias_degrees, -0.75)
        beyond = feed(e, 6.01, 8, air=(23, 0, 0))
        self.assertEqual(beyond.bias_degrees, after.bias_degrees)

    def test_buffers_are_bounded_and_bad_settings_rejected(self):
        e = fusion.FusionEngine()
        for i in range(2000):
            e.push_airpods((0, 0, 0), i / 1000)
            e.push_webcam((0, 0, 0), i / 1000)
        self.assertLessEqual(len(e._air), 512)
        self.assertLessEqual(len(e._camera), 256)
        for args in [dict(mode='other'), dict(webcam_latency=-1), dict(webcam_latency=float('nan')),
                     dict(correction_rate=4), dict(correction_tau=0), dict(airpods_timeout=float('inf'))]:
            with self.assertRaises(ValueError):
                fusion.FusionEngine(**args)

    def test_calibration_waits_for_stable_aligned_history(self):
        e = fusion.FusionEngine(mode='hybrid')
        feed(e, 0, 0.2)
        self.assertFalse(e.calibrate(0.2))
        feed(e, 0.21, 1, camera=(50, 0, 0))
        self.assertFalse(e.calibrate(1))
        feed(e, 1.01, 2)
        self.assertTrue(e.calibrate(2))

    def test_immediate_center_clears_drift_without_old_reference_undoing_it(self):
        e = self.hybrid(correction_tau=1)
        before = feed(e, 1.01, 3, air=(8, 2, -1))
        self.assertNotEqual(before.pose[0], 0)
        self.assertTrue(e.recenter(3))
        centered = e.sample(3)
        self.assertEqual(centered.pose, (0, 0, 0))
        self.assertTrue(centered.calibrated)
        settled = feed(e, 3.01, 5, air=(8, 2, -1))
        self.assertEqual(settled.pose, (0, 0, 0))

    def test_immediate_center_without_camera_requires_new_hybrid_calibration(self):
        e = self.hybrid()
        feed(e, 1.01, 2, air=(8, 0, 0))
        e.push_webcam(None, 2.01, valid=False)
        e.push_airpods((8, 0, 0), 2.01)
        self.assertTrue(e.recenter(2.01))
        result = e.sample(2.01)
        self.assertEqual(result.pose, (0, 0, 0))
        self.assertFalse(result.calibrated)
        self.assertEqual(result.status, 'hybrid_uncalibrated')
        self.assertFalse(e.recenter(3))

    def test_immediate_center_compensates_delayed_camera_frame(self):
        e = self.hybrid(webcam_latency=0.2, correction_tau=1)
        trajectory = lambda t: max(0, t - 1) * 2
        feed(e, 1.01, 3, air=lambda t: (trajectory(t), 0, 0),
             camera=lambda t: (trajectory(t - 0.2), 0, 0))
        self.assertTrue(e.recenter(3))
        result = e.sample(3)
        self.assertEqual(result.pose, (0, 0, 0))
        self.assertTrue(result.calibrated)
        result = feed(e, 3.01, 4, air=lambda t: (trajectory(t), 0, 0),
                      camera=lambda t: (trajectory(t - 0.2), 0, 0))
        self.assertAlmostEqual(result.bias_degrees, 0, places=8)
        self.assertAlmostEqual(result.pose[0], 2, places=8)


if __name__ == '__main__':
    unittest.main()
