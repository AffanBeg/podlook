# PodLook — AirPods head tracking for a Windows PC

Free, original iPhone companion source inspired by MousePods. It uses Apple's public
Core Motion API, so no MagicAAP driver or Windows Test Mode is required.

**iPhone 0.2.0:** bounded recovery for silent AirPods motion, a manual sensor restart,
and separate sensor/network diagnostics. See the [update notes](docs/iphone-motion-recovery.md).

**Windows companion:** choose AirPods-only or AirPods + webcam, control sensitivity,
recenter with Alt+C, and launch OpenTrack from one app. Follow the
[Windows setup guide](windows/README.md) and [Windows test evidence](docs/windows-testing.md).
The existing iPhone app remains compatible: change its destination port to **4243**
when using the companion. The original direct-to-OpenTrack route below uses 4242.

The original iPhone-to-OpenTrack route has been confirmed working in F1 25 by the
user. New hybrid correction still requires physical direction/calibration checks.
See [iOS build test evidence](TESTING.md).

AirPods Pro 2 → iPhone (PodLook) → local Wi-Fi UDP → Windows (OpenTrack) → F1 25.

## Build and install without owning a Mac

The included GitHub Actions workflow builds on a standard hosted Mac. It runs the
Swift core tests, checks the iOS app, and packages a device build into an unsigned
IPA. Public-repository standard runner compute is free; private repositories use
the account's included allowance. Artifact storage also has quotas. Do not enable
paid overages for this project. The artifact retention is one day.

1. Place this folder's contents in your own GitHub repository, including `.github`.
2. Run the **Build and test PodLook** workflow under Actions (or push to main/master).
3. Only use the IPA from a successful run. Download and extract the artifact.
4. Connect the iPhone by USB and trust the PC. In Sideloadly, select the IPA and
   sign it using your Apple account. Enter Apple credentials in Sideloadly yourself.
5. Trust your developer entry under Settings → General → VPN & Device Management.
   Enable Settings → Privacy & Security → Developer Mode when prompted.
6. With a free Apple account, refresh the app's signature every seven days.
   Sideloadly can automate refresh when the PC and phone are reachable.

An unsigned IPA is a build output, not an already installable or Apple-reviewed app.
This project does not bundle or require Apple credentials, certificates, or profiles.

## Original direct-to-OpenTrack setup (without Windows companion)

1. Install OpenTrack from https://github.com/opentrack/opentrack/releases/latest.
2. Set Input to **UDP over network**, port **4242**. Set Output to **freetrack 2.0
   Enhanced**, with **Enable both**. Start with the **Accela** filter.
3. Put the PC and iPhone on the same trusted home network. On Windows, run `ipconfig`
   and find the IPv4 address of the active Wi-Fi/Ethernet adapter. Enter it in PodLook.
   Guest-network isolation, a VPN, or Windows Firewall can prevent receipt. If
   Windows asks about OpenTrack network access, allow it on your private home network.
   No router port forwarding is needed.
4. Connect the AirPods to the iPhone and wear them. Open PodLook, tap Start, and allow
   Motion & Fitness and Local Network permissions. Face forward for the first sample.
5. Confirm the OpenTrack preview responds to head movement. Its raw translation
   values should remain zero. If left/right or up/down is reversed, use PodLook's
   direction switches. Recenter while looking forward. Set a Center shortcut in
   OpenTrack so you can recenter from the PC while driving.
6. Enter a driving session in F1 25, pause, and enable Settings → Eye and Head Tracking → Head Tracking with
   TrackIR. Try the cockpit camera. Adjust response curves in OpenTrack; tune gently
   and test yaw/pitch separately before combining them. Roll is off by default.

The app reads head rotation, not physical leaning. Use PC speakers or another
headset for game audio while AirPods are connected to the iPhone.

## Limitations

- Keep PodLook open. It disables auto-lock only while running and stops on background.
- Missing motion for over one second sends neutral while the app is active. Recovery
  recenters automatically. If the app/phone/network stops completely, it cannot send
  neutral: OpenTrack may hold the last pose. Use OpenTrack's Stop/Center shortcut.
- "Sending" and the packet counter mean iOS accepted UDP sends, not that the PC
  received them. Verify the preview or use the listener below.
- Actual AirPods axis directions, latency, drift and game compatibility require a
  physical test. Simulator and unit tests do not establish those results.
- No camera, microphone, background audio, Bluetooth scanning, login, analytics, or
  internet service is used. Motion packets go unencrypted to the IP you enter; use
  a trusted network. Do not expose the UDP port to the internet.

## Tests and troubleshooting

Run `python -m unittest discover -s tests -p "test_*.py" -v` for independent packet
validation and an actual UDP loopback integration test. This does not test iOS APIs.

On a Mac, run `mkdir -p build && swiftc ios/PodLook/PoseCore.swift tests/main.swift
-o build/core-tests && build/core-tests` (as one line). The CI workflow runs these
same Swift tests against the real motion/encoding code used by the app.

To check the phone-to-PC connection, stop OpenTrack and run:

```powershell
python tools/check_udp.py --count 100 --timeout 30
```

Start PodLook. The listener prints source IP and head angles and passes after 100
valid packets. Stop the listener before restarting OpenTrack; both use UDP 4242.
This tool checks data shape and receipt, not physical angle accuracy.

## References

- Inspiration: https://github.com/starman-tech/mousepods (no source copied).
- Apple motion API: https://developer.apple.com/documentation/coremotion/cmheadphonemotionmanager
- OpenTrack wire protocol: https://github.com/opentrack/opentrack/blob/master/tracker-udp/ftnoir_tracker_udp.cpp
- F1 25 setting: https://headtrack.app/games/f1-25/
- Sideloadly setup/refresh: https://sideloadly.io/faq.html
- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
