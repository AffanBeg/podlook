"""Optional actual-model tests, no webcam. Supply the two fixture paths in env.

PODLOOK_TEST_MODEL: verified face_landmarker.task
PODLOOK_TEST_PORTRAIT: Google's public mediapipe-assets/portrait.jpg test image
No downloads or camera access are performed by these tests.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webcam import _MediaPipeDetector, sample_from_result, verify_model


@unittest.skipUnless(os.environ.get("PODLOOK_TEST_MODEL"),
                     "Set a local model fixture path for actual inference tests")
class ActualModelTests(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        self.camera_guard = patch.object(cv2, "VideoCapture", side_effect=AssertionError("Test must not use a webcam"))
        self.camera_guard.start()
        self.addCleanup(self.camera_guard.stop)
        model = Path(os.environ["PODLOOK_TEST_MODEL"])
        verify_model(model)
        self.detector = _MediaPipeDetector(model)
        self.addCleanup(self.detector.close)
        if os.environ.get("PODLOOK_TEST_PORTRAIT"):
            self.portrait = cv2.imread(os.environ["PODLOOK_TEST_PORTRAIT"])
            self.assertIsNotNone(self.portrait, "Portrait fixture must be readable")

    def test_black_frame_has_no_valid_face(self):
        result = self.detector.detect(self.np.zeros((480, 640, 3), dtype=self.np.uint8), 1)
        sample = sample_from_result(result, 1, now=1)
        self.assertFalse(sample.valid)
        self.assertEqual(sample.reason, "No face detected")

    @unittest.skipUnless(os.environ.get("PODLOOK_TEST_PORTRAIT"), "Set public portrait fixture path")
    def test_public_portrait_produces_valid_pose(self):
        sample = sample_from_result(self.detector.detect(self.portrait, 1), 1, now=1)
        self.assertTrue(sample.valid, sample.reason)
        self.assertIsNone(sample.confidence)

    @unittest.skipUnless(os.environ.get("PODLOOK_TEST_PORTRAIT"), "Set public portrait fixture path")
    def test_multiple_faces_and_face_loss_rejected(self):
        face = self.cv2.resize(self.portrait, (320, 400))
        result = self.detector.detect(self.np.hstack((face, face)), 1)
        sample = sample_from_result(result, 1, now=1)
        self.assertFalse(sample.valid)
        self.assertIn("Multiple faces", sample.reason)
        result = self.detector.detect(self.np.zeros((400, 640, 3), dtype=self.np.uint8), 2)
        self.assertFalse(sample_from_result(result, 2, now=2).valid)


if __name__ == "__main__":
    unittest.main()
