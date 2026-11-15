# iPhone motion recovery update (0.2.0, build 2)

This update addresses the app's failure to recover when a previously working
AirPods motion stream stops delivering samples. Bluetooth showing Connected does
not establish that Core Motion is delivering sensor updates.

- A fresh motion manager starts on each session. Callback delivery uses a serial
  worker queue. After five seconds without fresh samples, the app recreates the
  manager and tries Apple's polling API, up to three times.
- Duplicate sensor timestamps cannot keep a stalled stream alive or reset its
  retry budget. Old-manager callbacks are rejected after restart.
- Missing samples neutralize the local pose. The first recovered sample becomes
  the new center. Press Alt+C in Windows PodLook after recovery if its stale-input
  protection has latched the game output at neutral.
- Restart AirPods sensor provides a manual fresh attempt without replacing the
  existing UDP connection, preserving the Windows receiver's sender pin.
- A denied/restricted Motion permission stops tracking with an actionable error.
  The app does not consume retries while waiting for first-time permission.
- Sensor permission/availability/activity, received sample count, and network
  status are shown separately. UDP errors no longer stop local motion preview.
- New installations default to Windows companion port4243; existing settings
  remain saved. Direct-to-OpenTrack users can continue using4242.

Install the updated IPA with Sideloadly using the same Apple account and bundle
identifier. Keep the existing app until the replacement is signed and installed.
Version0.2.0 appears at the bottom of the app. Windows software cannot install an
unsigned IPA directly onto the phone.

Automated recovery-policy tests exercise startup silence and its grace period,
duplicate samples, stale transitions, retry bounds, recovery and reset. Simulator
tests verify controls and unavailable-hardware behavior. These tests cannot prove
that a particular AirPods/iOS connection will resume sensor delivery; check the
new permission/available/active details on the physical phone if it still fails.
