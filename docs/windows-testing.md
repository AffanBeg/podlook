# Windows companion verification

## Version 0.2 update

- All 48 root tests and all 26 webcam/controller/model tests passed locally.
- The packaged executable passed GUI and actual-model self-tests (exit 0).
- Desktop testing verified discovery of readable cameras, the visible camera picker, standalone Test webcam,
  live camera preview, and closing the preview to stop the camera. No video was
  recorded. Physical face tracking and AirPods alignment still require the user.
- [Windows CI run 43675212003](https://github.com/AffanBeg/podlook/actions/runs/43675212003)
  passed all build, test, packaging and artifact steps. Its two optional portrait
  tests skip; they both passed locally using the public fixture.
- The old Windows folder was retained locally as a backup and saved sensitivity
  settings were preserved. The current app identifies itself as PodLook 0.2.
- A packaged scan originally missed a valid camera because its three-second
  timeout was too short for a cold start. The final build uses an eight-second
  child-process limit and bounded frame warmup. The hardware scan passed after
  this fix. An inconclusive scan preserves manual camera choices.
- The final 0.2 ZIP passed integrity checking (2,661 files); its SHA-256 is
  `9129aa5a459c3de9fb9d586a444af1a4f26eadd132cec05fea045daa1fa8e056`.

The sections below record the initial 0.1 verification and its original hashes.

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

- The complete root Python suite passed **48 tests** after the global-hotkey, IPv6 listener and
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
  structure passed local checks. [GitHub Windows run 43665543315](https://github.com/AffanBeg/podlook/actions/runs/43665543315) passed all steps, including packaging and both executable self-tests. Its webcam suite runs 16 tests and explicitly skips the two optional portrait-fixture tests; the full 18 passed locally with that public fixture.

The local executable also passed GUI initialization and actual bundled-model
inference self-tests (both exit 0). Desktop interaction verified both mode
selections, automatic OpenTrack startup, webcam no-face reporting, and Stop.
A synthetic yaw 10 / pitch 5 pose sent through the executable appeared as exactly
10 / 5 in OpenTrack's raw and game output. Alt+C while OpenTrack had focus
centered the companion output to zero. The real game was not running during this
check. The original OpenTrack profile was not edited.

The first macOS regression run exposed immediate-send/read assumptions in three
network tests. Commit a2046571b58375b5242b3f31fb21463f6a61ab4d corrects the tests
to wait for bounded packet delivery; all 48 root tests passed again locally.
Production code is unchanged by this test-only fix.
The [macOS/iPhone regression run 43665543330](https://github.com/AffanBeg/podlook/actions/runs/43665543330)
then passed every step: Swift checks, Python regression tests, simulator UI test,
and unsigned device IPA build. The iPhone app itself was unchanged.

The final local ZIP includes dependency license notices and passes a complete ZIP
CRC check (2,661 files). SHA-256:
`330ee0982252cb384d6c4ce7a7e1ef70a8a79087d651ad05280a7b2ed344e83e`.
The license-copy packaging helper was executed locally after the successful CI
build; it does not change the executable. The executable SHA-256 is
`4e3c53618f8d424d5f478cfa48e569720e12470412bca6790cf9b44889e0854b`.

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

- Keep the already-installed iPhone app; no new IPA is required for this companion.
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
