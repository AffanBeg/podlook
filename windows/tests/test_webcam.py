"""Pure geometry and injected-provider tests. These NEVER open the webcam."""
import math
from pathlib import Path
import queue
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webcam import WebcamProvider, WebcamSample, default_model_path, matrix_to_angles, sample_from_result, verify_model
from download_model import download_model


def transform(yaw=0, pitch=0, roll=0, scale=1):
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    # Independently expanded Ry * Rx * Rz.
    rows = [[cy*cr+sy*sp*sr, -cy*sr+sy*sp*cr, sy*cp, 1],
            [cp*sr, cp*cr, -sp, 2],
            [-sy*cr+cy*sp*sr, sy*sr+cy*sp*cr, cy*cp, -40],
            [0, 0, 0, 1]]
    for row in rows[:3]:
        for i in range(3):
            row[i] *= scale
    return rows


def result(faces=1, matrix=None):
    return SimpleNamespace(face_landmarks=[[]] * faces,
                           facial_transformation_matrixes=[matrix or transform()] * faces)


class GeometryTests(unittest.TestCase):
    def test_identity_and_translation(self):
        self.assertEqual(matrix_to_angles(transform()), (0, 0, 0))

    def test_compound_angles_and_scale(self):
        for angles in ((30, 20, -10), (-40, -30, 15), (0, 60, 0), (60, 0, 0)):
            for scale in (.25, 1, 5):
                with self.subTest(angles=angles, scale=scale):
                    for actual, expected in zip(matrix_to_angles(transform(*angles, scale)), angles):
                        self.assertAlmostEqual(actual, expected, places=8)

    def test_bad_shapes_nonfinite_shear_reflection(self):
        invalid = [[], [[1]*3]*3]
        for edit in (lambda m: m[0].__setitem__(0, math.nan),
                     lambda m: m[0].__setitem__(0, -1),
                     lambda m: m[0].__setitem__(1, .7),
                     lambda m: m[3].__setitem__(3, 0)):
            m = transform()
            edit(m)
            invalid.append(m)
        invalid.extend((transform(scale=0), transform(pitch=90)))
        for matrix in invalid:
            with self.subTest(matrix=matrix):
                with self.assertRaises(ValueError):
                    matrix_to_angles(matrix)

    def test_no_face_and_multiple_faces_never_valid(self):
        for n in (0, 2, 3):
            sample = sample_from_result(result(n), 10, now=10.01)
            self.assertFalse(sample.valid)
            self.assertIsNone(sample.confidence)

    def test_success_uses_capture_timestamp_and_no_invented_confidence(self):
        sample = sample_from_result(result(matrix=transform(20, -10, 5)), 10, now=10.2)
        self.assertTrue(sample.valid)
        self.assertEqual(sample.timestamp, 10)
        self.assertAlmostEqual(sample.yaw, 20)
        self.assertIsNone(sample.confidence)

    def test_stale_future_and_extreme_pose_rejected(self):
        for captured, now in ((10, 10.3), (11, 10), (math.nan, 10)):
            self.assertFalse(sample_from_result(result(), captured, now=now).valid)
        self.assertFalse(sample_from_result(result(matrix=transform(yaw=80)), 10, now=10).valid)

    def test_missing_matrix_rejected(self):
        bad = result()
        bad.facial_transformation_matrixes = []
        self.assertFalse(sample_from_result(bad, 10, now=10).valid)


class FakeCamera:
    def __init__(self):
        self.frames = queue.Queue()
        self.closed = False
        self.last_frame = None

    def read(self):
        try:
            self.last_frame = self.frames.get(timeout=.02)
        except queue.Empty:
            pass
        return self.last_frame is not None, self.last_frame

    def close(self):
        self.closed = True


class FakeDetector:
    def __init__(self):
        self.closed = False
        self.timestamps = []

    def detect(self, frame, timestamp):
        self.timestamps.append(timestamp)
        return frame

    def close(self):
        self.closed = True


class ProviderTests(unittest.TestCase):
    def test_construction_and_import_do_not_open_camera(self):
        opened = []
        provider = WebcamProvider(_capture_factory=lambda i: opened.append(i), _detector_factory=lambda p: None)
        self.assertFalse(provider.running)
        self.assertEqual(opened, [])
        self.assertIsNone(provider.poll())

    def test_real_workers_with_injected_camera_and_detector(self):
        camera, detector = FakeCamera(), FakeDetector()
        camera.frames.put(result(matrix=transform(12, 3, 1)))
        provider = WebcamProvider(_capture_factory=lambda i: camera, _detector_factory=lambda p: detector)
        provider.start()
        received = None
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            received = provider.poll()
            if received is not None:
                break
            time.sleep(.002)
        provider.stop()
        self.assertIsNotNone(received)
        self.assertTrue(received.valid)
        self.assertAlmostEqual(received.yaw, 12)
        self.assertTrue(camera.closed)
        self.assertTrue(detector.closed)
        self.assertFalse(provider.running)
        self.assertFalse(provider.poll().valid)
        self.assertIsNone(provider.poll())

    def test_camera_error_surfaces_and_stops(self):
        def failing_camera(index):
            raise RuntimeError("permission denied")
        provider = WebcamProvider(_capture_factory=failing_camera, _detector_factory=lambda p: FakeDetector())
        provider.start()
        deadline = time.monotonic() + .5
        while provider.running and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertIn("permission denied", provider.error)
        self.assertFalse(provider.poll().valid)
        provider.stop()

    def test_poll_invalidates_old_mailbox_result(self):
        provider = WebcamProvider(_clock=lambda: 11)
        provider._publish(WebcamSample(10, 1, 2, 3, True, "Face tracked"))
        self.assertFalse(provider.poll().valid)
        self.assertIsNone(provider.poll())

    def test_missing_model_does_not_open_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("webcam._OpenCVCapture") as camera:
                provider = WebcamProvider(model_path=Path(directory) / "missing.task")
                provider.start()
                self.assertIn("model missing", provider.error.lower())
                camera.assert_not_called()


class ModelTests(unittest.TestCase):
    def test_wrong_hash_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.task"
            path.write_bytes(b"not a model")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                verify_model(path)

    def test_failed_download_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.task"
            path.write_bytes(b"original")
            with patch("download_model.urllib.request.urlopen") as response:
                response.return_value.__enter__.return_value.read.return_value = b"untrusted bytes"
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    download_model(path)
            self.assertEqual(path.read_bytes(), b"original")

    def test_bundled_model_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "models" / "face_landmarker.task"
            model.parent.mkdir()
            model.write_bytes(b"placeholder")
            with patch.object(sys, "_MEIPASS", directory, create=True):
                self.assertEqual(default_model_path(), model)


if __name__ == "__main__":
    unittest.main()
