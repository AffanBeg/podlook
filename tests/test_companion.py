"""Real UDP loopback tests for the Windows companion, without GUI or webcam."""
import importlib.util
import json
import math
from pathlib import Path
import socket
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("companion", ROOT / "windows" / "companion.py")
companion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(companion)

FUSION_SPEC = importlib.util.spec_from_file_location("podlook_fusion_for_companion_test", ROOT / "windows" / "fusion.py")
fusion = importlib.util.module_from_spec(FUSION_SPEC)
sys.modules[FUSION_SPEC.name] = fusion
FUSION_SPEC.loader.exec_module(fusion)


def packet(yaw=0.0, pitch=0.0, roll=0.0):
    return struct.pack("<6d", 0, 0, 0, yaw, pitch, roll)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.output.bind(("127.0.0.1", 0))
        self.output.settimeout(1)
        self.addCleanup(self.output.close)
        self.transport = companion.PacketTransport(input_port=0, output_port=self.output.getsockname()[1], bind_address="127.0.0.1")
        self.transport.start()
        self.addCleanup(self.transport.close)
        self.phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.phone.bind(("127.0.0.1", 0))
        self.addCleanup(self.phone.close)
        self.address = ("127.0.0.1", self.transport.input_port)

    def test_newest_packet_is_returned_and_source_is_pinned(self):
        self.phone.sendto(packet(1, 2, 3), self.address)
        self.phone.sendto(packet(4, 5, 6), self.address)
        sample = self.transport.drain(now=10.0)
        self.assertEqual(sample.pose, (4, 5, 6))
        self.assertEqual(sample.timestamp, 10.0)
        self.assertEqual(self.transport.received, 2)
        self.assertEqual(self.transport.sender, self.phone.getsockname())

    def test_malformed_packet_does_not_claim_source(self):
        attacker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(attacker.close)
        attacker.sendto(b"not-pose", self.address)
        self.assertIsNone(self.transport.drain())
        self.assertIsNone(self.transport.sender)
        self.phone.sendto(packet(12), self.address)
        self.assertEqual(self.transport.drain().pose[0], 12)
        self.assertEqual(self.transport.sender, self.phone.getsockname())
        self.assertEqual(self.transport.rejected, 1)

    def test_second_sender_is_ignored_even_after_long_gap(self):
        self.phone.sendto(packet(1), self.address)
        self.transport.drain(now=1)
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(second.close)
        second.sendto(packet(90), self.address)
        self.assertIsNone(self.transport.drain(now=1000))
        self.assertEqual(self.transport.foreign, 1)
        self.assertEqual(self.transport.sender, self.phone.getsockname())

    def test_sends_exact_opentrack_format_and_neutral_on_close(self):
        self.transport.send((13.25, -4.5, 2))
        data, source = self.output.recvfrom(1024)
        self.assertEqual(len(data), 48)
        self.assertEqual(source[0], "127.0.0.1")
        self.assertEqual(struct.unpack("<6d", data), (0, 0, 0, 13.25, -4.5, 2))
        self.transport.close()
        neutral, _ = self.output.recvfrom(1024)
        self.assertEqual(struct.unpack("<6d", neutral), (0,) * 6)
        self.transport.close()  # idempotent shutdown
        self.assertIsNone(self.transport.sender)

    def test_cannot_share_bound_input_port(self):
        competing = companion.PacketTransport(input_port=self.transport.input_port, output_port=self.output.getsockname()[1], bind_address="127.0.0.1")
        self.addCleanup(competing.close)
        with self.assertRaises(OSError):
            competing.start()
        self.assertIsNone(competing.receiver)

    def test_bounded_drain_does_not_present_backlog_as_fresh(self):
        self.transport.drain_limit = 2
        for yaw in range(5):
            self.phone.sendto(packet(yaw), self.address)
        self.assertIsNone(self.transport.drain(now=1))
        self.assertEqual(self.transport.received, 2)
        self.assertEqual(self.transport.overloaded, 1)
        self.assertIsNone(self.transport.drain(now=2))
        self.assertEqual(self.transport.drain(now=3).pose, (4, 0, 0))

    def test_invalid_output_is_not_sent(self):
        for pose in ((math.nan, 0, 0), (0, math.inf, 0), (1, 2)):
            with self.assertRaises(ValueError):
                self.transport.send(pose)

    def test_input_and_output_ports_must_differ(self):
        with self.assertRaises(ValueError):
            companion.PacketTransport(input_port=4242, output_port=4242)

    def test_actual_udp_through_fusion_gain_and_stale_neutral(self):
        engine = fusion.FusionEngine(mode="airpods")
        base = time.monotonic()
        for i in range(25):
            self.phone.sendto(packet(12, 3, 2), self.address)
            incoming = self.transport.drain(now=base + i * 0.02)
            self.assertIsNotNone(incoming)
            self.assertTrue(engine.push_airpods(incoming.pose, incoming.timestamp))
        self.assertTrue(engine.calibrate(base + 0.48))
        self.phone.sendto(packet(22, 8, 4), self.address)
        incoming = self.transport.drain(now=base + 0.50)
        engine.push_airpods(incoming.pose, incoming.timestamp)
        result = engine.sample(base + 0.50)
        shaped = companion.shape_output(result.pose, yaw_gain=2, pitch_gain=1.5)
        self.transport.send(shaped)
        output, _ = self.output.recvfrom(1024)
        values = struct.unpack("<6d", output)
        self.assertAlmostEqual(values[3], 20, places=6)
        self.assertAlmostEqual(values[4], 7.5, places=6)
        self.assertEqual(values[5], 0)
        stale = engine.sample(base + 1.50)
        self.assertEqual(stale.status, "airpods_lost_reset_required")
        self.transport.send(companion.shape_output(stale.pose))
        output, _ = self.output.recvfrom(1024)
        self.assertEqual(struct.unpack("<6d", output), (0,) * 6)


class ValidationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Win32 hotkey registration test")
    def test_global_hotkey_registers_or_reports_conflict_and_closes(self):
        hotkey = companion.CenterHotkey()
        try:
            self.assertTrue(hotkey.initialized.wait(1))
            self.assertIn("Alt+C", hotkey.message)
            self.assertFalse(hotkey.pressed())
        finally:
            hotkey.close()
        self.assertFalse(hotkey.registered)
    def test_invalid_airpods_packets(self):
        invalid = [b"", packet() + b"x", packet(math.nan), packet(math.inf), packet(181),
                   struct.pack("<6d", 1, 0, 0, 0, 0, 0)]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                companion.decode_airpods(data)

    def test_gains_are_applied_without_changing_input(self):
        raw = (30, -20, 5)
        self.assertEqual(companion.shape_output(raw, 2, 1.5), (60, -30, 0))
        self.assertEqual(raw, (30, -20, 5))
        self.assertEqual(companion.shape_output((100, 70, 30), 3, 2, True), (180, 90, 30))
        for gain in (0, 5, math.nan, math.inf):
            with self.assertRaises(ValueError):
                companion.shape_output(raw, gain)

    def test_preferences_are_validated_and_only_allowed_values_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"mode": "hybrid", "auto": True, "yaw_gain": 2,
                                        "pitch_gain": -1, "camera_index": 100, "roll": "yes",
                                        "pose_history": [1, 2, 3]}), encoding="utf-8")
            self.assertEqual(companion.read_preferences(path), {"mode": "hybrid", "auto": True, "yaw_gain": 2})
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(companion.read_preferences(path), {})


if __name__ == "__main__":
    unittest.main()
