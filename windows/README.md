# PodLook for Windows

PodLook receives AirPods motion from the iPhone app and sends head rotation to
OpenTrack. Optional webcam observations slowly correct yaw drift. The webcam does
not replace the iPhone or AirPods in this version.

AirPods → iPhone PodLook → PC UDP **4243** → Windows PodLook → OpenTrack UDP **4242** → game.

## First use

Version 0.2 shows **Choose camera**, **Find cameras**, and **Test webcam** at the
top of the Windows app, even in AirPods-only mode. Find cameras explicitly probes
indexes 0–8 with an eight-second limit per index (up to 72 seconds total). This
allows for packaged-app startup and camera warmup. If a scan cannot confirm a
camera, choose Camera 0 and try Test webcam directly. Labels use OpenCV camera numbers
because device names cannot reliably be matched to those indexes. Test webcam
opens a live local preview without needing the iPhone or OpenTrack. Closing that
test preview releases the camera. During hybrid tracking, closing the preview
only hides it; **Stop** releases the camera. Frames are never recorded or uploaded.

1. Extract the entire Windows package into a writable folder. Keep `PodLook.exe`
   and its supporting folders together. Install OpenTrack if it is not already
   installed. Close other OpenTrack windows before starting PodLook.
2. Connect the AirPods to the iPhone and wear them. Put the phone and PC on the
   same trusted network. Launch `PodLook.exe`, select **AirPods only**, and press
   **Start tracking**. PodLook launches its own copy of OpenTrack with a dedicated
   profile. Existing installed OpenTrack profiles are not edited.
3. In the iPhone PodLook app, enter the PC IPv4 address shown in the Windows app
   and port **4243**. If several addresses appear, use the active Wi-Fi or Ethernet
   adapter shown by `ipconfig`. Start phone motion while facing forward. Allow
   Motion & Fitness and Local Network access on the phone and private-network
   access on Windows if prompted. Keep the iPhone app open.
4. Check that the Windows valid-packet count increases. Face forward, click
   **Calibrate in 3 seconds**, and hold still through the countdown. The live
   angles and OpenTrack preview should respond to your head movements.
5. Test left/right and up/down separately before driving. If the AirPods output
   itself is reversed, change the corresponding direction switch on the iPhone,
   then stop/start and calibrate the Windows session again.
6. In F1 25, enable its **Head Tracking with TrackIR** option and try the cockpit
   camera. Set **Horizontal** and **Vertical** sensitivity in PodLook. Head tilt
   (roll) is optional and off by default. Physical leaning is not measured.

The phone's direct-to-OpenTrack setup uses 4242; this Windows companion needs the
phone on **4243** instead. No router port forwarding is needed. AirPods remain
connected to the phone, so use PC speakers or another headset for game audio.

## Webcam correction

Stop tracking, select **AirPods + webcam**, then start again. Camera index 0 is
usually the default; try another index only if the wrong camera opens. Close
other apps using the webcam. Place it near the monitor, with your face clearly
visible and no second face in view. Face forward and calibrate.

Before enabling correction for driving, check the axis directions. Temporarily
turn off **Automatic yaw correction from webcam**, set both sensitivity sliders
to 1.00×, and calibrate while looking forward. Slowly turn your head a little
left and then right. The webcam yaw shown in its status should change in the
same direction as the live AirPods yaw. Compare pitch with a small up/down nod
too; compare changes from neutral, since the webcam's raw zero may differ.
If a direction disagrees, stop tracking, change **Invert webcam yaw** or
**Invert webcam pitch**, start and calibrate again. Then enable correction.
Neutral calibration alone cannot detect opposing axis directions.

Correction is deliberately slow and only changes yaw. It pauses during turns,
large disagreement, poor timing, missing faces or multiple faces. Pitch and roll
continue to come from AirPods. If the webcam loses your face, AirPods tracking
continues with the existing correction held constant. Recalibrate after moving
the seat or webcam. Switching automatic correction off holds the current offset
to avoid a jump; calibration clears it.

**Center assist** in AirPods-only mode is different: it estimates forward from
small, steady angles without a webcam measurement. It is off by default and can
misinterpret a sustained side view. Leave it off if you want only manual centering.

## Centering, stopping and reconnecting

- **Alt+C** centers immediately while the app is running, including when the game
  is focused. Face forward first. If the shortcut is unavailable, use the
  calibration button. If no usable webcam observation accompanies an immediate
  center, hybrid correction waits for a full calibration.
- If AirPods packets stop for more than 0.25 seconds, Windows sends a neutral
  pose and requires a deliberate center/calibration or restart after recovery.
- The first valid phone IP address and source port are pinned for each session.
  If the phone app restarts or its network changes, **Stop** and **Start tracking**
  in Windows to accept the new sender.
- **Stop** sends neutral, closes the phone receiver and stops the OpenTrack
  process launched by PodLook. It also stops the webcam; a slow camera driver may
  take longer to release. A new session is blocked while that driver is stopping.
- Stop before changing tracking modes or webcam direction settings. Preferences
  are saved on normal exit in `%LOCALAPPDATA%\PodLook\settings.json`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No valid phone packets | Check the PC address and phone port 4243; keep the phone app open. Check phone permissions, Windows private-network firewall access, VPN routing and guest Wi-Fi isolation. |
| Other-source packets ignored | Stop/start Windows tracking after restarting the phone app. |
| Port 4242 is in use | Stop a separately opened OpenTrack instance and retry. |
| OpenTrack receiver does not start | Inspect its taskbar window. Check for a startup error and retry after closing other instances. OpenTrack must be installed or present in the supplied runtime folder. |
| Calibration fails | Wait for fresh phone motion, face forward and hold still. Hybrid mode also needs one clearly visible face and timely webcam observations. |
| Webcam missing or denied | Close other camera apps; check Windows camera access for desktop apps; try the correct camera index. AirPods-only mode does not use the camera. |
| Head movement reverses or drifts faster in hybrid | Disable correction and perform the direction comparison above; then recalibrate. |
| OpenTrack moves but the game does not | Check the game's head-tracking option, active camera and OpenTrack output settings. A working packet/preview test does not prove game compatibility. |

Frames are processed locally in memory. This code does not save or upload camera
footage. The normal packaged runtime needs no model download; source users can
explicitly download the pinned model as described below. Camera timestamps mark
host receipt of frames, not hardware exposure. Phone timestamps mark packet
processing, not sensor capture. These limitations are why correction uses stable
observations and conservative gates. Runtime unit tests cannot establish real
AirPods direction, webcam latency, F1 compatibility or anti-cheat acceptance.

## Run and test from source

Use Windows x64 and Python 3.12. From the project root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r windows\requirements.txt
.venv\Scripts\python.exe windows\download_model.py
.venv\Scripts\python.exe windows\companion.py
```

The explicit downloader verifies the model checksum before installing it into
`%LOCALAPPDATA%\PodLook\models`. Do not install `opencv-python` alongside the
pinned `opencv-contrib-python` wheel. See `THIRD_PARTY.md` for model and library
sources and licenses.

Checks that do not open a webcam:

```powershell
.venv\Scripts\python.exe -m unittest discover -s windows\tests -v
.venv\Scripts\python.exe windows\companion.py --smoke-test
.venv\Scripts\python.exe windows\companion.py --model-self-test
```

The GUI smoke test creates and closes the window without starting tracking. The
model self-test verifies the model and performs real inference on a blank image.
Optional actual-model unit tests run when `PODLOOK_TEST_MODEL` points to the local
model file. Setting `PODLOOK_TEST_PORTRAIT` to Google's public `portrait.jpg`
fixture additionally exercises real single-face, multiple-face and face-loss
inference. Tests never download fixtures or access the webcam.

The packaged executable accepts the same two self-test flags. A windowed build
may not display console text; inspect its process exit code (zero means success).
Neither check starts the phone receiver, OpenTrack or camera.
