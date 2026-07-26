import argparse
import copy
import hashlib
import json
import os
import plistlib
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.source_pin import (
    EXPECTED_CARGO_PACKAGE,
    EXPECTED_COMMIT,
    EXPECTED_FEATURES,
    EXPECTED_REPOSITORY,
    EXPECTED_RUST_TARGETS,
    EXPECTED_RUST_TOOLCHAIN,
    EXPECTED_TAG,
    SourcePin,
    SourcePinError,
)


RELEASE_REPOSITORY = "https://github.com/ayooooo123/fcast-sender-sdk-swift"
RELEASE_ASSET = "fcast_sender_sdk.xcframework.zip"
VERSION_PATTERN = re.compile(r"0\.0\.8-mediastorm\.[1-9][0-9]*")
LOWERCASE_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
LOWERCASE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
BINARY_TARGET = "fcast_sender_sdkFFI"
PACKAGE_PRODUCT = "FCastSenderSDK"

EXPECTED_ENVIRONMENT = {
    "schemaVersion": 1,
    "runner": "macos-26",
    "xcodeVersion": "26.6",
    "xcodeBuildVersion": "17F113",
    "swiftVersion": "6.3.3",
    "swiftLanguageRevision": "swiftlang-6.3.3.1.3",
    "rustVersion": "1.96.1",
    "zipVersion": "3.0",
}


class ReleaseMetadataError(ValueError):
    """Raised when release metadata is not canonical or immutable."""


@dataclass(frozen=True)
class BuildEnvironment:
    schema_version: int
    runner: str
    xcode_version: str
    xcode_build_version: str
    swift_version: str
    swift_language_revision: str
    rust_version: str
    zip_version: str

    @classmethod
    def load(cls, path):
        lock_path = Path(path)
        try:
            payload = json.loads(
                lock_path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_object_keys,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            detail = getattr(error, "msg", str(error))
            raise ReleaseMetadataError(
                f"{lock_path} must contain valid JSON encoded as UTF-8: "
                f"{detail}"
            ) from error
        except OSError as error:
            raise ReleaseMetadataError(
                f"unable to read build environment {lock_path}: {error}"
            ) from error

        if type(payload) is not dict:
            raise ReleaseMetadataError(
                "build environment root must be a JSON object"
            )

        expected_keys = set(EXPECTED_ENVIRONMENT)
        actual_keys = set(payload)
        missing = expected_keys - actual_keys
        if missing:
            raise ReleaseMetadataError(
                "build environment is missing required keys: "
                + ", ".join(sorted(missing))
            )
        unknown = actual_keys - expected_keys
        if unknown:
            raise ReleaseMetadataError(
                "build environment contains unknown keys: "
                + ", ".join(sorted(unknown))
            )

        if type(payload["schemaVersion"]) is not int:
            raise ReleaseMetadataError(
                "schemaVersion must be a JSON integer"
            )
        for key in expected_keys - {"schemaVersion"}:
            if type(payload[key]) is not str:
                raise ReleaseMetadataError(f"{key} must be a JSON string")

        for key, expected in EXPECTED_ENVIRONMENT.items():
            if payload[key] != expected:
                raise ReleaseMetadataError(
                    f"{key} must be exactly {expected}"
                )

        return cls(
            schema_version=payload["schemaVersion"],
            runner=payload["runner"],
            xcode_version=payload["xcodeVersion"],
            xcode_build_version=payload["xcodeBuildVersion"],
            swift_version=payload["swiftVersion"],
            swift_language_revision=payload["swiftLanguageRevision"],
            rust_version=payload["rustVersion"],
            zip_version=payload["zipVersion"],
        )

    def as_lock_payload(self):
        return {
            "schemaVersion": self.schema_version,
            "runner": self.runner,
            "xcodeVersion": self.xcode_version,
            "xcodeBuildVersion": self.xcode_build_version,
            "swiftVersion": self.swift_version,
            "swiftLanguageRevision": self.swift_language_revision,
            "rustVersion": self.rust_version,
            "zipVersion": self.zip_version,
        }


def release_url(version: str) -> str:
    _validate_version(version)
    return (
        f"{RELEASE_REPOSITORY}/releases/download/{version}/{RELEASE_ASSET}"
    )


def render_package(version: str, checksum: str) -> str:
    asset_url = release_url(version)
    _validate_sha256(checksum, "checksum")
    return f"""// swift-tools-version: 6.1

import PackageDescription

let url = "{asset_url}"
let checksum = "{checksum}"

let package = Package(
    name: "FCastSenderSDK",
    platforms: [
        .iOS(.v16)
    ],
    products: [
        .library(name: "FCastSenderSDK", targets: ["FCastSenderSDK"])
    ],
    targets: [
        .binaryTarget(name: "fcast_sender_sdkFFI", url: url, checksum: checksum),
        .target(
            name: "FCastSenderSDK",
            dependencies: [.target(name: "fcast_sender_sdkFFI")]
        )
    ]
)
"""


def build_provenance(
    *,
    version: str,
    distribution_commit: str,
    source_pin: SourcePin,
    environment: BuildEnvironment,
    archive_sha256: str,
    binary_target: str,
    product: str,
    artifact_url: str,
    swiftpm_checksum: str,
) -> dict[str, object]:
    _validate_version(version)
    _validate_commit(distribution_commit, "distribution commit")
    _validate_source_pin(source_pin)
    _validate_environment(environment)
    _validate_sha256(archive_sha256, "archive SHA-256")
    if binary_target != BINARY_TARGET:
        raise ReleaseMetadataError(
            f"binary target must be exactly {BINARY_TARGET}"
        )
    if product != PACKAGE_PRODUCT:
        raise ReleaseMetadataError(
            f"product must be exactly {PACKAGE_PRODUCT}"
        )
    expected_url = release_url(version)
    if artifact_url != expected_url:
        raise ReleaseMetadataError(
            f"artifact URL must be exactly {expected_url}"
        )
    _validate_sha256(swiftpm_checksum, "SwiftPM checksum")
    if archive_sha256 != swiftpm_checksum:
        raise ReleaseMetadataError(
            "archive SHA-256 and SwiftPM checksum must match exactly "
            "for the ZIP binary target"
        )

    return {
        "schemaVersion": 1,
        "version": version,
        "tag": version,
        "distributionInputCommit": distribution_commit,
        "source": {
            "repository": source_pin.repository,
            "tag": source_pin.tag,
            "commit": source_pin.commit,
        },
        "build": {
            "runner": environment.runner,
            "rustVersion": environment.rust_version,
            "rustTargets": list(source_pin.rust_targets),
            "xcodeVersion": environment.xcode_version,
            "xcodeBuildVersion": environment.xcode_build_version,
            "swiftVersion": environment.swift_version,
            "swiftLanguageRevision": environment.swift_language_revision,
            "zipVersion": environment.zip_version,
        },
        "package": {
            "binaryTarget": binary_target,
            "product": product,
            "assetURL": artifact_url,
            "swiftPMChecksum": swiftpm_checksum,
        },
        "archiveSHA256": archive_sha256,
        "sourcePatched": False,
    }


def validate_provenance(
    provenance,
    *,
    version,
    distribution_commit,
    source_pin,
    environment,
    archive_sha256,
    binary_target,
    product,
    artifact_url,
    swiftpm_checksum,
):
    expected = build_provenance(
        version=version,
        distribution_commit=distribution_commit,
        source_pin=source_pin,
        environment=environment,
        archive_sha256=archive_sha256,
        binary_target=binary_target,
        product=product,
        artifact_url=artifact_url,
        swiftpm_checksum=swiftpm_checksum,
    )
    _require_exact_value(provenance, expected, "provenance")


def augment_provenance(
    base: dict[str, object],
    release_tag_commit: str,
) -> dict[str, object]:
    """Return the canonical release-asset provenance for an existing tag commit."""
    _validate_commit(release_tag_commit, "release tag commit")
    if type(base) is not dict:
        raise ReleaseMetadataError("base provenance must be a JSON object")
    if "releaseTagCommit" in base:
        raise ReleaseMetadataError(
            "base provenance already contains releaseTagCommit"
        )
    _verify_base_provenance_schema(base)
    augmented = copy.deepcopy(base)
    augmented["releaseTagCommit"] = release_tag_commit
    return augmented


def verify_provenance(
    provenance: dict[str, object],
    *,
    package_swift: str,
    source_pin: SourcePin,
    release_input_commit: str,
    release_tag_commit: str | None,
    archive: Path | None,
) -> None:
    """Require exact manifest, lock, commit, provenance, and optional ZIP agreement."""
    _validate_commit(release_input_commit, "release input commit")
    _validate_source_pin(source_pin)
    version, checksum = _parse_canonical_package(package_swift)
    expected = build_provenance(
        version=version,
        distribution_commit=release_input_commit,
        source_pin=source_pin,
        environment=_approved_environment(),
        archive_sha256=checksum,
        binary_target=BINARY_TARGET,
        product=PACKAGE_PRODUCT,
        artifact_url=release_url(version),
        swiftpm_checksum=checksum,
    )
    if release_tag_commit is not None:
        expected = augment_provenance(expected, release_tag_commit)
    _require_exact_value(provenance, expected, "provenance")

    if archive is not None:
        archive_path = Path(archive)
        try:
            archive_stat = archive_path.lstat()
        except OSError as error:
            raise ReleaseMetadataError(
                f"unable to inspect archive {archive_path}: {error}"
            ) from error
        if stat.S_ISLNK(archive_stat.st_mode) or not stat.S_ISREG(
            archive_stat.st_mode
        ):
            raise ReleaseMetadataError(
                "archive must be a non-symlink regular file"
            )
        python_checksum = _sha256_file(archive_path)
        if python_checksum != checksum:
            raise ReleaseMetadataError(
                "archive Python SHA-256 disagrees with Package.swift"
            )
        try:
            completed = subprocess.run(
                ["swift", "package", "compute-checksum", str(archive_path)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            raise ReleaseMetadataError(
                f"unable to compute SwiftPM checksum: {detail}"
            ) from error
        swiftpm_checksum = completed.stdout.strip()
        _validate_sha256(swiftpm_checksum, "SwiftPM checksum")
        if swiftpm_checksum != checksum:
            raise ReleaseMetadataError(
                "archive SwiftPM checksum disagrees with Package.swift"
            )


def serialize_json(payload: object) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def normalize_xcframework_plist(plist_bytes: bytes) -> bytes:
    """Return one deterministic encoding with device before simulator."""
    if type(plist_bytes) is not bytes:
        raise ReleaseMetadataError("XCFramework Info.plist input must be bytes")
    try:
        payload = plistlib.loads(plist_bytes)
    except (plistlib.InvalidFileException, ValueError, TypeError) as error:
        raise ReleaseMetadataError(
            f"XCFramework Info.plist must be a valid property list: {error}"
        ) from error
    if type(payload) is not dict:
        raise ReleaseMetadataError(
            "XCFramework Info.plist root must be a dictionary"
        )
    expected_root_keys = {
        "AvailableLibraries",
        "CFBundlePackageType",
        "XCFrameworkFormatVersion",
    }
    if set(payload) != expected_root_keys:
        raise ReleaseMetadataError(
            "XCFramework Info.plist contains unknown or missing root keys"
        )
    if payload["CFBundlePackageType"] != "XFWK":
        raise ReleaseMetadataError(
            "XCFramework CFBundlePackageType must be exactly XFWK"
        )
    if payload["XCFrameworkFormatVersion"] != "1.0":
        raise ReleaseMetadataError(
            "XCFramework format version must be exactly 1.0"
        )
    libraries = payload["AvailableLibraries"]
    if type(libraries) is not list or len(libraries) != 2:
        raise ReleaseMetadataError(
            "AvailableLibraries must contain exactly two entries with "
            "LibraryIdentifier values"
        )
    identifiers = []
    for index, entry in enumerate(libraries):
        if type(entry) is not dict:
            raise ReleaseMetadataError(
                f"AvailableLibraries[{index}] with LibraryIdentifier "
                "must be a dictionary"
            )
        identifier = entry.get("LibraryIdentifier")
        if type(identifier) is not str:
            raise ReleaseMetadataError(
                f"AvailableLibraries[{index}].LibraryIdentifier "
                "must be a string"
            )
        identifiers.append(identifier)
    expected_identifiers = {"ios-arm64", "ios-arm64-simulator"}
    if set(identifiers) != expected_identifiers or len(set(identifiers)) != 2:
        raise ReleaseMetadataError(
            "LibraryIdentifier values must be exactly ios-arm64 and "
            "ios-arm64-simulator without duplicates"
        )
    libraries.sort(key=lambda entry: entry["LibraryIdentifier"])
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def atomic_write_text(path, text):
    output = Path(path)
    if type(text) is not str:
        raise ReleaseMetadataError("atomic writer input must be text")
    parent = output.parent
    name = output.name
    if not name or name in (".", ".."):
        raise ReleaseMetadataError("atomic writer output must name a file")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as error:
        raise ReleaseMetadataError(
            f"output parent must be a real non-symlink directory: {error}"
        ) from error
    temporary_name = None
    file_fd = None
    try:
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                raise ReleaseMetadataError(
                    "atomic writer output must not be a symlink"
                )
            if not stat.S_ISREG(existing.st_mode):
                raise ReleaseMetadataError(
                    "atomic writer output must be a regular file"
                )
        for _ in range(128):
            temporary_name = f".{name}.tmp-{secrets.token_hex(12)}"
            try:
                file_fd = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                break
            except FileExistsError:
                temporary_name = None
        if file_fd is None:
            raise ReleaseMetadataError(
                "unable to allocate secure atomic-writer temporary file"
            )
        payload = text.encode("utf-8")
        with os.fdopen(file_fd, "wb", closefd=True) as stream:
            file_fd = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
        os.fsync(parent_fd)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def render_build_inputs(source_pin, environment):
    _validate_source_pin(source_pin)
    _validate_environment(environment)
    fields = (
        source_pin.repository,
        source_pin.tag,
        source_pin.commit,
        source_pin.rust_toolchain,
        ",".join(source_pin.rust_targets),
        source_pin.cargo_package,
        environment.runner,
        environment.xcode_version,
        environment.xcode_build_version,
        environment.swift_version,
        environment.swift_language_revision,
        environment.rust_version,
        environment.zip_version,
    )
    if any("\t" in field or "\n" in field or "\r" in field for field in fields):
        raise ReleaseMetadataError(
            "build inputs must not contain tab or newline characters"
        )
    return "\t".join(fields) + "\n"


def render_build_metadata(source_pin, environment, source_date_epoch):
    _validate_source_pin(source_pin)
    _validate_environment(environment)
    if type(source_date_epoch) is not int or source_date_epoch < 0:
        raise ReleaseMetadataError(
            "source date epoch must be a non-negative JSON integer"
        )
    return serialize_json(
        {
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
    )


def _reject_duplicate_object_keys(pairs):
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise ReleaseMetadataError(
                f"duplicate JSON object key is not allowed: {key}"
            )
        parsed[key] = value
    return parsed


def _validate_version(version):
    if type(version) is not str or VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseMetadataError(
            "version must match 0.0.8-mediastorm.<positive integer>"
        )


def _validate_sha256(value, field):
    if (
        type(value) is not str
        or LOWERCASE_SHA256_PATTERN.fullmatch(value) is None
    ):
        raise ReleaseMetadataError(
            f"{field} must contain exactly 64 lowercase hexadecimal characters"
        )


def _validate_commit(value, field):
    if (
        type(value) is not str
        or LOWERCASE_COMMIT_PATTERN.fullmatch(value) is None
    ):
        raise ReleaseMetadataError(
            f"{field} must contain exactly 40 lowercase hexadecimal characters"
        )


def _validate_source_pin(source_pin):
    if type(source_pin) is not SourcePin:
        raise ReleaseMetadataError("source pin must be a SourcePin")
    expected = {
        "schema_version": 1,
        "repository": EXPECTED_REPOSITORY,
        "tag": EXPECTED_TAG,
        "commit": EXPECTED_COMMIT,
        "rust_toolchain": EXPECTED_RUST_TOOLCHAIN,
        "rust_targets": EXPECTED_RUST_TARGETS,
        "cargo_package": EXPECTED_CARGO_PACKAGE,
        "features": EXPECTED_FEATURES,
    }
    for field, value in expected.items():
        if getattr(source_pin, field) != value:
            raise ReleaseMetadataError(
                f"source pin {field.replace('_', ' ')} disagrees with "
                f"source.lock.json; expected {value}"
            )


def _validate_environment(environment):
    if type(environment) is not BuildEnvironment:
        raise ReleaseMetadataError(
            "environment must be a BuildEnvironment"
        )
    payload = environment.as_lock_payload()
    for key, expected in EXPECTED_ENVIRONMENT.items():
        if payload[key] != expected:
            raise ReleaseMetadataError(
                f"environment {key} disagrees with "
                f"build-environment.lock.json; expected {expected}"
            )


def _approved_environment():
    return BuildEnvironment(
        schema_version=EXPECTED_ENVIRONMENT["schemaVersion"],
        runner=EXPECTED_ENVIRONMENT["runner"],
        xcode_version=EXPECTED_ENVIRONMENT["xcodeVersion"],
        xcode_build_version=EXPECTED_ENVIRONMENT["xcodeBuildVersion"],
        swift_version=EXPECTED_ENVIRONMENT["swiftVersion"],
        swift_language_revision=EXPECTED_ENVIRONMENT[
            "swiftLanguageRevision"
        ],
        rust_version=EXPECTED_ENVIRONMENT["rustVersion"],
        zip_version=EXPECTED_ENVIRONMENT["zipVersion"],
    )


def _parse_canonical_package(package_swift):
    if type(package_swift) is not str:
        raise ReleaseMetadataError("Package.swift must be text")
    url_matches = re.findall(r'^let url = "([^"]+)"$', package_swift, re.M)
    checksum_matches = re.findall(
        r'^let checksum = "([^"]*)"$',
        package_swift,
        re.M,
    )
    if len(url_matches) != 1 or len(checksum_matches) != 1:
        raise ReleaseMetadataError(
            "Package.swift must contain one canonical URL and checksum"
        )
    url = url_matches[0]
    checksum = checksum_matches[0]
    _validate_sha256(checksum, "Package.swift checksum")
    url_pattern = re.compile(
        re.escape(RELEASE_REPOSITORY)
        + r"/releases/download/([^/]+)/"
        + re.escape(RELEASE_ASSET)
    )
    match = url_pattern.fullmatch(url)
    if match is None:
        raise ReleaseMetadataError(
            "Package.swift URL must be the immutable versioned GitHub asset"
        )
    version = match.group(1)
    _validate_version(version)
    canonical = render_package(version, checksum)
    if package_swift != canonical:
        raise ReleaseMetadataError(
            "Package.swift must exactly equal the canonical rendered manifest"
        )
    return version, checksum


def _verify_base_provenance_schema(base):
    if type(base) is not dict:
        raise ReleaseMetadataError("base provenance must be a JSON object")
    required = {
        "schemaVersion",
        "version",
        "tag",
        "distributionInputCommit",
        "source",
        "build",
        "package",
        "archiveSHA256",
        "sourcePatched",
    }
    unknown = set(base) - required
    if unknown:
        raise ReleaseMetadataError(
            "base provenance contains unknown keys: "
            + ", ".join(sorted(unknown))
        )
    missing = required - set(base)
    if missing:
        raise ReleaseMetadataError(
            "base provenance is missing keys: " + ", ".join(sorted(missing))
        )
    if type(base["version"]) is not str:
        raise ReleaseMetadataError("base provenance version must be text")
    if type(base["distributionInputCommit"]) is not str:
        raise ReleaseMetadataError(
            "base provenance distributionInputCommit must be text"
        )
    if type(base["package"]) is not dict:
        raise ReleaseMetadataError("base provenance package must be an object")
    package_keys = {
        "binaryTarget",
        "product",
        "assetURL",
        "swiftPMChecksum",
    }
    if set(base["package"]) != package_keys:
        raise ReleaseMetadataError(
            "base provenance package contains unknown or missing keys"
        )
    version = base["version"]
    release_input_commit = base["distributionInputCommit"]
    checksum = base["package"]["swiftPMChecksum"]
    if type(checksum) is not str:
        raise ReleaseMetadataError(
            "base provenance SwiftPM checksum must be text"
        )
    expected = build_provenance(
        version=version,
        distribution_commit=release_input_commit,
        source_pin=_approved_source_pin(),
        environment=_approved_environment(),
        archive_sha256=checksum,
        binary_target=BINARY_TARGET,
        product=PACKAGE_PRODUCT,
        artifact_url=release_url(version),
        swiftpm_checksum=checksum,
    )
    _require_exact_value(base, expected, "base provenance")


def _approved_source_pin():
    return SourcePin(
        schema_version=1,
        repository=EXPECTED_REPOSITORY,
        tag=EXPECTED_TAG,
        commit=EXPECTED_COMMIT,
        rust_toolchain=EXPECTED_RUST_TOOLCHAIN,
        rust_targets=EXPECTED_RUST_TARGETS,
        cargo_package=EXPECTED_CARGO_PACKAGE,
        features=EXPECTED_FEATURES,
    )


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReleaseMetadataError(
            f"unable to hash archive {path}: {error}"
        ) from error
    return digest.hexdigest()


def _require_exact_value(actual, expected, path):
    if type(actual) is not type(expected):
        raise ReleaseMetadataError(
            f"{path} has the wrong type; expected {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        actual_keys = set(actual)
        expected_keys = set(expected)
        unknown = actual_keys - expected_keys
        if unknown:
            raise ReleaseMetadataError(
                f"{path} contains unknown keys: "
                + ", ".join(sorted(unknown))
            )
        missing = expected_keys - actual_keys
        if missing:
            raise ReleaseMetadataError(
                f"{path} is missing keys: " + ", ".join(sorted(missing))
            )
        for key in expected:
            _require_exact_value(actual[key], expected[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ReleaseMetadataError(
                f"{path} has the wrong number of entries"
            )
        for index, (actual_item, expected_item) in enumerate(
            zip(actual, expected)
        ):
            _require_exact_value(
                actual_item,
                expected_item,
                f"{path}[{index}]",
            )
        return
    if actual != expected:
        raise ReleaseMetadataError(
            f"{path} disagrees with the release locks; expected {expected}"
        )


def _build_parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    package = commands.add_parser("render-package")
    package.add_argument("--version", required=True)
    package.add_argument("--checksum", required=True)
    package.add_argument("--output", required=True)

    inputs = commands.add_parser("build-inputs")
    inputs.add_argument("--source-lock", required=True)
    inputs.add_argument("--environment-lock", required=True)

    metadata = commands.add_parser("render-build-metadata")
    metadata.add_argument("--source-lock", required=True)
    metadata.add_argument("--environment-lock", required=True)
    metadata.add_argument("--source-date-epoch", required=True, type=int)
    metadata.add_argument("--output", required=True)

    provenance = commands.add_parser("render-provenance")
    provenance.add_argument("--version", required=True)
    provenance.add_argument("--release-input-commit", required=True)
    provenance.add_argument("--checksum", required=True)
    provenance.add_argument("--source-lock", required=True)
    provenance.add_argument("--environment-lock", required=True)
    provenance.add_argument("--output", required=True)

    augment = commands.add_parser("augment-provenance")
    augment.add_argument("--base", required=True)
    augment.add_argument("--release-tag-commit", required=True)
    augment.add_argument("--output", required=True)

    verify = commands.add_parser("verify-provenance")
    verify.add_argument("--provenance", required=True)
    verify.add_argument("--package", required=True)
    verify.add_argument("--source-lock", required=True)
    verify.add_argument("--release-input-commit", required=True)
    verify.add_argument("--release-tag-commit")
    verify.add_argument("--archive")

    normalize = commands.add_parser("normalize-xcframework-plist")
    normalize.add_argument("--plist", required=True)
    return parser


def _load_json_object(path, field):
    input_path = Path(path)
    try:
        payload = json.loads(
            input_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        detail = getattr(error, "msg", str(error))
        raise ReleaseMetadataError(
            f"{field} must contain valid JSON encoded as UTF-8: {detail}"
        ) from error
    except OSError as error:
        raise ReleaseMetadataError(f"unable to read {field}: {error}") from error
    if type(payload) is not dict:
        raise ReleaseMetadataError(f"{field} must be a JSON object")
    return payload


def main(argv=None):
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "render-package":
            rendered = render_package(arguments.version, arguments.checksum)
            atomic_write_text(arguments.output, rendered)
        elif arguments.command == "build-inputs":
            source_pin = SourcePin.load(arguments.source_lock)
            environment = BuildEnvironment.load(arguments.environment_lock)
            sys.stdout.write(render_build_inputs(source_pin, environment))
        elif arguments.command == "render-build-metadata":
            source_pin = SourcePin.load(arguments.source_lock)
            environment = BuildEnvironment.load(arguments.environment_lock)
            rendered = render_build_metadata(
                source_pin,
                environment,
                arguments.source_date_epoch,
            )
            atomic_write_text(arguments.output, rendered)
        elif arguments.command == "render-provenance":
            source_pin = SourcePin.load(arguments.source_lock)
            environment = BuildEnvironment.load(arguments.environment_lock)
            payload = build_provenance(
                version=arguments.version,
                distribution_commit=arguments.release_input_commit,
                source_pin=source_pin,
                environment=environment,
                archive_sha256=arguments.checksum,
                binary_target=BINARY_TARGET,
                product=PACKAGE_PRODUCT,
                artifact_url=release_url(arguments.version),
                swiftpm_checksum=arguments.checksum,
            )
            atomic_write_text(arguments.output, serialize_json(payload))
        elif arguments.command == "augment-provenance":
            base = _load_json_object(arguments.base, "base provenance")
            payload = augment_provenance(
                base,
                arguments.release_tag_commit,
            )
            atomic_write_text(arguments.output, serialize_json(payload))
        elif arguments.command == "verify-provenance":
            payload = _load_json_object(
                arguments.provenance,
                "provenance",
            )
            try:
                package_swift = Path(arguments.package).read_text(
                    encoding="utf-8"
                )
            except (OSError, UnicodeDecodeError) as error:
                raise ReleaseMetadataError(
                    f"unable to read Package.swift: {error}"
                ) from error
            source_pin = SourcePin.load(arguments.source_lock)
            verify_provenance(
                payload,
                package_swift=package_swift,
                source_pin=source_pin,
                release_input_commit=arguments.release_input_commit,
                release_tag_commit=arguments.release_tag_commit,
                archive=(
                    Path(arguments.archive)
                    if arguments.archive is not None
                    else None
                ),
            )
        else:
            plist_path = Path(arguments.plist)
            try:
                plist_bytes = plist_path.read_bytes()
            except OSError as error:
                raise ReleaseMetadataError(
                    f"unable to read XCFramework Info.plist: {error}"
                ) from error
            normalized = normalize_xcframework_plist(plist_bytes)
            try:
                normalized_text = normalized.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ReleaseMetadataError(
                    "normalized XCFramework Info.plist must be UTF-8"
                ) from error
            atomic_write_text(plist_path, normalized_text)
    except (OSError, SourcePinError, ReleaseMetadataError) as error:
        print(f"release metadata error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
