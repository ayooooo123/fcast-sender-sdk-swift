import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.release_metadata as release_metadata_module

from scripts.release_metadata import (
    BuildEnvironment,
    ReleaseMetadataError,
    build_provenance,
    release_url,
    render_package,
    serialize_json,
    validate_provenance,
)
from scripts.source_pin import SourcePin


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = REPOSITORY_ROOT / "source.lock.json"
BUILD_ENVIRONMENT_LOCK = REPOSITORY_ROOT / "build-environment.lock.json"
VERSION = "0.0.8-mediastorm.1"
CHECKSUM = "a" * 64
ARCHIVE_SHA256 = CHECKSUM
DISTRIBUTION_COMMIT = "c" * 40
EXPECTED_URL = (
    "https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/"
    "0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip"
)


def expected_environment_payload():
    return {
        "schemaVersion": 1,
        "runner": "macos-26",
        "xcodeVersion": "26.6",
        "xcodeBuildVersion": "17F113",
        "swiftVersion": "6.3.3",
        "swiftLanguageRevision": "swiftlang-6.3.3.1.3",
        "rustVersion": "1.96.1",
        "zipVersion": "3.0",
    }


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.source_pin = SourcePin.load(SOURCE_LOCK)
        self.environment = BuildEnvironment.load(BUILD_ENVIRONMENT_LOCK)

    def make_provenance(self):
        return build_provenance(
            version=VERSION,
            distribution_commit=DISTRIBUTION_COMMIT,
            source_pin=self.source_pin,
            environment=self.environment,
            archive_sha256=ARCHIVE_SHA256,
            binary_target="fcast_sender_sdkFFI",
            product="FCastSenderSDK",
            artifact_url=EXPECTED_URL,
            swiftpm_checksum=CHECKSUM,
        )

    def write_environment(self, directory, payload):
        path = Path(directory) / "build-environment.lock.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_release_url_is_exact_immutable_versioned_asset(self):
        self.assertEqual(release_url(VERSION), EXPECTED_URL)

    def test_render_package_preserves_public_contract_and_remote_binary(self):
        package = render_package(VERSION, CHECKSUM)

        self.assertTrue(package.startswith("// swift-tools-version: 6.1\n"))
        self.assertIn('name: "FCastSenderSDK"', package)
        self.assertIn(".iOS(.v16)", package)
        self.assertIn(
            '.library(name: "FCastSenderSDK", targets: ["FCastSenderSDK"])',
            package,
        )
        self.assertIn(
            '.binaryTarget(name: "fcast_sender_sdkFFI", url: url, '
            "checksum: checksum)",
            package,
        )
        self.assertIn(EXPECTED_URL, package)
        self.assertNotIn(".binaryTarget(path:", package)
        self.assertTrue(package.endswith("\n"))

    def test_rejects_invalid_checksum_forms(self):
        for checksum in (
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            "../" + "a" * 61,
        ):
            with self.subTest(checksum=checksum):
                with self.assertRaisesRegex(
                    ReleaseMetadataError,
                    "checksum.*64 lowercase hexadecimal",
                ):
                    render_package(VERSION, checksum)

    def test_rejects_non_release_versions(self):
        invalid_versions = (
            "main",
            "latest",
            "0.0.8",
            "0.0.8-mediastorm",
            "0.0.8-mediastorm.0",
            "0.0.8-mediastorm.01",
            "0.0.8-other.1",
            "1.0.0-mediastorm.1",
            "0.0.8-mediastorm.1/asset",
            "../0.0.8-mediastorm.1",
            " 0.0.8-mediastorm.1",
            "0.0.8-mediastorm.1 ",
            "0.0.8-mediastorm.1\n",
        )
        for version in invalid_versions:
            with self.subTest(version=version):
                with self.assertRaisesRegex(
                    ReleaseMetadataError,
                    "version.*0\\.0\\.8-mediastorm",
                ):
                    release_url(version)

    def test_build_provenance_has_exact_release_contract(self):
        provenance = self.make_provenance()

        self.assertEqual(
            provenance,
            {
                "schemaVersion": 1,
                "version": VERSION,
                "tag": VERSION,
                "distributionInputCommit": DISTRIBUTION_COMMIT,
                "source": {
                    "repository": self.source_pin.repository,
                    "tag": self.source_pin.tag,
                    "commit": self.source_pin.commit,
                },
                "build": {
                    "runner": "macos-26",
                    "rustVersion": "1.96.1",
                    "rustTargets": [
                        "aarch64-apple-ios",
                        "aarch64-apple-ios-sim",
                    ],
                    "xcodeVersion": "26.6",
                    "xcodeBuildVersion": "17F113",
                    "swiftVersion": "6.3.3",
                    "swiftLanguageRevision": "swiftlang-6.3.3.1.3",
                    "zipVersion": "3.0",
                },
                "package": {
                    "binaryTarget": "fcast_sender_sdkFFI",
                    "product": "FCastSenderSDK",
                    "assetURL": EXPECTED_URL,
                    "swiftPMChecksum": CHECKSUM,
                },
                "archiveSHA256": ARCHIVE_SHA256,
                "sourcePatched": False,
            },
        )

    def test_build_provenance_rejects_disagreement_with_locks(self):
        wrong_environment = copy.copy(self.environment)
        object.__setattr__(wrong_environment, "rust_version", "nightly")

        with self.assertRaisesRegex(
            ReleaseMetadataError,
            "environment.*rustVersion",
        ):
            build_provenance(
                version=VERSION,
                distribution_commit=DISTRIBUTION_COMMIT,
                source_pin=self.source_pin,
                environment=wrong_environment,
                archive_sha256=ARCHIVE_SHA256,
                binary_target="fcast_sender_sdkFFI",
                product="FCastSenderSDK",
                artifact_url=EXPECTED_URL,
                swiftpm_checksum=CHECKSUM,
            )

        wrong_source = copy.copy(self.source_pin)
        object.__setattr__(wrong_source, "commit", "d" * 40)
        with self.assertRaisesRegex(
            ReleaseMetadataError,
            "source pin.*commit",
        ):
            build_provenance(
                version=VERSION,
                distribution_commit=DISTRIBUTION_COMMIT,
                source_pin=wrong_source,
                environment=self.environment,
                archive_sha256=ARCHIVE_SHA256,
                binary_target="fcast_sender_sdkFFI",
                product="FCastSenderSDK",
                artifact_url=EXPECTED_URL,
                swiftpm_checksum=CHECKSUM,
            )

    def test_build_provenance_rejects_noncanonical_arguments(self):
        cases = {
            "distribution commit": {"distribution_commit": "c" * 39},
            "archive": {"archive_sha256": "B" * 64},
            "binary target": {"binary_target": "Other"},
            "product": {"product": "Other"},
            "artifact URL": {"artifact_url": EXPECTED_URL + "?download=1"},
            "SwiftPM checksum": {"swiftpm_checksum": "G" * 64},
        }
        defaults = {
            "version": VERSION,
            "distribution_commit": DISTRIBUTION_COMMIT,
            "source_pin": self.source_pin,
            "environment": self.environment,
            "archive_sha256": ARCHIVE_SHA256,
            "binary_target": "fcast_sender_sdkFFI",
            "product": "FCastSenderSDK",
            "artifact_url": EXPECTED_URL,
            "swiftpm_checksum": CHECKSUM,
        }
        for message, changes in cases.items():
            with self.subTest(case=message):
                arguments = defaults | changes
                with self.assertRaisesRegex(ReleaseMetadataError, message):
                    build_provenance(**arguments)

    def test_build_provenance_rejects_checksum_disagreement(self):
        with self.assertRaisesRegex(
            ReleaseMetadataError,
            "archive SHA-256.*SwiftPM checksum.*match",
        ):
            build_provenance(
                version=VERSION,
                distribution_commit=DISTRIBUTION_COMMIT,
                source_pin=self.source_pin,
                environment=self.environment,
                archive_sha256="b" * 64,
                binary_target="fcast_sender_sdkFFI",
                product="FCastSenderSDK",
                artifact_url=EXPECTED_URL,
                swiftpm_checksum=CHECKSUM,
            )

    def test_validate_provenance_rejects_unknown_or_disagreeing_fields(self):
        provenance = self.make_provenance()
        cases = (
            ("unknown", lambda value: value.update({"unexpected": True})),
            (
                "version",
                lambda value: value.update({"version": "0.0.8-mediastorm.2"}),
            ),
            (
                "source.*commit",
                lambda value: value["source"].update({"commit": "d" * 40}),
            ),
            (
                "build.*xcodeBuildVersion",
                lambda value: value["build"].update(
                    {"xcodeBuildVersion": "different"}
                ),
            ),
            (
                "package.*product",
                lambda value: value["package"].update({"product": "Other"}),
            ),
        )

        for message, mutate in cases:
            with self.subTest(message=message):
                changed = copy.deepcopy(provenance)
                mutate(changed)
                with self.assertRaisesRegex(ReleaseMetadataError, message):
                    validate_provenance(
                        changed,
                        version=VERSION,
                        distribution_commit=DISTRIBUTION_COMMIT,
                        source_pin=self.source_pin,
                        environment=self.environment,
                        archive_sha256=ARCHIVE_SHA256,
                        binary_target="fcast_sender_sdkFFI",
                        product="FCastSenderSDK",
                        artifact_url=EXPECTED_URL,
                        swiftpm_checksum=CHECKSUM,
                    )

    def test_build_environment_loads_exact_checked_in_lock(self):
        self.assertEqual(self.environment.schema_version, 1)
        self.assertEqual(self.environment.runner, "macos-26")
        self.assertEqual(self.environment.xcode_version, "26.6")
        self.assertEqual(self.environment.xcode_build_version, "17F113")
        self.assertEqual(self.environment.swift_version, "6.3.3")
        self.assertEqual(
            self.environment.swift_language_revision,
            "swiftlang-6.3.3.1.3",
        )
        self.assertEqual(self.environment.rust_version, "1.96.1")
        self.assertEqual(self.environment.zip_version, "3.0")

    def test_build_environment_rejects_unknown_missing_wrong_types_and_values(self):
        cases = (
            ("missing.*runner", lambda value: value.pop("runner")),
            ("unknown.*extra", lambda value: value.update({"extra": True})),
            (
                "schemaVersion.*integer",
                lambda value: value.update({"schemaVersion": True}),
            ),
            ("runner.*string", lambda value: value.update({"runner": 26})),
            (
                "xcodeVersion.*exactly 26\\.6",
                lambda value: value.update({"xcodeVersion": "26.7"}),
            ),
        )
        for message, mutate in cases:
            with self.subTest(message=message):
                payload = expected_environment_payload()
                mutate(payload)
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_environment(directory, payload)
                    with self.assertRaisesRegex(ReleaseMetadataError, message):
                        BuildEnvironment.load(path)

    def test_build_environment_rejects_duplicate_json_keys(self):
        raw = json.dumps(expected_environment_payload()).replace(
            '"runner": ',
            '"runner": "attacker", "runner": ',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "build-environment.lock.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(
                ReleaseMetadataError,
                "duplicate.*runner",
            ):
                BuildEnvironment.load(path)

    def test_serialize_json_is_canonical_utf8_text(self):
        payload = {"z": "café", "a": {"second": 2, "first": 1}}
        rendered = serialize_json(payload)

        self.assertEqual(
            rendered,
            '{\n'
            '  "a": {\n'
            '    "first": 1,\n'
            '    "second": 2\n'
            '  },\n'
            '  "z": "café"\n'
            '}\n',
        )
        self.assertIn(b"caf\xc3\xa9", rendered.encode("utf-8"))

    def test_atomic_writer_preserves_destination_and_cleans_temp_on_fsync_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "Package.swift"
            output.write_text("old", encoding="utf-8")
            with mock.patch(
                "scripts.release_metadata.os.fsync",
                side_effect=OSError("interrupted"),
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    release_metadata_module.atomic_write_text(output, "new")
            self.assertEqual(output.read_text(encoding="utf-8"), "old")
            self.assertEqual(
                [entry.name for entry in Path(directory).iterdir()],
                ["Package.swift"],
            )

    def test_atomic_writer_preserves_destination_and_cleans_temp_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "build-metadata.json"
            output.write_text("old", encoding="utf-8")
            with mock.patch(
                "scripts.release_metadata.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    release_metadata_module.atomic_write_text(output, "new")
            self.assertEqual(output.read_text(encoding="utf-8"), "old")
            self.assertEqual(
                [entry.name for entry in Path(directory).iterdir()],
                ["build-metadata.json"],
            )

    def test_atomic_writer_rejects_symlink_and_writes_exact_success_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            victim = parent / "victim"
            victim.write_text("preserve", encoding="utf-8")
            output = parent / "Package.swift"
            output.symlink_to(victim)
            with self.assertRaisesRegex(
                ReleaseMetadataError,
                "symlink",
            ):
                release_metadata_module.atomic_write_text(output, "attacker")
            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve")

            output.unlink()
            release_metadata_module.atomic_write_text(output, "café\n")
            self.assertEqual(output.read_bytes(), "café\n".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
