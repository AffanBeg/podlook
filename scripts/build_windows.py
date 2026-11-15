"""Build the Windows companion with a verified offline face model. Python 3.12."""
from pathlib import Path
import argparse
import hashlib
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'windows'))
from webcam import MODEL_URL, MODEL_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT / 'build' / 'windows')
    args = parser.parse_args()
    build = ROOT / 'build' / 'windows-work'
    build.mkdir(parents=True, exist_ok=True)
    model = build / 'face_landmarker.task'
    if not model.is_file():
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise RuntimeError('Face model checksum mismatch')
        model.write_bytes(data)
    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError('Face model checksum mismatch')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
               '--name', 'PodLook', '--distpath', str(args.dist.resolve()),
               '--workpath', str(build / 'pyinstaller'), '--specpath', str(build),
               '--paths', str(ROOT / 'windows'), '--add-data', str(model) + ';models',
               '--collect-all', 'mediapipe', '--exclude-module', 'jax', '--exclude-module', 'jaxlib',
               '--exclude-module', 'IPython', '--exclude-module', 'pytest',
               str(ROOT / 'windows' / 'companion.py')]
    subprocess.run(command, check=True)
    destination = args.dist / 'PodLook'
    for source, target in [(ROOT / 'windows' / 'README.md', 'README.txt'),
                           (ROOT / 'windows' / 'THIRD_PARTY.md', 'THIRD_PARTY.txt')]:
        if source.is_file():
            (destination / target).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    print(destination / 'PodLook.exe')


if __name__ == '__main__':
    main()
