# Verification status

This is a source prototype, not a verified release. No IPA has been built yet.

## Completed locally on Windows

All eight Python unit/integration tests passed after the source review.
Project generation is deterministic; packaging checks reject simulator apps and
missing executables and verify the IPA Payload structure and privacy declarations.

- Independent little-endian OpenTrack packet fixture: passed.
- Invalid length, nonfinite numbers, nonzero translation and out-of-range angles:
  rejected as expected.
- Real UDP loopback integration: passed, with one malformed packet rejected and
  three valid poses received by a separate process. The sandbox blocked loopback;
  the same test passed outside it using only 127.0.0.1.
- Independent source review: corrected Combine import, callback actor transitions,
  disconnect handling, and keyboard accessibility for UI tests.

## Prepared; must execute on the cloud Mac

- Swift tests compile and run the actual PoseCore.swift used by the iPhone app:
  quaternion validity, known and combined rotations, arbitrary neutral orientation,
  sign equivalence, singularities, yaw wrap, stale input, monotonic timestamps,
  recentering, time-based smoothing, and exact 48-byte encoding.
- Simulator UI test: launch, missing address, invalid port, no-headphones status.
- Simulator and arm64 iPhone compilation, privacy plist checks and IPA packaging.

## Must verify on your physical devices

1. IPA signs and installs through Sideloadly, then launches on your iPhone.
2. Motion and Local Network permission prompts work; Start receives AirPods samples.
3. Turn right, look up, tilt right: check the expected axes and adjust inversion.
4. Hold still, recenter, and check for objectionable jitter/drift over several minutes.
5. Windows listener receives 100 valid packets from the phone (not synthetic packets).
6. OpenTrack preview follows the same movements; center hotkey works from Windows.
7. F1 25 cockpit camera follows head movement with its TrackIR setting enabled.
8. Remove/disconnect AirPods; app stops and sends neutral if network is available.
9. Background the app; it stops. Return, Start, and recenter before driving again.
10. Disconnect Wi-Fi; confirm you can manually Stop/Center OpenTrack. UDP cannot
    deliver a neutral pose when the network or sender is gone.

Passing unit or simulator tests cannot prove sensor accuracy, network delivery on
your LAN, or F1 25 anti-cheat compatibility. This route uses normal OpenTrack input
and no custom Windows kernel driver; it has not been approved by EA.
