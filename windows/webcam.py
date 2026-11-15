"""Local-only webcam pose provider. Importing this module never opens a camera.

Frames are neither saved nor uploaded. Camera capture and inference use separate
workers with a one-frame mailbox, so slow inference cannot create a frame queue.
Timestamp means host receipt from the camera driver, not sensor exposure time.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import sys
import subprocess
import threading
import time

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


def probe_camera(index):
    """Probe one index in a disposable process; never store captured frames."""
    if not 0 <= index <= 8:
        return False
    camera = None
    try:
        camera = _OpenCVCapture(index)
        ok, frame = camera.read()
        return bool(ok and frame is not None)
    except Exception:
        return False
    finally:
        if camera is not None:
            camera.close()


def discover_cameras(cancel=None, *, runner=subprocess.run):
    """Explicit scan with a three-second child-process limit per device.

    Driver enumeration names cannot reliably be mapped to OpenCV indexes, so
    labels deliberately use tested indexes rather than guessed device names.
    """
    found = []
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).with_name("companion.py"))]
    for index in range(9):
        if cancel is not None and cancel.is_set():
            break
        try:
            result = runner(prefix + ["--probe-camera", str(index)], timeout=3,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                found.append(index)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return found


def default_model_path() -> Path:
    if hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "models" / "face_landmarker.task"
        if bundled.is_file():
            return bundled
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "PodLook" / "models" / "face_landmarker.task"


def verify_model(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError("Face model missing. Run download_model.py before enabling webcam tracking.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Face model checksum mismatch. Run download_model.py to restore the verified model.")


@dataclass(frozen=True)
class WebcamSample:
    timestamp: float
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    valid: bool = False
    reason: str = "No face"
    # Detection thresholds are configuration, not an exposed per-result score.
    confidence: float | None = None


def matrix_to_angles(matrix) -> tuple[float, float, float]:
    """Canonical face-to-camera 4x4 transform → Y-X-Z rotation in degrees.

    Camera metric coordinates are right handed (x right, y up, z toward camera).
    Input frames are NOT mirrored. Check physical axis direction in calibration.
    Translation is intentionally ignored; uniform positive scale is removed.
    Reflections, shear, malformed matrices and near-gimbal-lock poses are rejected.
    """
    try:
        if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
            raise ValueError("Expected a 4x4 face transform")
        m = [[float(v) for v in row] for row in matrix]
    except (TypeError, OverflowError) as exc:
        raise ValueError("Invalid face transform") from exc
    if not all(math.isfinite(v) for row in m for v in row):
        raise ValueError("Nonfinite face transform")
    if max(abs(m[3][i] - (1.0 if i == 3 else 0.0)) for i in range(4)) > 1e-3:
        raise ValueError("Invalid homogeneous transform")
    columns = [[m[row][col] for row in range(3)] for col in range(3)]
    lengths = [math.sqrt(sum(v * v for v in col)) for col in columns]
    if any(not math.isfinite(v) or v < 1e-8 for v in lengths):
        raise ValueError("Degenerate face transform")
    if max(lengths) / min(lengths) > 1.08:
        raise ValueError("Nonuniform face transform scale")
    c = [[v / length for v in col] for col, length in zip(columns, lengths)]
    if any(abs(sum(a * b for a, b in zip(c[i], c[j]))) > .08
           for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Sheared face transform")
    det = (c[0][0] * (c[1][1] * c[2][2] - c[1][2] * c[2][1])
           - c[1][0] * (c[0][1] * c[2][2] - c[0][2] * c[2][1])
           + c[2][0] * (c[0][1] * c[1][2] - c[0][2] * c[1][1]))
    if det < .95:
        raise ValueError("Reflected or invalid face transform")
    r = [[c[col][row] for col in range(3)] for row in range(3)]
    pitch = math.asin(max(-1.0, min(1.0, -r[1][2])))
    if abs(math.cos(pitch)) < .1:
        raise ValueError("Head pitch outside reliable tracking range")
    yaw = math.atan2(r[0][2], r[2][2])
    roll = math.atan2(r[1][0], r[1][1])
    return tuple(math.degrees(v) for v in (yaw, pitch, roll))


def sample_from_result(result, captured_at: float, *, now: float,
                       max_age: float = .25) -> WebcamSample:
    if (not math.isfinite(captured_at) or not math.isfinite(now)
            or now < captured_at or now - captured_at > max_age):
        return WebcamSample(captured_at, reason="Stale camera frame")
    faces = getattr(result, "face_landmarks", ())
    matrices = getattr(result, "facial_transformation_matrixes", ())
    if len(faces) == 0:
        return WebcamSample(captured_at, reason="No face detected")
    if len(faces) != 1:
        return WebcamSample(captured_at, reason="Multiple faces detected; only one person may be tracked")
    if len(matrices) != 1:
        return WebcamSample(captured_at, reason="Face orientation unavailable")
    try:
        yaw, pitch, roll = matrix_to_angles(matrices[0])
    except (ValueError, TypeError) as exc:
        return WebcamSample(captured_at, reason=str(exc))
    # Extreme angles are not a trustworthy yaw-drift reference for racing.
    if abs(yaw) > 70 or abs(pitch) > 60 or abs(roll) > 60:
        return WebcamSample(captured_at, reason="Face turned too far for reliable correction")
    return WebcamSample(captured_at, yaw, pitch, roll, True, "Face tracked")


class _OpenCVCapture:
    def __init__(self, index: int):
        import cv2
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError(f"Cannot open webcam {index}. Check Windows camera access and other camera apps.")
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.capture.set(cv2.CAP_PROP_FPS, 30)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Backend may ignore this; mailbox still stays bounded.

    def read(self):
        return self.capture.read()

    def close(self):
        self.capture.release()


class _MediaPipeDetector:
    def __init__(self, model_path: Path):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        self.cv2, self.mp = cv2, mp
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=2,
            min_face_detection_confidence=.7,
            min_face_presence_confidence=.7,
            min_tracking_confidence=.7,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)

    def detect(self, frame, timestamp_ms: int):
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        return self.landmarker.detect_for_video(image, timestamp_ms)

    def close(self):
        self.landmarker.close()


class WebcamProvider:
    def __init__(self, camera_index: int = 0, model_path=None, *,
                 _capture_factory=None, _detector_factory=None, _clock=time.monotonic):
        self.camera_index = camera_index
        self.model_path = Path(model_path) if model_path else default_model_path()
        self._capture_factory = _capture_factory or _OpenCVCapture
        self._detector_factory = _detector_factory or _MediaPipeDetector
        self._needs_model = _detector_factory is None
        self._clock = _clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame_ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._frame = None
        self._latest = None
        self._preview = None
        self._preview_enabled = False
        self.status = "Webcam off"
        self.error: str | None = None

    @property
    def running(self):
        return any(thread.is_alive() for thread in self._threads)

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._frame_ready.clear()
        with self._lock:
            self._frame = self._latest = self._preview = None
        self.error = None
        self.status = "Starting webcam"
        if self._needs_model:
            try:
                verify_model(self.model_path)
            except (OSError, RuntimeError) as exc:
                self._fail(str(exc))
                return
        self._threads = [threading.Thread(target=self._capture_loop, daemon=True, name="PodLook-camera"),
                         threading.Thread(target=self._inference_loop, daemon=True, name="PodLook-face")]
        for thread in self._threads:
            thread.start()

    def stop(self):
        self._stop.set()
        self._frame_ready.set()
        deadline = time.monotonic() + .75
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))
        with self._lock:
            self._frame = None
            self._preview = None
            self._latest = WebcamSample(self._clock(), reason="Webcam stopped")
        self.status = "Webcam stopping" if self.running else "Webcam off"

    def poll(self) -> WebcamSample | None:
        with self._lock:
            sample, self._latest = self._latest, None
        if sample is not None and sample.valid and self._clock() - sample.timestamp > .25:
            return WebcamSample(sample.timestamp, reason="Stale camera frame")
        return sample

    def enable_preview(self, enabled=True):
        with self._lock:
            self._preview_enabled = bool(enabled)
            if not enabled:
                self._preview = None

    def poll_preview(self):
        """Consume latest image only, independently of inference's mailbox."""
        with self._lock:
            preview, self._preview = self._preview, None
        if preview is None or self._clock() - preview[1] > 1:
            return None
        return preview

    def _publish(self, sample):
        with self._lock:
            self._latest = sample
        self.status = sample.reason

    def _fail(self, message):
        self.error = message
        self._publish(WebcamSample(self._clock(), reason=message))
        self._stop.set()
        self._frame_ready.set()

    def _capture_loop(self):
        camera = None
        try:
            camera = self._capture_factory(self.camera_index)
            while not self._stop.is_set():
                ok, frame = camera.read()
                captured_at = self._clock()
                if self._stop.is_set():
                    break
                if not ok or frame is None:
                    self._fail("Webcam stopped delivering frames. Check its connection and camera permissions.")
                    break
                with self._lock:
                    self._frame = (frame, captured_at)
                    if self._preview_enabled:
                        self._preview = (frame, captured_at)
                self._frame_ready.set()
        except ImportError:
            self._fail("Camera dependencies missing. Re-extract the complete Windows package; source users install windows/requirements.txt.")
        except Exception as exc:
            self._fail(f"Webcam error: {exc}")
        finally:
            if camera is not None:
                camera.close()

    def _inference_loop(self):
        detector = None
        previous_ms = -1
        try:
            detector = self._detector_factory(self.model_path)
            while not self._stop.is_set():
                self._frame_ready.wait(.1)
                self._frame_ready.clear()
                with self._lock:
                    frame, self._frame = self._frame, None
                if frame is None or self._stop.is_set():
                    continue
                image, captured_at = frame
                timestamp_ms = max(previous_ms + 1, int(captured_at * 1000))
                previous_ms = timestamp_ms
                result = detector.detect(image, timestamp_ms)
                if not self._stop.is_set():
                    self._publish(sample_from_result(result, captured_at, now=self._clock()))
        except ImportError:
            self._fail("Face tracking dependencies missing. Re-extract the complete Windows package; source users install windows/requirements.txt.")
        except Exception as exc:
            self._fail(f"Face tracking error: {exc}")
        finally:
            if detector is not None:
                detector.close()
