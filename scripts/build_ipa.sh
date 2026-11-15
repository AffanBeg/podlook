#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/generate_project.py
xcodebuild -project ios/PodLook.xcodeproj -scheme PodLook \
  -configuration Release -destination 'generic/platform=iOS' \
  -derivedDataPath build/device CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY='' build
python3 scripts/package_ipa.py build/device/Build/Products/Release-iphoneos/PodLook.app build/PodLook-unsigned.ipa
