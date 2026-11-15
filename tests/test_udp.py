"""Independent OpenTrack wire checks and a real loopback UDP integration test."""
import importlib.util
import pathlib
import queue
import socket
import struct
import subprocess
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_udp', ROOT / 'tools/check_udp.py')
udp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(udp)


class WireTests(unittest.TestCase):
    def test_known_ieee754_fixture(self):
        data = bytes(24) + bytes.fromhex('0000000000003e4000000000000034c00000000000002440')
        self.assertEqual(udp.decode(data), (30, -20, 10))

    def test_invalid_inputs(self):
        packets = [b'', bytes(47), bytes(49), bytes(8192),
                   struct.pack('<6d', 1, 0, 0, 0, 0, 0),
                   struct.pack('<6d', 0, 0, 0, float('nan'), 0, 0),
                   struct.pack('<6d', 0, 0, 0, 0, float('inf'), 0),
                   struct.pack('<6d', 0, 0, 0, 0, 0, 200)]
        for packet in packets:
            with self.subTest(packet=packet[:30]):
                with self.assertRaises(ValueError):
                    udp.decode(packet)

    def test_real_udp_loopback_with_bad_packet_then_valid_poses(self):
        process = subprocess.Popen([sys.executable, str(ROOT / 'tools/check_udp.py'),
                                    '--bind', '127.0.0.1', '--port', '0', '--count', '3', '--timeout', '5'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = queue.Queue()
        def read_lines():
            for line in process.stdout:
                lines.put(line)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        try:
            banner = lines.get(timeout=5)
            self.assertIn('Listening on', banner)
            port = int(banner.split(':')[1].split('.')[0])
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for data in [b'invalid', struct.pack('<6d', 0, 0, 0, 0, 0, 0),
                             struct.pack('<6d', 0, 0, 0, 30, -20, 10),
                             struct.pack('<6d', 0, 0, 0, -179, 89, -40)]:
                    sender.sendto(data, ('127.0.0.1', port))
            process.wait(timeout=8)
            reader.join(timeout=1)
            output = ''.join(list(lines.queue))
            self.assertEqual(process.returncode, 0, output)
            self.assertIn('PASS: received 3 valid packets; rejected 1.', output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=1)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
