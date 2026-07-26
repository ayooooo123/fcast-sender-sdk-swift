import argparse
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_metadata import BuildEnvironment, ReleaseMetadataError
from scripts.source_pin import SourcePin, SourcePinError


FRAMEWORK_NAME = "fcast_sender_sdk.xcframework"
LIBRARY_NAME = "libfcast_sender_sdk.a"
HEADER_NAME = "fcast_sender_sdkFFI.h"
MODULE_MAP_NAME = "module.modulemap"
MODULE_NAME = "fcast_sender_sdkFFI"
CANONICAL_MODULE_MAP = (
    b"module fcast_sender_sdkFFI {\n"
    b"    header \"fcast_sender_sdkFFI.h\"\n"
    b"    export *\n"
    b"    use \"Darwin\"\n"
    b"    use \"_Builtin_stdbool\"\n"
    b"    use \"_Builtin_stdint\"\n"
    b"}"
)
OUTPUT_NAMES = {
    FRAMEWORK_NAME,
    "FCastSenderSDK.swift",
    "build-metadata.json",
}
TEMP_PATH_PATTERN = re.compile(
    rb"(?:/private/tmp/|/private/var/folders/|/tmp/)[^\x00\s\"']*"
)


class ArtifactVerificationError(ValueError):
    """Raised when a candidate distribution artifact is not canonical."""


def _reject_duplicate_object_keys(pairs):
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise ArtifactVerificationError(
                f"duplicate JSON object key is not allowed: {key}"
            )
        parsed[key] = value
    return parsed


def _load_metadata(data):
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ArtifactVerificationError(
            f"build metadata must be valid UTF-8 JSON: {error}"
        ) from error
    if type(payload) is not dict:
        raise ArtifactVerificationError(
            "build metadata has the wrong type; expected object"
        )
    return payload


def _require_exact(actual, expected, path):
    if type(actual) is not type(expected):
        raise ArtifactVerificationError(
            f"{path} has the wrong type; expected {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        actual_keys = set(actual)
        expected_keys = set(expected)
        unknown = actual_keys - expected_keys
        if unknown:
            raise ArtifactVerificationError(
                f"{path} contains unknown keys: "
                + ", ".join(sorted(unknown))
            )
        missing = expected_keys - actual_keys
        if missing:
            raise ArtifactVerificationError(
                f"{path} is missing keys: " + ", ".join(sorted(missing))
            )
        for key in expected:
            _require_exact(actual[key], expected[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ArtifactVerificationError(
                f"{path} has the wrong number of entries"
            )
        for index, (actual_item, expected_item) in enumerate(
            zip(actual, expected)
        ):
            _require_exact(
                actual_item,
                expected_item,
                f"{path}[{index}]",
            )
        return
    if actual != expected:
        raise ArtifactVerificationError(
            f"{path} disagrees with its lock; expected {expected}"
        )


def _expected_metadata(source_pin, environment, source_date_epoch):
    return {
        "buildEnvironment": environment.as_lock_payload(),
        "cargoPackage": source_pin.cargo_package,
        "features": list(source_pin.features),
        "rustTargets": list(source_pin.rust_targets),
        "schemaVersion": 1,
        "source": {
            "commit": source_pin.commit,
            "repository": source_pin.repository,
            "tag": source_pin.tag,
        },
        "sourceDateEpoch": source_date_epoch,
        "sourcePatched": False,
    }


def _read_bytes(path, label):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ArtifactVerificationError(
                f"{label} must be a real non-symlink regular file"
            )
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        raise ArtifactVerificationError(f"unable to read {label}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _directory_names(path, label):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_dir():
            raise ArtifactVerificationError(
                f"{label} must be a real non-symlink directory"
            )
        return {entry.name for entry in path.iterdir()}
    except OSError as error:
        raise ArtifactVerificationError(
            f"unable to enumerate {label}: {error}"
        ) from error


def _require_directory_contents(path, expected, label):
    actual = _directory_names(path, label)
    if actual != set(expected):
        raise ArtifactVerificationError(
            f"{label} must have exact contents: "
            + ", ".join(sorted(expected))
        )


def _reject_temp_path(data, label):
    match = TEMP_PATH_PATTERN.search(data)
    if match is not None:
        leaked = match.group(0).decode("utf-8", errors="replace")
        raise ArtifactVerificationError(
            f"{label} contains an absolute temporary path: {leaked}"
        )


def _validate_library_entry(entry, expected, label):
    if type(entry) is not dict:
        raise ArtifactVerificationError(
            f"{label} AvailableLibraries entry must be a dictionary"
        )
    required = set(expected)
    missing = required - set(entry)
    unknown = set(entry) - required
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise ArtifactVerificationError(
            f"{label} AvailableLibraries entry is invalid: {'; '.join(details)}"
        )
    for key, value in expected.items():
        if entry[key] != value:
            field = {
                "SupportedPlatformVariant": "variant",
                "SupportedArchitectures": "architecture",
            }.get(key, key)
            raise ArtifactVerificationError(
                f"{label} {field} must be exactly {value}"
            )


def _validate_module_map(data):
    if data != CANONICAL_MODULE_MAP:
        raise ArtifactVerificationError(
            f"module map must equal the canonical module map for {MODULE_NAME}"
        )


def _validate_lipo(library):
    try:
        result = subprocess.run(
            ["/usr/bin/lipo", "-archs", str(library)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ArtifactVerificationError(f"unable to run lipo: {error}") from error
    if result.returncode != 0:
        raise ArtifactVerificationError(
            f"lipo failed for {library}: {result.stderr.strip()}"
        )
    if result.stdout.split() != ["arm64"]:
        raise ArtifactVerificationError(
            f"lipo architecture must be exactly arm64 for {library}"
        )


def verify_artifact(*, output, source_lock, environment_lock, tracked_swift):
    output = Path(output)
    actual_names = _directory_names(output, "distribution output")
    if actual_names != OUTPUT_NAMES:
        raise ArtifactVerificationError(
            "distribution output must contain only: "
            + ", ".join(sorted(OUTPUT_NAMES))
        )

    try:
        source_pin = SourcePin.load(source_lock)
        environment = BuildEnvironment.load(environment_lock)
    except (SourcePinError, ReleaseMetadataError) as error:
        raise ArtifactVerificationError(f"invalid release lock: {error}") from error

    metadata_path = output / "build-metadata.json"
    metadata_bytes = _read_bytes(metadata_path, "build metadata")
    _reject_temp_path(metadata_bytes, "build metadata")
    metadata = _load_metadata(metadata_bytes)
    source_date_epoch = metadata.get("sourceDateEpoch")
    if type(source_date_epoch) is not int or source_date_epoch < 0:
        raise ArtifactVerificationError(
            "build metadata sourceDateEpoch has the wrong type or value"
        )
    expected_metadata = _expected_metadata(
        source_pin,
        environment,
        source_date_epoch,
    )
    try:
        _require_exact(metadata, expected_metadata, "metadata")
    except ArtifactVerificationError as error:
        raise ArtifactVerificationError(
            f"metadata disagrees with a release lock: {error}"
        ) from error

    framework = output / FRAMEWORK_NAME
    _require_directory_contents(
        framework,
        {"Info.plist", "ios-arm64", "ios-arm64-simulator"},
        "XCFramework root",
    )
    plist_path = framework / "Info.plist"
    plist_bytes = _read_bytes(plist_path, "XCFramework Info.plist")
    _reject_temp_path(plist_bytes, "XCFramework Info.plist")
    try:
        plist = plistlib.loads(plist_bytes)
    except plistlib.InvalidFileException as error:
        raise ArtifactVerificationError(
            f"unable to parse XCFramework Info.plist: {error}"
        ) from error
    if type(plist) is not dict:
        raise ArtifactVerificationError(
            "XCFramework plist root must be a dictionary"
        )
    if set(plist) != {
        "AvailableLibraries",
        "CFBundlePackageType",
        "XCFrameworkFormatVersion",
    }:
        raise ArtifactVerificationError(
            "XCFramework plist must have the exact root keys"
        )
    if (
        plist["CFBundlePackageType"] != "XFWK"
        or plist["XCFrameworkFormatVersion"] != "1.0"
    ):
        raise ArtifactVerificationError(
            "XCFramework plist must have exact package type XFWK and format 1.0"
        )
    available = plist.get("AvailableLibraries")
    if type(available) is not list or len(available) != 2:
        raise ArtifactVerificationError(
            "AvailableLibraries must contain exactly two platform slices"
        )

    expected_device = {
        "BinaryPath": LIBRARY_NAME,
        "LibraryIdentifier": "ios-arm64",
        "LibraryPath": LIBRARY_NAME,
        "HeadersPath": "Headers",
        "SupportedArchitectures": ["arm64"],
        "SupportedPlatform": "ios",
    }
    expected_simulator = {
        "BinaryPath": LIBRARY_NAME,
        "LibraryIdentifier": "ios-arm64-simulator",
        "LibraryPath": LIBRARY_NAME,
        "HeadersPath": "Headers",
        "SupportedArchitectures": ["arm64"],
        "SupportedPlatform": "ios",
        "SupportedPlatformVariant": "simulator",
    }
    entries = {}
    for entry in available:
        if type(entry) is not dict:
            raise ArtifactVerificationError(
                "AvailableLibraries entries must be dictionaries"
            )
        identifier = entry.get("LibraryIdentifier")
        if type(identifier) is not str or identifier in entries:
            raise ArtifactVerificationError(
                "AvailableLibraries identifiers must be unique strings"
            )
        entries[identifier] = entry
    if set(entries) != {"ios-arm64", "ios-arm64-simulator"}:
        raise ArtifactVerificationError(
            "AvailableLibraries must contain exactly the iOS device and "
            "simulator slices"
        )
    _validate_library_entry(
        entries["ios-arm64"],
        expected_device,
        "device",
    )
    _validate_library_entry(
        entries["ios-arm64-simulator"],
        expected_simulator,
        "simulator",
    )

    slice_contents = []
    for identifier in ("ios-arm64", "ios-arm64-simulator"):
        slice_root = framework / identifier
        library = slice_root / LIBRARY_NAME
        headers = slice_root / "Headers"
        _require_directory_contents(
            slice_root,
            {LIBRARY_NAME, "Headers"},
            f"{identifier} slice",
        )
        _require_directory_contents(
            headers,
            {
                HEADER_NAME,
                MODULE_MAP_NAME,
                "fcast_sender_sdk.swift",
            },
            f"{identifier} Headers",
        )
        library_bytes = _read_bytes(library, f"{identifier} static library")
        header = _read_bytes(headers / HEADER_NAME, f"{identifier} header")
        module_map = _read_bytes(
            headers / MODULE_MAP_NAME,
            f"{identifier} module map",
        )
        embedded_swift = _read_bytes(
            headers / "fcast_sender_sdk.swift",
            f"{identifier} embedded Swift",
        )
        _reject_temp_path(library_bytes, f"{identifier} static library")
        _reject_temp_path(header, f"{identifier} header")
        _reject_temp_path(module_map, f"{identifier} module map")
        _reject_temp_path(embedded_swift, f"{identifier} embedded Swift")
        _validate_lipo(library)
        slice_contents.append((header, module_map, embedded_swift))

    if slice_contents[0] != slice_contents[1]:
        raise ArtifactVerificationError(
            "generated header, module map, and Swift binding must be "
            "identical across slices"
        )
    _validate_module_map(slice_contents[0][1])

    generated_swift = _read_bytes(
        output / "FCastSenderSDK.swift",
        "generated Swift",
    )
    tracked_swift_bytes = _read_bytes(
        Path(tracked_swift),
        "tracked Swift",
    )
    _reject_temp_path(generated_swift, "generated Swift")
    if generated_swift != tracked_swift_bytes:
        raise ArtifactVerificationError(
            "generated Swift does not equal the tracked binding"
        )
    if slice_contents[0][2] != generated_swift:
        raise ArtifactVerificationError(
            "embedded Swift does not equal the generated Swift binding"
        )


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-lock", required=True)
    parser.add_argument("--environment-lock", required=True)
    parser.add_argument("--tracked-swift", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    arguments = _parse_args(argv)
    try:
        verify_artifact(
            output=arguments.output,
            source_lock=arguments.source_lock,
            environment_lock=arguments.environment_lock,
            tracked_swift=arguments.tracked_swift,
        )
    except ArtifactVerificationError as error:
        print(f"artifact verification failed: {error}", file=sys.stderr)
        return 1
    print("artifact verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
