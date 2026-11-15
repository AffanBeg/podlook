#!/usr/bin/env python3
"""Package a device .app, refusing simulator or wrongly named bundles."""
from pathlib import Path
import plistlib
import sys
import zipfile


def package(app, destination):
    app, destination = Path(app), Path(destination)
    with (app / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    if app.name != "PodLook.app" or info.get("CFBundlePackageType") != "APPL":
        raise ValueError("Expected a built PodLook.app application")
    if "iPhoneOS" not in info.get("CFBundleSupportedPlatforms", []):
        raise ValueError("This is not an iPhoneOS device build; simulator apps cannot be sideloaded")
    executable = app / info["CFBundleExecutable"]
    if not executable.is_file():
        raise ValueError("Application executable is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                archive.write(path, "Payload/PodLook.app/" + path.relative_to(app).as_posix())
    print(f"Packaged {destination} (unsigned; sign using Sideloadly before installing)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: package_ipa.py built/PodLook.app output.ipa")
    package(sys.argv[1], sys.argv[2])
