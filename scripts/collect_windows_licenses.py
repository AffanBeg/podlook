"""Retain installed dependency notices, including a build-environment superset."""
import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil
import sys


def collect_licenses(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    inventory = [
        'PodLook dependency license inventory',
        'This includes the build environment; some listed packages are not bundled.',
        'Original wheel-relative paths are preserved below each distribution folder.',
        'Tcl/Tk notices also remain with the bundled Tcl/Tk runtime in _internal.',
        '',
    ]
    count = 0
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
        name = dist.metadata.get('Name', 'unknown')
        folder = re.sub(r'[^A-Za-z0-9_.-]', '_', f'{name}-{dist.version}')
        copied = 0
        for item in dist.files or ():
            relative = Path(str(item))
            if relative.is_absolute() or '..' in relative.parts:
                continue
            parts = [part.lower() for part in relative.parts]
            filename = relative.name.lower()
            license_directory = any(part in ('license', 'licenses', 'licences') for part in parts[:-1])
            notice_name = any(token in filename for token in ('license', 'licence', 'copying', 'notice'))
            if not license_directory and not notice_name:
                continue
            if relative.suffix.lower() in ('.py', '.pyc', '.pyd', '.dll', '.so'):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            target = destination / folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        # Metadata supplies attribution and license information even for wheels
        # that do not ship a separate license file. It is not a substitute for
        # an upstream license missing from such a wheel.
        package_dir = destination / folder
        package_dir.mkdir(parents=True, exist_ok=True)
        details = '\n'.join(f'{key}: {value}' for key, value in dist.metadata.items()
                            if key in ('Name', 'Version', 'Author', 'Author-email', 'License',
                                       'License-Expression', 'License-File', 'Home-page', 'Project-URL'))
        (package_dir / 'ATTRIBUTION.txt').write_text(details + '\n', encoding='utf-8')
        inventory.append(f'{name} {dist.version}: {copied} notice files')
        count += copied
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError(f'Python license missing: {python_license}')
    shutil.copy2(python_license, destination / 'Python-LICENSE.txt')
    inventory.append(f'Python {sys.version.split()[0]}: Python-LICENSE.txt')
    (destination / 'INDEX.txt').write_text('\n'.join(inventory) + '\n', encoding='utf-8')
    return count + 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(f'Copied {collect_licenses(args.destination)} license/notice files to {args.destination}')
