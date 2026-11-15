"""Download only the official, versioned model and verify before installing it."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

from webcam import MODEL_SHA256, MODEL_URL, default_model_path, verify_model


def download_model(destination: Path) -> Path:
    if destination.exists():
        try:
            verify_model(destination)
            return destination
        except RuntimeError:
            pass
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
        data = response.read(10 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Downloaded model checksum mismatch; no file was installed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_model_path())
    args = parser.parse_args()
    print(download_model(args.output))
