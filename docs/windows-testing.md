# Windows companion verification

This report separates automated software checks from tests that need the user's
AirPods, iPhone, webcam, home network and F1 25 installation. No automated result
here is an EA approval or a claim of measured head-tracking accuracy.

## Fusion core: executed locally

Twenty Python unit/behavioral tests passed on Windows after implementing the
fusion engine and immediate recenter shortcut. Command:

```powershell
python -m unittest discover -s tests -p test_fusion.py -v
```

Those tests exercise the actual `windows/fusion.py`, including:

- AirPods-only passthrough and center assist being disabled by default.
- Capturing separate, temporally aligned neutral references.
- Camera delay compensation without interpreting head motion as drift.
- Correction rate limits and preserving AirPods pitch/roll.
- Invalid camera data, no face, stale observations and camera recovery.
- Freezing bias when camera correction is unavailable or disabled.
- Returning neutral when AirPods data stops and requiring explicit reset.
- Immediate centering, including delayed-camera and lost-camera cases.
- Angle wrapping, malformed inputs, bounded buffers and configuration limits.

This exercises timestamped synthetic observations, not physical sensor behavior.
Camera matching uses host monotonic timestamps; those do not measure Bluetooth
latency or the webcam's exposure time. Real-device alignment still needs checking.

## Integration checks executed locally

- The complete root Python suite passed **45 tests** after the global-hotkey and
  real UDP-to-fusion integration tests were added. This includes actual loopback
  sockets and a separate receiver process, not only mocked networking.
- The source Tk GUI smoke test passed: its widgets initialized and closed without
  starting the camera or network.
- The webcam suite passed **18 tests** with the verified model and Google's public
  portrait fixture, including actual inference and face-loss handling.
- A seven-second check opened webcam index 0 and ran inference. It detected no
  face and reported invalid observations as designed, with no provider error.
  Stopping released the camera. No captured frames were saved. This verifies
  camera access and the no-face path, not positive tracking of the user.
- The Windows workflow YAML parsed successfully and its runner/permission
  structure passed local checks. A GitHub-hosted Windows run is still pending.

Executable packaging and its packaged GUI/model checks are in progress; the
source test results above must not be presented as packaged-executable results.

## Automated Windows workflow

`.github/workflows/windows.yml` installs pinned Python dependencies and performs:

1. A checksum-verified download of the versioned face model.
2. Root Python tests for packet handling, real loopback UDP, fusion, application
   lifecycle, OpenTrack runtime isolation and iOS packaging helpers.
3. Webcam provider tests and actual-model inference on a generated black frame.
4. A source GUI smoke test that opens no camera or network session.
5. A PyInstaller build with the verified model included for offline inference.
6. Packaged executable GUI and model self-tests.
7. An archive containing the entire executable folder and its dependencies.

The workflow logs are the authoritative record for its specific commit. Adding
this workflow does not establish that a cloud run has passed. The root test count
may grow as integration work continues; use the exact count in that run's log.

Optional portrait tests require `PODLOOK_TEST_PORTRAIT` to point to a local public
test image. They are skipped when no fixture is supplied. Such tests must never
silently open the user's webcam. `PODLOOK_TEST_MODEL` points to a hash-verified
`face_landmarker.task` file and is supplied by the CI job.

## Hardware checks still required

- Sideload the iPhone IPA and grant Motion and Local Network access.
- Confirm packets from real AirPods reach Windows port 4243.
- Verify right/up movements match the app and OpenTrack preview; adjust camera
  inversion only after checking its physical axes against AirPods.
- Check that the user's webcam can maintain timely, single-face detection in
  their normal seating position and lighting.
- Compare AirPods-only and hybrid behavior while looking forward, into a corner,
  and holding a deliberate glance. Confirm corrections remain gradual.
- Cover the webcam: AirPods tracking should continue with frozen correction.
- Stop phone motion or disconnect Wi-Fi: output should return to neutral, and
  require explicit recenter/restart after motion returns.
- Verify Alt+C works while F1 25 has focus.
- Test F1 25's cockpit camera with Head Tracking with TrackIR enabled.
- Check several minutes of drift, CPU load and subjective latency during a race.

PodLook's camera-validity checks are provider output and timing/geometry checks;
they are not an invented confidence percentage. AirPods-only center assist is
an optional heuristic and can mistake a sustained small glance for drift. Hybrid
camera correction remains limited to stable near-forward observations.
