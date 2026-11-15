"""Run with: python -m unittest discover -s tests -p 'test_*.py' -v"""
from pathlib import Path
import importlib.util
import plistlib
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ProjectTests(unittest.TestCase):
    def test_project_is_deterministic_and_all_sources_belong_to_target(self):
        generator = module("generate_project")
        with tempfile.TemporaryDirectory() as temp:
            generator.PROJECT = Path(temp) / "PodLook.xcodeproj"
            doc = generator.generate()
            original = (generator.PROJECT / "project.pbxproj").read_bytes()
            generator.generate()
            self.assertEqual(original, (generator.PROJECT / "project.pbxproj").read_bytes())
            objects = doc["objects"]
            self.assertIn(doc["rootObject"], objects)
            target = objects[generator.oid("app-target")]
            source_phase = objects[target["buildPhases"][0]]
            built_sources = {objects[objects[build]["fileRef"]]["path"] for build in source_phase["files"]}
            expected = {file.name for file in (ROOT / "ios" / "PodLook").glob("*.swift")}
            self.assertEqual(expected, built_sources)
            self.assertIn("PoseCore.swift", built_sources)
            scheme = ET.parse(generator.PROJECT / "xcshareddata/xcschemes/PodLook.xcscheme")
            refs = scheme.findall(".//BuildableReference")
            self.assertTrue(refs)
            self.assertTrue(all(ref.attrib["BlueprintIdentifier"] in objects for ref in refs))

    def test_privacy_and_foreground_only_configuration(self):
        with (ROOT / "ios/PodLook/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        self.assertTrue(info["NSMotionUsageDescription"])
        self.assertTrue(info["NSLocalNetworkUsageDescription"])
        self.assertNotIn("UIBackgroundModes", info)
        self.assertNotIn("NSCameraUsageDescription", info)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / "PodLook.app"
        self.app.mkdir()
        self.output = Path(self.temp.name) / "output.ipa"
        self.packager = module("package_ipa")

    def write_bundle(self, platform="iPhoneOS", executable=True):
        with (self.app / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundlePackageType": "APPL", "CFBundleExecutable": "PodLook",
                          "CFBundleSupportedPlatforms": [platform]}, handle)
        if executable:
            (self.app / "PodLook").write_bytes(b"fixture-executable")

    def test_device_bundle_has_exact_payload_structure(self):
        self.write_bundle()
        self.packager.package(self.app, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {"Payload/PodLook.app/Info.plist", "Payload/PodLook.app/PodLook"})
            self.assertEqual(archive.read("Payload/PodLook.app/PodLook"), b"fixture-executable")
            self.assertIsNone(archive.testzip())

    def test_simulator_app_is_rejected(self):
        self.write_bundle(platform="iPhoneSimulator")
        with self.assertRaisesRegex(ValueError, "simulator"):
            self.packager.package(self.app, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_executable_is_rejected(self):
        self.write_bundle(executable=False)
        with self.assertRaisesRegex(ValueError, "executable"):
            self.packager.package(self.app, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
