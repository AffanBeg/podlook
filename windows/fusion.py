"""Timestamped AirPods orientation with optional, conservative yaw correction.

All poses are (yaw, pitch, roll) in degrees with matching axis signs. Camera
timestamps should identify capture time; webcam_latency compensates only when
the caller instead supplies arrival time. Camera validity must come from the
provider: UDP angles alone do not contain a detection-confidence measurement.

Automatic corrections never recenter or jump. Explicit calibrate/reset may do
so, and a stale AirPods stream latches neutral until one of those explicit calls.
"""
from collections import deque
from dataclasses import dataclass
import math
import threading
from typing import Optional, Sequence

Pose = tuple[float, float, float]
ZERO: Pose = (0.0, 0.0, 0.0)


def wrap(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _pose(value: Sequence[float]) -> Optional[Pose]:
    try:
        if len(value) != 3:
            return None
        result = tuple(float(x) for x in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) and abs(x) <= 180.001 for x in result):
        return None
    return tuple(wrap(x) for x in result)


def _time(value: float) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(value) and value >= 0


def _relative(a: Pose, b: Pose) -> Pose:
    return tuple(wrap(x - y) for x, y in zip(a, b))


def _mean(poses: Sequence[Pose]) -> Pose:
    # Stable windows span only a few degrees; unwrap around the first sample.
    first = poses[0]
    return tuple(wrap(first[i] + sum(wrap(p[i] - first[i]) for p in poses) / len(poses))
                 for i in range(3))


def _stable(poses: Sequence[Pose], tolerance: float = 2.0) -> bool:
    center = _mean(poses)
    return all(abs(wrap(p[i] - center[i])) <= tolerance for p in poses for i in range(3))


@dataclass(frozen=True)
class _Sample:
    time: float
    pose: Pose


@dataclass(frozen=True)
class FusionResult:
    pose: Pose
    status: str
    calibrated: bool
    webcam_used: bool
    bias_degrees: float


class FusionEngine:
    """Thread-safe pose fusion; sample() should run regularly (e.g. 60 Hz).

    `airpods` is passthrough until explicit calibration. Its optional center
    assist (OFF by default) slowly pulls small, stable yaw angles toward zero;
    this is a heuristic, not an independent measurement of drift.

    `hybrid` uses only valid, fresh, aligned, stable and near-forward camera
    observations. Correction is yaw-only. Disabling auto correction freezes
    the existing bias for continuity. Explicit calibration clears that bias.
    """
    def __init__(self, mode: str = 'airpods', webcam_latency: float = 0.0,
                 airpods_timeout: float = 0.25, webcam_timeout: float = 0.5,
                 center_assist: bool = False, auto_correction: Optional[bool] = None,
                 correction_rate: float = 0.75, correction_tau: float = 20.0):
        if mode not in ('airpods', 'hybrid'):
            raise ValueError('mode must be airpods or hybrid')
        if not math.isfinite(webcam_latency) or not 0 <= webcam_latency <= 0.5:
            raise ValueError('webcam latency must be 0 to 0.5 seconds')
        for name, value in [('airpods_timeout', airpods_timeout), ('webcam_timeout', webcam_timeout),
                            ('correction_rate', correction_rate), ('correction_tau', correction_tau)]:
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if correction_rate > 3:
            raise ValueError('correction_rate must not exceed 3 degrees per second')
        self._lock = threading.RLock()
        self.mode = mode
        self.webcam_latency = webcam_latency
        self.airpods_timeout = airpods_timeout
        self.webcam_timeout = webcam_timeout
        self.correction_rate = correction_rate
        self.correction_tau = correction_tau
        self.auto_correction = ((mode == 'hybrid' or center_assist) if auto_correction is None
                                else bool(auto_correction))
        self._window_seconds = 0.35
        self._match_tolerance = 0.06
        self.reset()

    def reset(self):
        """Explicit reset/start: forget samples, references, bias and stale latch."""
        with self._lock:
            self._air = deque(maxlen=512)
            self._camera = deque(maxlen=256)
            self._last_camera_observation = None
            self._camera_valid = False
            self._air_reference = ZERO
            self._camera_reference = None
            self._calibrated = False
            self._bias = 0.0
            self._air_lost = False
            self._last_output_time = None
            self._result = FusionResult(ZERO, 'waiting_airpods', False, False, 0.0)

    def set_mode(self, mode: str):
        if mode not in ('airpods', 'hybrid'):
            raise ValueError('mode must be airpods or hybrid')
        with self._lock:
            if mode != self.mode:
                self.mode = mode
                self.auto_correction = mode == 'hybrid'
                self.reset()

    def set_auto_correction(self, enabled: bool):
        with self._lock:
            self.auto_correction = bool(enabled)

    def set_webcam_latency(self, seconds: float):
        if not math.isfinite(seconds) or not 0 <= seconds <= 0.5:
            raise ValueError('webcam latency must be 0 to 0.5 seconds')
        with self._lock:
            if seconds != self.webcam_latency:
                self.webcam_latency = seconds
                self.reset()

    @staticmethod
    def _append(buffer, pose, timestamp):
        if buffer and timestamp <= buffer[-1].time:
            return False
        buffer.append(_Sample(timestamp, pose))
        while buffer and timestamp - buffer[0].time > 3.0:
            buffer.popleft()
        return True

    def push_airpods(self, pose: Sequence[float], timestamp: float) -> bool:
        parsed = _pose(pose)
        if parsed is None or not _time(timestamp):
            return False
        with self._lock:
            if self._air and timestamp - self._air[-1].time > self.airpods_timeout:
                self._air_lost = True
                self._calibrated = False
            return self._append(self._air, parsed, float(timestamp))

    def push_webcam(self, pose: Optional[Sequence[float]], timestamp: float, valid: bool = True) -> bool:
        """A valid=False observation immediately freezes correction, including
        when no face was detected. Pass None as its pose in that case."""
        if not _time(timestamp):
            return False
        parsed = _pose(pose) if valid else None
        with self._lock:
            if self._last_camera_observation is not None and timestamp <= self._last_camera_observation:
                return False
            self._last_camera_observation = float(timestamp)
            self._camera_valid = bool(valid and parsed is not None)
            if not self._camera_valid:
                self._camera.clear()
                return not valid  # Malformed valid=True samples fail closed.
            return self._append(self._camera, parsed, float(timestamp))

    def _history_window(self, buffer, end: float):
        start = end - self._window_seconds
        if not buffer or buffer[0].time > start:
            return None
        samples = [s for s in buffer if s.time <= end]
        if not samples:
            return None
        earlier = [s for s in samples if s.time <= start]
        recent = [s for s in samples if s.time > start]
        window = earlier[-1:] + recent
        if len(window) < 3 or window[-1].time - window[0].time < self._window_seconds * 0.95:
            return None
        # A long hole is not a stable stream, even if its endpoints agree.
        if any(b.time - a.time > 0.15 for a, b in zip(window, window[1:])):
            return None
        return window

    def _air_at(self, timestamp: float) -> Optional[Pose]:
        previous = None
        for sample in self._air:
            if sample.time >= timestamp:
                if previous is None:
                    return sample.pose if sample.time - timestamp <= self._match_tolerance else None
                if timestamp - previous.time > self._match_tolerance or sample.time - timestamp > self._match_tolerance:
                    return None
                fraction = (timestamp - previous.time) / (sample.time - previous.time)
                return tuple(wrap(a + fraction * wrap(b - a)) for a, b in zip(previous.pose, sample.pose))
            previous = sample
        if previous and timestamp - previous.time <= self._match_tolerance:
            return previous.pose
        return None

    def _pairs(self, now: float):
        if not self._camera_valid or not self._camera:
            return None
        camera = self._camera[-1]
        if not 0 <= now - camera.time <= self.webcam_timeout:
            return None
        window = self._history_window(self._camera, camera.time)
        if window is None:
            return None
        pairs = []
        for item in window:
            air = self._air_at(item.time - self.webcam_latency)
            if air is None:
                return None
            pairs.append((air, item.pose))
        return pairs

    def calibrate(self, now: float) -> bool:
        """Explicitly center using stable observations; returns False until ready.
        In hybrid mode it captures the temporally aligned neutral pair. A
        successful manual calibration also clears a previously stale latch."""
        if not _time(now):
            return False
        with self._lock:
            if not self._air or not 0 <= now - self._air[-1].time <= self.airpods_timeout:
                return False
            current = self._history_window(self._air, self._air[-1].time)
            if current is None or not _stable([s.pose for s in current]):
                return False
            if self.mode == 'hybrid':
                pairs = self._pairs(now)
                if not pairs or not _stable([a for a, _ in pairs]) or not _stable([c for _, c in pairs]):
                    return False
                camera_mean = _mean([c for _, c in pairs])
                if any(abs(v) > bound for v, bound in zip(camera_mean, (35, 25, 25))):
                    return False
                self._air_reference = _mean([a for a, _ in pairs])
                self._camera_reference = camera_mean
            else:
                self._air_reference = _mean([s.pose for s in current])
                self._camera_reference = None
            self._calibrated = True
            self._air_lost = False
            self._bias = 0.0
            self._last_output_time = float(now)
            return True

    def recenter(self, now: float) -> bool:
        """Immediately center fresh AirPods (for a deliberate keyboard shortcut).

        A fresh aligned camera pair preserves hybrid calibration. Otherwise the
        app still centers, but needs full calibration before camera correction
        resumes. Never reuse an old camera neutral that could undo this action.
        """
        if not _time(now):
            return False
        with self._lock:
            if not self._air or not 0 <= now - self._air[-1].time <= self.airpods_timeout:
                return False
            current = self._air[-1].pose
            self._air_reference = current
            self._camera_reference = None
            self._calibrated = self.mode == 'airpods'
            if self.mode == 'hybrid' and self._camera_valid and self._camera:
                camera = self._camera[-1]
                aligned = self._air_at(camera.time - self.webcam_latency)
                if (0 <= now - camera.time <= self.webcam_timeout and aligned is not None
                        and all(abs(v) <= b for v, b in zip(camera.pose, (35, 25, 25)))):
                    # Carry camera orientation forward by the actual AirPods
                    # motion since capture, so a late camera frame cannot undo
                    # the new center immediately after the shortcut.
                    self._camera_reference = tuple(wrap(c + wrap(a - old))
                                                   for c, a, old in zip(camera.pose, current, aligned))
                    self._calibrated = True
            self._bias = 0.0
            self._air_lost = False
            self._last_output_time = float(now)
            return True

    def _correct(self, target: float, dt: float, rate: float, tau: float):
        error = wrap(target - self._bias)
        delta = error * (1 - math.exp(-dt / tau))
        bound = rate * dt
        self._bias = max(-30.0, min(30.0, self._bias + max(-bound, min(bound, delta))))

    def sample(self, now: float) -> FusionResult:
        if not _time(now):
            return FusionResult(ZERO, 'invalid_time', False, False, 0.0)
        with self._lock:
            if self._last_output_time is not None and now < self._last_output_time:
                return self._result
            dt = min(0.1, max(0, now - self._last_output_time)) if self._last_output_time is not None else 0
            self._last_output_time = float(now)
            if not self._air:
                return self._emit(ZERO, 'waiting_airpods')
            age = now - self._air[-1].time
            if age > self.airpods_timeout:
                self._air_lost = True
                self._calibrated = False
            if self._air_lost:
                return self._emit(ZERO, 'airpods_lost_reset_required')
            if age < 0:
                return self._emit(ZERO, 'waiting_airpods')
            raw = _relative(self._air[-1].pose, self._air_reference)
            status, used = 'airpods_only', False
            current = self._history_window(self._air, self._air[-1].time)
            stationary = current is not None and _stable([s.pose for s in current])
            if not self.auto_correction:
                status = 'correction_off'
            elif self.mode == 'airpods':
                if self._calibrated and stationary and all(abs(v) <= b for v, b in zip(raw, (8, 12, 12))):
                    self._correct(-raw[0], dt, rate=0.15, tau=120.0)
                    status = 'center_assist'
            elif not self._calibrated or self._camera_reference is None:
                status = 'hybrid_uncalibrated'
            elif not self._camera_valid or not self._camera or now - self._camera[-1].time > self.webcam_timeout:
                status = 'webcam_lost_airpods_fallback'
            else:
                pairs = self._pairs(now)
                if not pairs:
                    status = 'webcam_unaligned_airpods_fallback'
                else:
                    air = _mean([a for a, _ in pairs])
                    camera = _mean([c for _, c in pairs])
                    camera_relative = _relative(camera, self._camera_reference)
                    air_relative = _relative(air, self._air_reference)
                    near_forward = all(abs(v) <= b for v, b in zip(camera_relative, (25, 20, 20)))
                    target = wrap(camera_relative[0] - air_relative[0])
                    reliable = (stationary and near_forward and _stable([a for a, _ in pairs])
                                and _stable([c for _, c in pairs]) and abs(wrap(target - self._bias)) <= 20)
                    if reliable:
                        self._correct(target, dt, self.correction_rate, self.correction_tau)
                        status, used = 'hybrid_correcting', True
                    else:
                        status = 'motion_gate_airpods_fallback'
            output = (wrap(raw[0] + self._bias), raw[1], raw[2])
            return self._emit(output, status, used)

    def _emit(self, pose, status, used=False):
        self._result = FusionResult(pose, status, self._calibrated, used, self._bias)
        return self._result
