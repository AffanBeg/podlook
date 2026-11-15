"""Isolated OpenTrack integration; never edits the user's installed profiles."""
from pathlib import Path
import os
import errno
import shutil
import socket
import subprocess
import sys

PROFILE = '''[migrations]
last-migration-at=20220126_00~

[modules]
tracker-dll=udp
protocol-dll=freetrack
filter-dll=accela

[udp-tracker]
port=4242

[opentrack-ui]
center-at-startup=false
centering-mode=0
apply-mapping-curves=false
use-system-tray=false
tracklogging-enabled=false
'''


def app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def port_available(port=4242):
    """Check both address families; Qt may own only an IPv6 wildcard socket.

    An IPv4 bind alone is not evidence that OpenTrack's port is unused. Use
    exclusive, non-reusing probes, closing each immediately. Windows treats
    shared v6-only and shared dual-stack wildcard bindings differently, so test
    both IPv6 modes explicitly rather than assuming one detects the other.
    """
    families = [(socket.AF_INET, ('0.0.0.0', port), None)]
    if socket.has_ipv6:
        families.extend([(socket.AF_INET6, ('::', port, 0, 0), 1),
                         (socket.AF_INET6, ('::', port, 0, 0), 0)])
    for family, address, v6_only in families:
        try:
            probe = socket.socket(family, socket.SOCK_DGRAM)
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, 10047):
                continue
            return False
        with probe:
            try:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                if family == socket.AF_INET6:
                    probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6_only)
                probe.bind(address)
            except OSError:
                return False
    return True


def installed_opentrack():
    for root in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
        if root:
            candidate = Path(root) / 'opentrack' / 'opentrack.exe'
            if candidate.is_file():
                return candidate
    return None


def prepare_runtime(destination, installed=None, process_name=None):
    """Copy an installed runtime once, preserving its license files and dependencies."""
    destination = Path(destination).resolve()
    executable = destination / 'opentrack.exe'
    if not executable.is_file():
        source = Path(installed) if installed else installed_opentrack()
        if source is None or not source.is_file():
            raise RuntimeError('Install OpenTrack first, then try Start again.')
        if destination == source.parent.resolve():
            raise ValueError('PodLook requires a separate OpenTrack runtime directory.')
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError('The PodLook OpenTrack runtime is incomplete. Re-extract the Windows app.')
        shutil.copytree(source.parent, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('unins*', 'globals.ini', 'portable.txt', 'ini'))
    (destination / 'portable.txt').write_text('PodLook isolated settings\n', encoding='utf-8')
    profile_dir = destination / 'ini'
    profile_dir.mkdir(exist_ok=True)
    # This reserved profile is controlled by the companion. Original installed profiles are untouched.
    (profile_dir / 'podlook.ini').write_text(PROFILE, encoding='utf-8')
    name = process_name or Path(sys.executable).name
    if any(c in name for c in '\r\n\\/\x1e\x1f'):
        raise ValueError('Invalid executable name')
    globals_text = ('[General]\nsettings-filename=podlook.ini\n'
                    'executable-detector-enabled=true\n'
                    f'executable-list=\\x1e{name}\\x1fpodlook.ini\n')
    (destination / 'globals.ini').write_text(globals_text, encoding='utf-8')
    return executable


class OpenTrackBridge:
    def __init__(self, runtime=None):
        self.runtime = Path(runtime) if runtime else app_directory() / 'opentrack-runtime'
        self.process = None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return 'OpenTrack is already running for PodLook.'
        if not port_available():
            raise RuntimeError('UDP port 4242 is in use. Stop the separately opened OpenTrack instance, then press Start here.')
        executable = prepare_runtime(self.runtime)
        self.process = subprocess.Popen([str(executable)], cwd=str(self.runtime))
        return 'OpenTrack launched; its receiver starts automatically within a few seconds.'

    def ready(self):
        return self.process is not None and self.process.poll() is None and not port_available()

    def status(self):
        if self.process is None or self.process.poll() is not None:
            return 'OpenTrack stopped'
        return 'OpenTrack receiver active' if self.ready() else 'Waiting for OpenTrack receiver'

    def open_settings(self):
        # Its ordinary window remains available on the taskbar while running.
        if self.process is None or self.process.poll() is not None:
            return self.start()
        return 'Select the OpenTrack window on the taskbar. PodLook controls sensitivity and centering.'

    def stop(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            # Terminate only the child we launched, never other OpenTrack processes.
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
