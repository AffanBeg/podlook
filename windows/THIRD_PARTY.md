# Webcam dependencies and model

The webcam provider uses MediaPipe 0.10.21 (Apache-2.0), OpenCV contrib Python
4.11.0.86 (OpenCV Apache-2.0; the Python wheel includes additional dependency
licenses), and NumPy 1.26.4 (BSD-3-Clause). Their distributions carry their full
licenses. Retain dependency license files when packaging executables.

The pretrained Face Landmarker float16 version 1 bundle is obtained from Google:

https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

SHA-256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`

Google's model cards identify the face models as Apache-2.0:

- https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf
- https://github.com/google-ai-edge/mediapipe-samples/issues/407
- https://www.apache.org/licenses/LICENSE-2.0

The unmodified model is a separate binary asset; it is not source code authored
by this project. It may be bundled in the local executable or installed outside
the repository in `%LOCALAPPDATA%/PodLook/models` by `download_model.py`.
No automatic download occurs when launching or starting the webcam provider.

API documentation: https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python

FaceLandmarker returns face landmarks and transformation matrices, but does not
expose an overall per-face confidence score. PodLook sets detection, presence,
and tracking thresholds to 0.7, rejects missing/multiple faces and invalid or old
transforms, and reports `confidence=None` rather than inventing a numeric score.
It requests two faces so an additional detected face invalidates the correction.
Frames are processed locally in memory and are not saved or uploaded.

Capture time is host monotonic receipt time from OpenCV, not hardware exposure
time. A one-frame mailbox prevents accumulation during slow inference; OS camera
driver buffering and inference latency still affect the measurement. Results older
than 250 ms are invalidated. Physical axis alignment must be checked at setup.
