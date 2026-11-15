#!/usr/bin/env python3
"""Generate the Xcode project using only Python's standard library.

Run from any directory. IDs and ordering are deterministic, with no XcodeGen,
CocoaPods, packages, signing credentials, or development team required.
"""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios"
PROJECT = IOS / "PodLook.xcodeproj"


def oid(name):
    return hashlib.sha1(name.encode()).hexdigest()[:24].upper()


def dump(value, level=0):
    indent = "\t" * level
    if isinstance(value, dict):
        return "{\n" + "".join(
            f'{indent}\t{json.dumps(str(k))} = {dump(v, level + 1)};\n'
            for k, v in value.items()
        ) + indent + "}"
    if isinstance(value, list):
        return "(\n" + "".join(f'{indent}\t{dump(v, level + 1)},\n' for v in value) + indent + ")"
    return json.dumps(str(value))


def generate():
    objects = {}

    def add(identity_name, isa, **fields):
        identity = oid(identity_name)
        objects[identity] = {"isa": isa, **fields}
        return identity

    def config_list(name, settings):
        configs = []
        for configuration in ("Debug", "Release"):
            specific = dict(settings)
            if name == "project":
                specific.update({
                    "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if configuration == "Debug" else "-O",
                    "DEBUG_INFORMATION_FORMAT": "dwarf" if configuration == "Debug" else "dwarf-with-dsym",
                    "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG" if configuration == "Debug" else "",
                })
            configs.append(add(f"{name}-{configuration}", "XCBuildConfiguration", buildSettings=specific, name=configuration))
        return add(name + "-config-list", "XCConfigurationList", buildConfigurations=configs,
                   defaultConfigurationIsVisible="0", defaultConfigurationName="Release")

    app_refs, app_builds = [], []
    for source in sorted((IOS / "PodLook").glob("*.swift")):
        ref = add("file-" + source.name, "PBXFileReference", lastKnownFileType="sourcecode.swift",
                  path=source.name, sourceTree="<group>")
        app_refs.append(ref)
        app_builds.append(add("build-" + source.name, "PBXBuildFile", fileRef=ref))
    if not app_builds:
        raise RuntimeError("No Swift app sources found")
    app_refs.append(add("info", "PBXFileReference", lastKnownFileType="text.plist.xml",
                        path="Info.plist", sourceTree="<group>"))
    app_group = add("app-group", "PBXGroup", children=app_refs, path="PodLook", sourceTree="<group>")
    test_ref = add("ui-test-file", "PBXFileReference", lastKnownFileType="sourcecode.swift",
                   path="../tests/UI/PodLookUITests.swift", sourceTree="SOURCE_ROOT")
    test_group = add("test-group", "PBXGroup", children=[test_ref], name="UI Tests", sourceTree="<group>")
    test_build = add("ui-test-build", "PBXBuildFile", fileRef=test_ref)
    app_product = add("app-product", "PBXFileReference", explicitFileType="wrapper.application",
                      includeInIndex="0", path="PodLook.app", sourceTree="BUILT_PRODUCTS_DIR")
    test_product = add("test-product", "PBXFileReference", explicitFileType="wrapper.cfbundle",
                       includeInIndex="0", path="PodLookUITests.xctest", sourceTree="BUILT_PRODUCTS_DIR")
    products = add("products", "PBXGroup", children=[app_product, test_product], name="Products", sourceTree="<group>")
    main_group = add("main-group", "PBXGroup", children=[app_group, test_group, products], sourceTree="<group>")
    common = {
        "CLANG_ENABLE_MODULES": "YES", "CLANG_ENABLE_OBJC_ARC": "YES",
        "IPHONEOS_DEPLOYMENT_TARGET": "16.0", "SDKROOT": "iphoneos", "SWIFT_VERSION": "5.0",
        "TARGETED_DEVICE_FAMILY": "1", "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
        "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
    }
    project_configs = config_list("project", common)
    app_configs = config_list("app", {
        "PRODUCT_BUNDLE_IDENTIFIER": "org.podlook.personal", "PRODUCT_NAME": "$(TARGET_NAME)",
        "INFOPLIST_FILE": "PodLook/Info.plist", "GENERATE_INFOPLIST_FILE": "NO",
        "CODE_SIGN_STYLE": "Automatic", "LD_RUNPATH_SEARCH_PATHS": "$(inherited) @executable_path/Frameworks",
    })
    test_configs = config_list("ui-test", {
        "PRODUCT_BUNDLE_IDENTIFIER": "org.podlook.personal.uitests", "PRODUCT_NAME": "$(TARGET_NAME)",
        "GENERATE_INFOPLIST_FILE": "YES", "TEST_TARGET_NAME": "PodLook",
        "CODE_SIGN_STYLE": "Automatic", "LD_RUNPATH_SEARCH_PATHS": "$(inherited) @executable_path/Frameworks @loader_path/Frameworks",
    })

    def phases(prefix, builds):
        result = []
        for kind, files in (("Sources", builds), ("Frameworks", []), ("Resources", [])):
            result.append(add(prefix + "-" + kind, "PBX" + kind + "BuildPhase", buildActionMask="2147483647",
                              files=files, runOnlyForDeploymentPostprocessing="0"))
        return result

    app_target = add("app-target", "PBXNativeTarget", buildConfigurationList=app_configs,
                     buildPhases=phases("app", app_builds), buildRules=[], dependencies=[],
                     name="PodLook", productName="PodLook", productReference=app_product,
                     productType="com.apple.product-type.application")
    proxy = add("app-proxy", "PBXContainerItemProxy", containerPortal=oid("project"), proxyType="1",
                remoteGlobalIDString=app_target, remoteInfo="PodLook")
    dependency = add("test-dependency", "PBXTargetDependency", target=app_target, targetProxy=proxy)
    test_target = add("ui-test-target", "PBXNativeTarget", buildConfigurationList=test_configs,
                      buildPhases=phases("ui-test", [test_build]), buildRules=[], dependencies=[dependency],
                      name="PodLookUITests", productName="PodLookUITests", productReference=test_product,
                      productType="com.apple.product-type.bundle.ui-testing")
    add("project", "PBXProject", attributes={"LastUpgradeCheck": "1600", "BuildIndependentTargetsInParallel": "YES",
        "TargetAttributes": {app_target: {"CreatedOnToolsVersion": "16.0"},
                             test_target: {"CreatedOnToolsVersion": "16.0", "TestTargetID": app_target}}},
        buildConfigurationList=project_configs, compatibilityVersion="Xcode 14.0", developmentRegion="en",
        hasScannedForEncodings="0", knownRegions=["en", "Base"], mainGroup=main_group,
        productRefGroup=products, projectDirPath="", projectRoot="", targets=[app_target, test_target])
    document = {"archiveVersion": "1", "classes": {}, "objectVersion": "56", "objects": objects,
                "rootObject": oid("project")}
    PROJECT.mkdir(parents=True, exist_ok=True)
    (PROJECT / "project.pbxproj").write_text("// !$*UTF8*$!\n" + dump(document) + "\n", encoding="utf-8", newline="\n")

    scheme = ET.Element("Scheme", LastUpgradeVersion="1600", version="1.3")
    build = ET.SubElement(scheme, "BuildAction", parallelizeBuildables="YES", buildImplicitDependencies="YES")
    entries = ET.SubElement(build, "BuildActionEntries")

    def reference(parent, target, name, product):
        ET.SubElement(parent, "BuildableReference", BuildableIdentifier="primary", BlueprintIdentifier=target,
                      BuildableName=product, BlueprintName=name, ReferencedContainer="container:PodLook.xcodeproj")

    for target, name, product, is_app in ((app_target, "PodLook", "PodLook.app", True),
                                        (test_target, "PodLookUITests", "PodLookUITests.xctest", False)):
        entry = ET.SubElement(entries, "BuildActionEntry", buildForTesting="YES",
                              buildForRunning="YES" if is_app else "NO", buildForProfiling="YES" if is_app else "NO",
                              buildForArchiving="YES" if is_app else "NO", buildForAnalyzing="YES")
        reference(entry, target, name, product)
    test = ET.SubElement(scheme, "TestAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB",
                         selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB", shouldUseLaunchSchemeArgsEnv="YES")
    testables = ET.SubElement(test, "Testables")
    testable = ET.SubElement(testables, "TestableReference", skipped="NO")
    reference(testable, test_target, "PodLookUITests", "PodLookUITests.xctest")
    launch = ET.SubElement(scheme, "LaunchAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB",
                           selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB", launchStyle="0",
                           useCustomWorkingDirectory="NO", ignoresPersistentStateOnLaunch="NO", debugDocumentVersioning="YES",
                           debugServiceExtension="internal", allowLocationSimulation="YES")
    runnable = ET.SubElement(launch, "BuildableProductRunnable", runnableDebuggingMode="0")
    reference(runnable, app_target, "PodLook", "PodLook.app")
    ET.SubElement(scheme, "AnalyzeAction", buildConfiguration="Debug")
    ET.SubElement(scheme, "ArchiveAction", buildConfiguration="Release", revealArchiveInOrganizer="YES")
    scheme_dir = PROJECT / "xcshareddata" / "xcschemes"
    scheme_dir.mkdir(parents=True, exist_ok=True)
    ET.indent(scheme, space="  ")
    ET.ElementTree(scheme).write(scheme_dir / "PodLook.xcscheme", encoding="utf-8", xml_declaration=True)
    print(f"Generated {PROJECT} ({len(app_builds)} app sources, one UI test source)")
    return document


if __name__ == "__main__":
    generate()
