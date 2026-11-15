import importlib.util
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch, Mock

path = Path(__file__).resolve().parents[1] / 'windows' / 'opentrack_bridge.py'
spec = importlib.util.spec_from_file_location('opentrack_bridge_tested', path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class OpenTrackIntegrationTests(unittest.TestCase):
    def test_ipv4_listener_is_detected(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', 0))
            self.assertFalse(bridge.port_available(receiver.getsockname()[1]))

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 unavailable')
    def test_ipv6_shared_listener_is_detected_when_ipv4_probe_succeeds(self):
        # Qt can bind :: with ShareAddress/ReuseAddressHint. An IPv4-only probe
        # misses a v6-only listener completely: reproduce that exact false negative.
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as receiver:
            receiver.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            receiver.bind(('::', 0))
            port = receiver.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as old_probe:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    old_probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                old_probe.bind(('0.0.0.0', port))  # Old code incorrectly returned True.
            self.assertFalse(bridge.port_available(port))

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 unavailable')
    def test_dual_stack_shared_listener_is_detected(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as receiver:
            receiver.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            receiver.bind(('::', 0))
            self.assertFalse(bridge.port_available(receiver.getsockname()[1]))

    def test_isolated_runtime_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'installed'
            source.mkdir()
            (source / 'opentrack.exe').write_bytes(b'test fixture')
            (source / 'globals.ini').write_text('personal settings')
            (source / 'ini').mkdir()
            (source / 'ini' / 'default.ini').write_text('personal profile')
            destination = root / 'managed'
            exe = bridge.prepare_runtime(destination, source / 'opentrack.exe', 'PodLook.exe')
            self.assertEqual(exe.read_bytes(), b'test fixture')
            self.assertEqual((source / 'globals.ini').read_text(), 'personal settings')
            self.assertEqual((source / 'ini' / 'default.ini').read_text(), 'personal profile')
            self.assertFalse((destination / 'ini' / 'default.ini').exists())
            profile = (destination / 'ini' / 'podlook.ini').read_text()
            self.assertIn('center-at-startup=false', profile)
            self.assertIn('centering-mode=0', profile)
            self.assertIn('apply-mapping-curves=false', profile)
            self.assertIn('executable-list=\\x1ePodLook.exe\\x1fpodlook.ini',
                          (destination / 'globals.ini').read_text())

    def test_incomplete_runtime_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'partial.dll').write_bytes(b'keep')
            with patch.object(bridge, 'installed_opentrack', return_value=Path(__file__)):
                with self.assertRaises(RuntimeError):
                    bridge.prepare_runtime(root)
            self.assertEqual((root / 'partial.dll').read_bytes(), b'keep')

    def test_port_collision_prevents_launch(self):
        with patch.object(bridge, 'port_available', return_value=False), patch.object(bridge.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '4242'):
                bridge.OpenTrackBridge().start()
            launch.assert_not_called()

    def test_stop_terminates_only_owned_child(self):
        owner = bridge.OpenTrackBridge()
        process = Mock()
        process.poll.return_value = None
        owner.process = process
        owner.stop()
        process.terminate.assert_called_once()
        self.assertIsNone(owner.process)
        owner.stop()
        process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
