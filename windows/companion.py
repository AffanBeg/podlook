"""PodLook Windows companion. The GUI uses only Python's standard library.

Optional webcam dependencies are loaded only when hybrid tracking starts.
The phone sends OpenTrack-format UDP to 4243; game output stays on loopback:4242.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import queue
from typing import NamedTuple

PACKET = struct.Struct("<6d")
NEUTRAL_PACKET = PACKET.pack(0, 0, 0, 0, 0, 0)

STATUS_TEXT = {
    "waiting_airpods": "Waiting for iPhone motion on UDP 4243.",
    "airpods_lost_reset_required": "AirPods motion stopped. Restore the phone connection, then press Alt+C or restart tracking.",
    "airpods_only": "Tracking with AirPods. Automatic correction is off.",
    "center_assist": "Tracking with AirPods and slow center assist.",
    "correction_off": "Tracking with AirPods. Automatic correction is off.",
    "hybrid_uncalibrated": "AirPods tracking is active. Look forward and calibrate to enable webcam correction.",
    "webcam_lost_airpods_fallback": "Webcam cannot see a usable face. Continuing with AirPods; correction is paused.",
    "webcam_unaligned_airpods_fallback": "Webcam and AirPods timing do not align. Continuing with AirPods; correction is paused.",
    "motion_gate_airpods_fallback": "AirPods tracking active. Webcam correction pauses during turns or uncertain alignment.",
    "hybrid_correcting": "Tracking with AirPods and slow webcam yaw correction.",
    "invalid_time": "Timing error. Stop and restart tracking.",
}


def preferences_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PodLook" / "settings.json"


def read_preferences(path=None):
    try:
        data = json.loads((path or preferences_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {}
    if data.get("mode") in ("airpods", "hybrid"):
        clean["mode"] = data["mode"]
    for key in ("auto", "roll", "camera_invert_yaw", "camera_invert_pitch"):
        if type(data.get(key)) is bool:
            clean[key] = data[key]
    for key in ("yaw_gain", "pitch_gain"):
        value = data.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0.25 <= value <= 3:
            clean[key] = value
    if type(data.get("camera_index")) is int and 0 <= data["camera_index"] <= 8:
        clean["camera_index"] = data["camera_index"]
    return clean


class CenterHotkey:
    """Register Alt+C on the GUI thread. Poll only WM_HOTKEY, not other GUI input."""
    identifier = 0x504F

    def __init__(self):
        self.registered = False
        self.message = "Alt+C global center is available on Windows."
        self.user32 = None
        self.events = queue.SimpleQueue()
        self.stopping = threading.Event()
        self.initialized = threading.Event()
        self.thread = None
        if sys.platform != "win32":
            return
        self.thread = threading.Thread(target=self._worker, name="PodLook hotkey", daemon=True)
        self.thread.start()
        self.initialized.wait(0.5)

    def _worker(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.registered = bool(self.user32.RegisterHotKey(None, self.identifier, 0x4001, 0x43))
        self.message = "Alt+C: center immediately while looking forward." if self.registered else "Alt+C is unavailable (another app may use it). Use Calibrate instead."
        self.initialized.set()
        message = self.wintypes.MSG()
        try:
            while self.registered and not self.stopping.wait(0.01):
                # A dedicated thread owns the registration and its message queue,
                # so Tk cannot consume WM_HOTKEY before our polling callback.
                for _ in range(8):
                    if not self.user32.PeekMessageW(self.ctypes.byref(message), -1, 0x0312, 0x0312, 1):
                        break
                    if message.wParam == self.identifier:
                        self.events.put(True)
        finally:
            if self.registered:
                self.user32.UnregisterHotKey(None, self.identifier)
            self.registered = False

    def pressed(self):
        pressed = False
        for _ in range(8):
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
            pressed = True
        return pressed

    def close(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(0.5)


class ReceivedPose(NamedTuple):
    pose: tuple[float, float, float]
    timestamp: float


def decode_airpods(data: bytes) -> tuple[float, float, float]:
    if len(data) != PACKET.size:
        raise ValueError("AirPods packet must be exactly 48 bytes")
    values = PACKET.unpack(data)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Nonfinite pose")
    if any(value != 0 for value in values[:3]):
        raise ValueError("Expected rotation-only PodLook input")
    if any(abs(value) > 180.001 for value in values[3:]):
        raise ValueError("Angle outside expected range")
    return values[3:]


def shape_output(pose, yaw_gain=1.0, pitch_gain=1.0, include_roll=False):
    """Apply display gains after fusion; never feed amplified values into fusion."""
    values = tuple(pose)
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("Invalid output pose")
    if not all(math.isfinite(value) and 0.1 <= value <= 4 for value in (yaw_gain, pitch_gain)):
        raise ValueError("Gains must be finite and between 0.1 and 4")
    yaw = max(-180.0, min(180.0, values[0] * yaw_gain))
    pitch = max(-90.0, min(90.0, values[1] * pitch_gain))
    roll = max(-90.0, min(90.0, values[2])) if include_roll else 0.0
    return yaw, pitch, roll


class PacketTransport:
    """Nonblocking, bounded UDP transport with explicit session source pinning.

    A sender is pinned only after a valid packet and stays pinned until close.
    Datagram source timestamps are unavailable in the iPhone's standard packet:
    timestamp is monotonic arrival-processing time, never a claimed sensor time.
    """
    def __init__(self, input_port=4243, output_port=4242, bind_address="0.0.0.0", drain_limit=128):
        if not 0 <= input_port <= 65535 or not 1 <= output_port <= 65535:
            raise ValueError("Invalid UDP port")
        if input_port and input_port == output_port:
            raise ValueError("Input and output ports must differ")
        if not 1 <= drain_limit <= 4096:
            raise ValueError("Invalid drain limit")
        self.input_port, self.output_port = input_port, output_port
        self.bind_address, self.drain_limit = bind_address, drain_limit
        self.receiver = None
        self.output = None
        self.sender = None
        self.received = self.rejected = self.foreign = self.overloaded = 0

    def start(self):
        if self.receiver is not None:
            raise RuntimeError("Transport already started")
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        output = None
        try:
            # Never enable SO_REUSEADDR: on Windows that can steal datagrams from
            # another listener. Exclusive binding also provides a clear conflict.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                receiver.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
            receiver.bind((self.bind_address, self.input_port))
            receiver.setblocking(False)
            output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            output.setblocking(False)
        except Exception:
            receiver.close()
            if output is not None:
                output.close()
            raise
        self.receiver, self.output = receiver, output
        self.input_port = receiver.getsockname()[1]
        self.sender = None
        self.received = self.rejected = self.foreign = self.overloaded = 0

    def drain(self, now=None):
        if self.receiver is None:
            return None
        timestamp = time.monotonic() if now is None else now
        if not math.isfinite(timestamp):
            raise ValueError("Invalid timestamp")
        newest = None
        for _ in range(self.drain_limit):
            try:
                data, sender = self.receiver.recvfrom(65535)
            except BlockingIOError:
                return newest
            if self.sender is not None and sender != self.sender:
                self.foreign += 1
                continue
            try:
                pose = decode_airpods(data)
            except ValueError:
                self.rejected += 1
                continue
            if self.sender is None:
                self.sender = sender
            self.received += 1
            newest = ReceivedPose(pose, timestamp)
        # With a saturated input queue, don't represent a backlog as fresh data.
        # Keep event-loop work bounded; sustained overload becomes stale in fusion.
        self.overloaded += 1
        return None

    def send(self, pose):
        values = tuple(pose)
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise ValueError("Invalid output pose")
        if self.output is None:
            raise RuntimeError("Transport is not started")
        self.output.sendto(PACKET.pack(0, 0, 0, *values), ("127.0.0.1", self.output_port))

    def close(self):
        if self.output is not None:
            try:
                self.output.sendto(NEUTRAL_PACKET, ("127.0.0.1", self.output_port))
            except OSError:
                pass
            self.output.close()
        if self.receiver is not None:
            self.receiver.close()
        self.output = self.receiver = None
        self.sender = None


def local_addresses():
    try:
        candidates = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        return sorted(ip for ip in candidates if not ip.startswith("127."))
    except OSError:
        return []


class CompanionApp:
    def __init__(self, root, smoke_test=False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.smoke_test = smoke_test
        prefs = {} if smoke_test else read_preferences()
        self.hotkey = None if smoke_test else CenterHotkey()
        self.transport = self.engine = self.camera = self.bridge = None
        self.stopping_camera = None
        self.preview_window = self.preview_label = self.preview_photo = None
        self.last_preview_at = 0.0
        self.scan_thread = None
        self.scan_cancel = threading.Event()
        self.scan_results = queue.SimpleQueue()
        self.calibrate_at = None
        self.last_ui_update = 0.0
        self.last_packet = None
        self.bridge_started_at = 0.0
        self.last_bridge_check = 0.0
        self.bridge_ready = False
        self.mode = tk.StringVar(value=prefs.get("mode", "airpods"))
        self.auto = tk.BooleanVar(value=prefs.get("auto", self.mode.get() == "hybrid"))
        self.roll = tk.BooleanVar(value=prefs.get("roll", False))
        self.yaw_gain = tk.DoubleVar(value=prefs.get("yaw_gain", 1.0))
        self.pitch_gain = tk.DoubleVar(value=prefs.get("pitch_gain", 1.0))
        self.camera_index = tk.StringVar(value=str(prefs.get("camera_index", 0)))
        self.camera_choice = tk.StringVar(value=f"Camera {self.camera_index.get()}")
        self.camera_invert_yaw = tk.BooleanVar(value=prefs.get("camera_invert_yaw", False))
        self.camera_invert_pitch = tk.BooleanVar(value=prefs.get("camera_invert_pitch", False))
        self.status = tk.StringVar(value="Ready. Start tracking, then connect the iPhone app.")
        self.camera_status = tk.StringVar(value="Webcam is off in AirPods-only mode.")
        self.stats = tk.StringVar(value="Waiting for a phone — UDP input 4243")
        self.angles = tk.StringVar(value="YAW   0.0°       PITCH   0.0°       ROLL   0.0°")
        self.calibration_status = tk.StringVar(value="Look forward and calibrate after motion arrives.")
        self.auto_label = tk.StringVar(value="Automatic yaw correction from webcam" if self.mode.get() == "hybrid" else "Center assist (slow estimate)")
        self.gain_label = tk.StringVar()
        self._build()
        self._set_camera_controls()
        self._gains_changed()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(20, self._tick)

    def _build(self):
        tk, ttk, root = self.tk, self.ttk, self.root
        root.title("PodLook 0.2 · AirPods head tracking")
        root.geometry(f"760x{min(960, max(650, root.winfo_screenheight() - 160))}")
        root.minsize(700, 650)
        root.configure(bg="#101820")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#101820")
        style.configure("TLabelframe", background="#101820", foreground="#edf4f7")
        style.configure("TLabelframe.Label", background="#101820", foreground="#7fdec6", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background="#101820", foreground="#edf4f7", font=("Segoe UI", 10))
        style.configure("Secondary.TLabel", foreground="#a9bdc9")
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("TCheckbutton", background="#101820", foreground="#edf4f7", font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", "#20313f")])
        style.configure("TRadiobutton", background="#101820", foreground="#edf4f7", font=("Segoe UI", 10))
        style.map("TRadiobutton", background=[("active", "#20313f")])
        style.configure("TButton", padding=(12, 8), font=("Segoe UI", 10))
        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, bg="#101820", highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        panel = ttk.Frame(canvas, padding=22)
        content = canvas.create_window((0, 0), window=panel, anchor="nw")
        panel.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        root.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        ttk.Label(panel, text="PodLook 0.2", style="Title.TLabel").pack(anchor="w")
        ttk.Label(panel, text="AirPods motion. Optional webcam correction. Local game output.", style="Secondary.TLabel").pack(anchor="w", pady=(3, 16))
        modebox = ttk.LabelFrame(panel, text="Tracking mode", padding=12)
        modebox.pack(fill="x", pady=(0, 12))
        self.airpods_radio = ttk.Radiobutton(modebox, text="AirPods only", value="airpods", variable=self.mode, command=self._mode_changed)
        self.hybrid_radio = ttk.Radiobutton(modebox, text="AirPods + webcam", value="hybrid", variable=self.mode, command=self._mode_changed)
        self.airpods_radio.grid(row=0, column=0, sticky="w", padx=(0, 24))
        self.hybrid_radio.grid(row=0, column=1, sticky="w")
        camera_line = ttk.Frame(modebox)
        camera_line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(camera_line, text="Choose camera:").pack(side="left")
        self.camera_selector = ttk.Combobox(camera_line, values=[f"Camera {i}" for i in range(9)], width=20, textvariable=self.camera_choice, state="readonly")
        self.camera_selector.pack(side="left", padx=8)
        self.camera_selector.bind("<<ComboboxSelected>>", lambda _: self.camera_index.set(self.camera_choice.get().split()[1]))
        self.scan_button = ttk.Button(camera_line, text="Find cameras", command=self.find_cameras)
        self.scan_button.pack(side="left", padx=(0, 8))
        self.test_camera_button = ttk.Button(camera_line, text="Test webcam", command=self.test_camera)
        self.test_camera_button.pack(side="left")
        camera_options = ttk.Frame(modebox)
        camera_options.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.camera_yaw_check = ttk.Checkbutton(camera_options, text="Invert webcam yaw", variable=self.camera_invert_yaw)
        self.camera_yaw_check.pack(side="left", padx=(10, 8))
        self.camera_pitch_check = ttk.Checkbutton(camera_options, text="Invert webcam pitch", variable=self.camera_invert_pitch)
        self.camera_pitch_check.pack(side="left")
        ttk.Label(modebox, textvariable=self.camera_status, wraplength=660, style="Secondary.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(modebox, text="Test webcam works without AirPods. Select AirPods + webcam to use it during tracking.", wraplength=660, style="Secondary.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        connection = ttk.LabelFrame(panel, text="iPhone connection", padding=12)
        connection.pack(fill="x", pady=(0, 12))
        addresses = local_addresses()
        address_text = " / ".join(addresses) if addresses else "Run ipconfig to find your PC's IPv4 address"
        ttk.Label(connection, text=f"PC address: {address_text}", wraplength=660).pack(anchor="w")
        ttk.Label(connection, text="In the iPhone PodLook app, use this PC address and port 4243.", style="Secondary.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(connection, textvariable=self.stats, wraplength=660, style="Secondary.TLabel").pack(anchor="w", pady=(4, 0))
        live = ttk.LabelFrame(panel, text="Live tracking", padding=12)
        live.pack(fill="x", pady=(0, 12))
        ttk.Label(live, textvariable=self.status, wraplength=660).pack(anchor="w")
        ttk.Label(live, textvariable=self.angles, font=("Consolas", 14)).pack(anchor="w", pady=12)
        ttk.Label(live, textvariable=self.calibration_status, wraplength=660, style="Secondary.TLabel").pack(anchor="w")
        buttons = ttk.Frame(live)
        buttons.pack(fill="x", pady=(12, 0))
        self.start_button = ttk.Button(buttons, text="Start tracking", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.calibrate_button = ttk.Button(buttons, text="Calibrate in 3 seconds", command=self.begin_calibration, state="disabled")
        self.calibrate_button.pack(side="left")
        tuning = ttk.LabelFrame(panel, text="Camera movement", padding=12)
        tuning.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(tuning, textvariable=self.auto_label, variable=self.auto, command=self._auto_changed).pack(anchor="w")
        ttk.Label(tuning, text="AirPods-only center assist estimates forward; disable it for sustained side views.", wraplength=660, style="Secondary.TLabel").pack(anchor="w", pady=(3, 6))
        for label, variable in (("Horizontal", self.yaw_gain), ("Vertical", self.pitch_gain)):
            line = ttk.Frame(tuning)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=label, width=12).pack(side="left")
            ttk.Scale(line, from_=0.25, to=3.0, variable=variable, command=lambda _: self._gains_changed()).pack(side="left", fill="x", expand=True)
        ttk.Label(tuning, textvariable=self.gain_label, style="Secondary.TLabel").pack(anchor="w", pady=(3, 5))
        ttk.Checkbutton(tuning, text="Include head tilt (roll)", variable=self.roll).pack(anchor="w")
        bottom = ttk.Frame(panel)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="OpenTrack settings", command=self.open_settings).pack(side="left")
        ttk.Button(bottom, text="Setup help", command=self.help).pack(side="left", padx=8)
        ttk.Label(panel, text="Game output: 127.0.0.1:4242 · Keep the iPhone app open while driving.", style="Secondary.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Label(panel, text=self.hotkey.message if self.hotkey else "Alt+C global hotkey is skipped during smoke tests.", style="Secondary.TLabel").pack(anchor="w", pady=(3, 0))

    def _mode_changed(self):
        if self.transport is not None:
            return
        hybrid = self.mode.get() == "hybrid"
        self.auto.set(hybrid)
        self.auto_label.set("Automatic yaw correction from webcam" if hybrid else "Center assist (slow estimate)")
        self._set_camera_controls()
        if self.camera is None:
            self.camera_status.set("Select a camera and Test webcam, or Start tracking." if hybrid else "Webcam is off. Test webcam is available without changing tracking mode.")

    def _set_camera_controls(self):
        state = "normal" if self.mode.get() == "hybrid" and self.transport is None else "disabled"
        self.camera_yaw_check.configure(state=state)
        self.camera_pitch_check.configure(state=state)
        busy = self.camera is not None or self.transport is not None or self.scan_thread is not None
        self.camera_selector.configure(state="disabled" if busy else "readonly")
        self.scan_button.configure(state="disabled" if busy else "normal")
        self.test_camera_button.configure(state="disabled" if self.scan_thread is not None else "normal",
                                          text="Show preview" if self.transport is not None and self.camera is not None else "Stop webcam test" if self.camera is not None else "Test webcam")

    def find_cameras(self):
        if self.camera is not None or self.transport is not None or self.scan_thread is not None:
            return
        self.scan_cancel.clear()
        self.camera_status.set("Checking cameras 0–8 locally; this can take up to 72 seconds…")
        def scan():
            try:
                from webcam import discover_cameras
                self.scan_results.put((discover_cameras(self.scan_cancel), None))
            except Exception as error:
                self.scan_results.put(([], str(error)))
        self.scan_thread = threading.Thread(target=scan, name="PodLook camera discovery", daemon=True)
        self.scan_thread.start()
        self._set_camera_controls()

    def _open_camera(self):
        if self.stopping_camera is not None and self.stopping_camera.running:
            raise RuntimeError("Previous camera session is still stopping. Wait before opening it again.")
        from webcam import WebcamProvider
        self.camera = WebcamProvider(camera_index=int(self.camera_index.get()))
        self.camera.start()
        self.camera_status.set(self.camera.error or "Opening selected camera…")

    def test_camera(self):
        if self.scan_thread is not None:
            return
        if self.transport is not None and self.camera is None:
            self.camera_status.set("Stop tracking first to test the webcam, or switch to AirPods + webcam.")
            return
        if self.camera is not None and self.transport is None:
            self._stop_camera()
            self._set_camera_controls()
            return
        try:
            if self.camera is None:
                self._open_camera()
            self._show_preview()
            self._set_camera_controls()
        except Exception as error:
            self.camera_status.set(f"Could not test webcam: {error}")

    def _show_preview(self):
        if self.preview_window is not None:
            self.preview_window.lift()
            return
        window = self.tk.Toplevel(self.root)
        window.title(f"PodLook · Camera {self.camera_index.get()} preview")
        window.geometry("520x430")
        self.preview_window = window
        self.preview_label = self.ttk.Label(window, text="Waiting for the first camera frame…", anchor="center")
        self.preview_label.pack(fill="both", expand=True, padx=12, pady=12)
        self.ttk.Label(window, textvariable=self.camera_status, wraplength=490).pack(padx=12, pady=8)
        self.ttk.Label(window, text="Local preview only · no video saved or uploaded", wraplength=490).pack(pady=(0, 12))
        window.protocol("WM_DELETE_WINDOW", self._close_preview)
        if self.camera is not None:
            self.camera.enable_preview(True)

    def _close_preview(self):
        if self.camera is not None:
            self.camera.enable_preview(False)
        if self.preview_window is not None:
            self.preview_window.destroy()
        self.preview_window = self.preview_label = self.preview_photo = None
        if self.transport is None and self.camera is not None:
            self._stop_camera()
            self._set_camera_controls()

    def _stop_camera(self):
        camera, self.camera = self.camera, None
        if camera is not None:
            camera.enable_preview(False)
            camera.stop()
            if camera.running:
                self.stopping_camera = camera
        self._close_preview()
        self.camera_status.set("Webcam stopped.")

    def _poll_camera(self, now):
        if self.camera is None:
            return
        observation = self.camera.poll()
        if observation is not None:
            pose = (observation.yaw * (-1 if self.camera_invert_yaw.get() else 1),
                    observation.pitch * (-1 if self.camera_invert_pitch.get() else 1), observation.roll)
            if self.engine is not None and self.transport is not None:
                self.engine.push_webcam(pose, observation.timestamp, valid=observation.valid)
            suffix = f" · yaw {pose[0]:+.1f}° / pitch {pose[1]:+.1f}°" if observation.valid else ""
            self.camera_status.set((self.camera.error or self.camera.status) + suffix)
        elif self.camera.error:
            self.camera_status.set(self.camera.error)
        if self.preview_window is not None and now - self.last_preview_at >= .1:
            self.last_preview_at = now
            preview = self.camera.poll_preview()
            if preview is not None:
                from PIL import Image, ImageTk
                frame, _ = preview
                picture = Image.fromarray(frame[:, :, ::-1])
                picture.thumbnail((490, 320))
                self.preview_photo = ImageTk.PhotoImage(picture, master=self.preview_window)
                self.preview_label.configure(image=self.preview_photo, text="")

    def _gains_changed(self):
        self.gain_label.set(f"Horizontal {self.yaw_gain.get():.2f}×   ·   Vertical {self.pitch_gain.get():.2f}×")

    def _auto_changed(self):
        if self.engine is not None:
            self.engine.set_auto_correction(self.auto.get())

    def start(self):
        if self.transport is not None:
            return
        try:
            if self.scan_thread is not None:
                self.status.set("Wait for camera discovery to finish before starting tracking.")
                return
            if self.camera is not None:
                self._stop_camera()
            if self.stopping_camera is not None and self.stopping_camera.running:
                self.status.set("The previous webcam session is still stopping. Wait, or close PodLook if the camera driver stays busy.")
                return
            self.stopping_camera = None
            try:
                from .fusion import FusionEngine
                from .opentrack_bridge import OpenTrackBridge
            except ImportError:
                from fusion import FusionEngine
                from opentrack_bridge import OpenTrackBridge
            index = int(self.camera_index.get())
            if not 0 <= index <= 8:
                raise ValueError("Webcam index must be between 0 and 8")
            self.engine = FusionEngine(mode=self.mode.get(), webcam_latency=0)
            self.engine.set_auto_correction(self.auto.get())
            self.bridge = OpenTrackBridge()
            bridge_status = self.bridge.start()
            self.bridge_started_at = time.monotonic()
            self.last_bridge_check = 0.0
            self.bridge_ready = False
            self.transport = PacketTransport()
            self.transport.start()
            if self.mode.get() == "hybrid":
                self._open_camera()
                self._show_preview()
            self.status.set(f"Waiting for AirPods. {bridge_status or ''}")
            self.calibration_status.set("When motion arrives, look forward and click Calibrate.")
            self.last_packet = None
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.calibrate_button.configure(state="normal")
            self.airpods_radio.configure(state="disabled")
            self.hybrid_radio.configure(state="disabled")
            self.camera_selector.configure(state="disabled")
            self._set_camera_controls()
        except Exception as error:
            self.stop(message=f"Could not start: {error}")

    def stop(self, message="Stopped. The game camera has been sent a neutral pose."):
        self.calibrate_at = None
        if self.transport is not None:
            self.transport.close()
        self.transport = None
        self._stop_camera()
        if self.engine is not None:
            self.engine.reset()
        self.engine = None
        if self.bridge is not None:
            self.bridge.stop()
        self.bridge = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.calibrate_button.configure(state="disabled")
        self.airpods_radio.configure(state="normal")
        self.hybrid_radio.configure(state="normal")
        self._set_camera_controls()
        self.status.set(message)
        self.camera_status.set("Camera driver is still stopping; close PodLook if it does not release." if self.stopping_camera is not None and self.stopping_camera.running else "Webcam stopped.")
        self.stats.set("No sender pinned. Start a new session to reconnect the phone.")
        self.angles.set("YAW   0.0°       PITCH   0.0°       ROLL   0.0°")

    def begin_calibration(self):
        if self.transport is None:
            return
        self.calibrate_at = time.monotonic() + 3
        self.calibration_status.set("Face straight ahead and hold still — calibrating in 3…")

    def _tick(self):
        now = time.monotonic()
        if self.scan_thread is not None:
            try:
                found, error = self.scan_results.get_nowait()
            except queue.Empty:
                pass
            else:
                self.scan_thread = None
                choices = [f"Camera {index} — detected" for index in found]
                current = int(self.camera_index.get())
                if found and current not in found:
                    current = found[0]
                    self.camera_index.set(str(current))
                if not found:
                    choices = [f"Camera {index} — not confirmed" for index in range(9)]
                self.camera_selector.configure(values=choices)
                self.camera_choice.set(next(choice for choice in choices if choice.split()[1] == str(current)))
                self.camera_status.set(f"Camera scan failed: {error}" if error else f"Found {len(found)} camera(s). Choose one, then Test webcam." if found else "Scan did not confirm a camera. Choose Camera 0 and try Test webcam; close other camera apps if it cannot open.")
                self._set_camera_controls()
        try:
            self._poll_camera(now)
        except Exception as error:
            self.camera_status.set(f"Webcam preview error: {error}")
        if self.stopping_camera is not None and not self.stopping_camera.running:
            self.stopping_camera = None
            if self.camera is None:
                self.camera_status.set("Webcam stopped.")
        if self.hotkey is not None and self.hotkey.pressed() and self.engine is not None:
            centered = self.engine.recenter(now)
            self.calibrate_at = None
            self.calibration_status.set("Centered. Keep looking forward briefly to stabilize tracking." if centered else "Cannot center: fresh AirPods motion is required.")
        if self.transport is not None:
            try:
                if now - self.last_bridge_check >= 0.5:
                    self.last_bridge_check = now
                    self.bridge_ready = self.bridge.ready()
                    if not self.bridge_ready and now - self.bridge_started_at > 10:
                        raise RuntimeError("OpenTrack did not start its receiver, or stopped. Check its window, close other instances, then retry.")
                sample = self.transport.drain(now)
                if sample is not None:
                    self.engine.push_airpods(sample.pose, sample.timestamp)
                    self.last_packet = now
                if self.calibrate_at is not None:
                    remaining = self.calibrate_at - now
                    if remaining <= 0:
                        calibrated = self.engine.calibrate(now)
                        self.calibration_status.set("Calibrated. Recalibrate if you move your seat or webcam." if calibrated else
                                                    "Calibration needs stable, fresh input. Face forward, hold still and try again.")
                        self.calibrate_at = None
                    else:
                        self.calibration_status.set(f"Face forward and hold still — calibrating in {math.ceil(remaining)}…")
                result = self.engine.sample(now)
                output = shape_output(result.pose, self.yaw_gain.get(), self.pitch_gain.get(), self.roll.get())
                self.transport.send(output)
                if now - self.last_ui_update >= 0.1:
                    self.last_ui_update = now
                    description = STATUS_TEXT.get(result.status, "Tracking state changed; check your phone and webcam.")
                    self.status.set(description if self.bridge_ready else "Waiting for OpenTrack receiver… " + description)
                    self.angles.set(f"YAW {output[0]:+6.1f}°    PITCH {output[1]:+6.1f}°    ROLL {output[2]:+6.1f}°")
                    source = f"{self.transport.sender[0]}:{self.transport.sender[1]}" if self.transport.sender else "waiting"
                    self.stats.set(f"Phone: {source} · {self.transport.received} valid packets · {self.transport.rejected} rejected · {self.transport.foreign} other-source packets ignored")
            except Exception as error:
                self.stop(message=f"Tracking stopped: {error}")
        self.root.after(20, self._tick)

    def open_settings(self):
        try:
            if self.bridge is None:
                self.status.set("Start tracking first, then open OpenTrack settings.")
            else:
                message = self.bridge.open_settings()
                if message:
                    self.status.set(message)
        except Exception as error:
            self.status.set(f"OpenTrack settings unavailable: {error}")

    def help(self):
        from tkinter import messagebox
        messagebox.showinfo("PodLook setup", "1. Stop other OpenTrack instances. PodLook launches its own game-output instance.\n\n"
            "2. Connect AirPods to the iPhone. Set the iPhone app to your PC's IPv4 address, UDP port 4243 (not 4242). Keep the app open.\n\n"
            "3. Start PodLook on Windows, then start motion on the iPhone. Allow private-network access if Windows asks.\n\n"
            "4. For hybrid mode, close other apps using the webcam. Face forward, click Calibrate, and hold still through the countdown.\n\n"
            "5. Enable Head Tracking with TrackIR in F1 25. Check left/right and up/down before driving.\n\n"
            "Hybrid uses valid webcam observations for slow yaw correction. AirPods-only center assist is optional and estimates where forward is.\n\n"
            "Switch modes by stopping, choosing the mode, starting and centering again. If the phone restarts or changes connection, stop and restart this Windows session. Packet source stays pinned until Stop.\n\n"
            "The phone and PC must share a trusted local network. No router port forwarding is needed.", parent=self.root)

    def close(self):
        self.scan_cancel.set()
        self.stop()
        if self.hotkey is not None:
            self.hotkey.close()
        if not self.smoke_test:
            try:
                data = {"mode": self.mode.get(), "auto": self.auto.get(), "roll": self.roll.get(),
                        "yaw_gain": self.yaw_gain.get(), "pitch_gain": self.pitch_gain.get(),
                        "camera_index": int(self.camera_index.get()),
                        "camera_invert_yaw": self.camera_invert_yaw.get(), "camera_invert_pitch": self.camera_invert_pitch.get()}
                destination = preferences_path()
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".tmp")
                temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
                temporary.replace(destination)
            except (OSError, ValueError):
                pass  # Preferences must not prevent normal shutdown.
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", help="Create the GUI, process events, then exit without camera/network access")
    parser.add_argument("--model-self-test", action="store_true", help="Verify bundled model and inference dependencies with a blank image; does not open a camera")
    parser.add_argument("--probe-camera", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe_camera is not None:
        from webcam import probe_camera
        return 0 if probe_camera(args.probe_camera) else 2
    if args.model_self_test:
        try:
            import numpy as np
            try:
                from .webcam import _MediaPipeDetector, default_model_path, verify_model
            except ImportError:
                from webcam import _MediaPipeDetector, default_model_path, verify_model
            path = default_model_path()
            verify_model(path)
            detector = _MediaPipeDetector(path)
            try:
                result = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8), 1)
                if len(result.face_landmarks) != 0:
                    raise RuntimeError("Blank-image inference unexpectedly returned a face")
            finally:
                detector.close()
            print("PASS: model verified, inference loaded, blank image correctly rejected without opening a camera")
            return 0
        except Exception as error:
            print(f"FAIL: model self-test: {error}")
            return 2
    import tkinter as tk
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    app = CompanionApp(root, smoke_test=args.smoke_test)
    if args.smoke_test:
        root.update_idletasks()
        root.update()
        app.close()
        print("PASS: PodLook Tk GUI initialized and closed without starting hardware or network")
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
