import argparse
import json
import plistlib
import re
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


def _load_metadata(path):
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except json.JSONDecodeError as error:
        raise ArtifactVerificationError(
            f"build metadata must be valid JSON: {error.msg}"
        ) from error
    except OSError as error:
        raise ArtifactVerificationError(
            f"unable to read build metadata: {error}"
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
    try:
        if path.is_symlink():
            raise ArtifactVerificationError(f"{label} must not be a symlink")
        return path.read_bytes()
    except OSError as error:
        raise ArtifactVerificationError(f"unable to read {label}: {error}") from error


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
    optional = {"BinaryPath"}
    missing = required - set(entry)
    unknown = set(entry) - required - optional
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
    if "BinaryPath" in entry and entry["BinaryPath"] != LIBRARY_NAME:
        raise ArtifactVerificationError(
            f"{label} BinaryPath must be exactly {LIBRARY_NAME}"
        )


def _validate_module_map(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactVerificationError(
            "module map must be UTF-8 text"
        ) from error
    declarations = re.findall(r"(?m)^\s*module\s+([A-Za-z0-9_]+)\s*\{", text)
    if declarations != [MODULE_NAME]:
        raise ArtifactVerificationError(
            f"module map must export exactly module {MODULE_NAME}"
        )
    if re.findall(r'(?m)^\s*header\s+"([^"]+)"\s*$', text) != [HEADER_NAME]:
        raise ArtifactVerificationError(
            f"module {MODULE_NAME} must reference exactly header {HEADER_NAME}"
        )
    if len(re.findall(r"(?m)^\s*export\s+\*\s*$", text)) != 1:
        raise ArtifactVerificationError(
            f"module {MODULE_NAME} must contain exactly one export *"
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
    if output.is_symlink() or not output.is_dir():
        raise ArtifactVerificationError(
            "distribution output must be a real directory"
        )
    actual_names = {entry.name for entry in output.iterdir()}
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
    metadata = _load_metadata(metadata_path)
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
    if framework.is_symlink() or not framework.is_dir():
        raise ArtifactVerificationError("XCFramework must be a real directory")
    plist_path = framework / "Info.plist"
    try:
        with plist_path.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise ArtifactVerificationError(
            f"unable to parse XCFramework Info.plist: {error}"
        ) from error
    available = plist.get("AvailableLibraries")
    if type(available) is not list or len(available) != 2:
        raise ArtifactVerificationError(
            "AvailableLibraries must contain exactly two platform slices"
        )

    expected_device = {
        "LibraryIdentifier": "ios-arm64",
        "LibraryPath": LIBRARY_NAME,
        "HeadersPath": "Headers",
        "SupportedArchitectures": ["arm64"],
        "SupportedPlatform": "ios",
    }
    expected_simulator = {
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
        if (
            slice_root.is_symlink()
            or library.is_symlink()
            or headers.is_symlink()
            or not library.is_file()
            or not headers.is_dir()
        ):
            raise ArtifactVerificationError(
                f"{identifier} must contain the exact library and Headers path"
            )
        if {entry.name for entry in headers.iterdir()} != {
            HEADER_NAME,
            MODULE_MAP_NAME,
            "fcast_sender_sdk.swift",
        }:
            raise ArtifactVerificationError(
                f"{identifier} Headers must contain the exact generated "
                "header, module map, and Swift binding"
            )
        header = _read_bytes(headers / HEADER_NAME, f"{identifier} header")
        module_map = _read_bytes(
            headers / MODULE_MAP_NAME,
            f"{identifier} module map",
        )
        embedded_swift = _read_bytes(
            headers / "fcast_sender_sdk.swift",
            f"{identifier} embedded Swift",
        )
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
