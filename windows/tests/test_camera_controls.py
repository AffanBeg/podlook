"""Camera selection, local preview lifecycle and bounded discovery tests."""
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webcam
import companion


class DiscoveryTests(unittest.TestCase):
    def test_scan_is_bounded_and_reports_only_readable_indexes(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if command[-1] == "1":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return SimpleNamespace(returncode=0 if command[-1] in ("0", "3") else 2)
        self.assertEqual(webcam.discover_cameras(runner=runner), [0, 3])
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(options["timeout"] == 3 for _, options in calls))

    def test_cancelled_scan_does_not_open_devices(self):
        cancel = threading.Event()
        cancel.set()
        with patch("subprocess.run") as runner:
            self.assertEqual(webcam.discover_cameras(cancel, runner=runner), [])
            runner.assert_not_called()

    def test_probe_releases_device_even_when_read_fails(self):
        camera = SimpleNamespace(read=lambda: (_ for _ in ()).throw(RuntimeError("read failed")), close=lambda: None)
        with patch.object(webcam, "_OpenCVCapture", return_value=camera), patch.object(camera, "close") as close:
            self.assertFalse(webcam.probe_camera(0))
            close.assert_called_once()


class PreviewMailboxTests(unittest.TestCase):
    def test_preview_consumption_does_not_consume_inference_mailbox(self):
        provider = webcam.WebcamProvider(_clock=lambda: 10)
        frame = object()
        provider.enable_preview()
        provider._frame = (frame, 10)
        provider._preview = (frame, 10)
        self.assertEqual(provider.poll_preview(), (frame, 10))
        self.assertIsNone(provider.poll_preview())
        self.assertEqual(provider._frame, (frame, 10))

    def test_stale_or_disabled_preview_is_discarded(self):
        provider = webcam.WebcamProvider(_clock=lambda: 10)
        provider._preview = (object(), 8)
        self.assertIsNone(provider.poll_preview())
        provider._preview = (object(), 10)
        provider.enable_preview(False)
        self.assertIsNone(provider.poll_preview())


class FakeProvider:
    def __init__(self, camera_index):
        self.index = camera_index
        self.running = False
        self.error = None
        self.preview = False
    def start(self): self.running = True
    def stop(self): self.running = False
    def enable_preview(self, enabled): self.preview = enabled


class CameraControlTests(unittest.TestCase):
    def test_camera_test_is_available_in_airpods_mode_without_network(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        app = companion.CompanionApp(root, smoke_test=True)
        self.addCleanup(app.close)
        self.assertEqual(app.mode.get(), "airpods")
        self.assertEqual(str(app.camera_selector.cget("state")), "readonly")
        app.camera_index.set("2")
        with patch.object(webcam, "WebcamProvider", FakeProvider):
            app.test_camera()
        camera = app.camera
        self.assertEqual(camera.index, 2)
        self.assertTrue(camera.running)
        self.assertTrue(camera.preview)
        self.assertIsNotNone(app.preview_window)
        self.assertIsNone(app.transport)
        self.assertIsNone(app.bridge)
        app._close_preview()
        self.assertIsNone(app.camera)
        self.assertFalse(camera.running)
        self.assertIsNone(app.preview_window)

    def test_start_tracking_releases_standalone_test_before_opening_hybrid_camera(self):
        import tkinter as tk
        import opentrack_bridge
        root = tk.Tk()
        root.withdraw()
        app = companion.CompanionApp(root, smoke_test=True)
        self.addCleanup(app.close)
        bridge = SimpleNamespace(start=lambda: "test bridge", stop=lambda: None)
        transport = SimpleNamespace(start=lambda: None, close=lambda: None)
        with patch.object(webcam, "WebcamProvider", FakeProvider):
            app.test_camera()
            old_camera = app.camera
            app.mode.set("hybrid")
            app._mode_changed()
            with patch.object(opentrack_bridge, "OpenTrackBridge", return_value=bridge), patch.object(companion, "PacketTransport", return_value=transport):
                app.start()
        self.assertFalse(old_camera.running)
        self.assertFalse(old_camera.preview)
        self.assertIsNot(app.camera, old_camera)
        self.assertTrue(app.camera.running)
        self.assertIs(app.transport, transport)
        active_camera = app.camera
        app.stop()
        self.assertFalse(active_camera.running)
        self.assertIsNone(app.camera)
        self.assertIsNone(app.preview_window)


if __name__ == "__main__":
    unittest.main()
