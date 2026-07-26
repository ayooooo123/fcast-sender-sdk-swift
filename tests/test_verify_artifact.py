import copy
import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.verify_artifact import ArtifactVerificationError, verify_artifact


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = REPOSITORY_ROOT / "source.lock.json"
ENVIRONMENT_LOCK = REPOSITORY_ROOT / "build-environment.lock.json"
TRACKED_SWIFT = REPOSITORY_ROOT / "Sources/FCastSenderSDK/FCastSenderSDK.swift"


class ArtifactVerifierTests(unittest.TestCase):
    def make_fixture(self, directory):
        output = Path(directory) / "output"
        framework = output / "fcast_sender_sdk.xcframework"
        device = framework / "ios-arm64"
        simulator = framework / "ios-arm64-simulator"
        header = b"void fcast_sender_sdk_test(void);\n"
        modulemap = (
            b"module fcast_sender_sdkFFI {\n"
            b"    header \"fcast_sender_sdkFFI.h\"\n"
            b"    export *\n"
            b"    use \"Darwin\"\n"
            b"    use \"_Builtin_stdbool\"\n"
            b"    use \"_Builtin_stdint\"\n"
            b"}"
        )
        for slice_path in (device, simulator):
            headers = slice_path / "Headers"
            headers.mkdir(parents=True)
            (slice_path / "libfcast_sender_sdk.a").write_bytes(b"archive")
            (headers / "fcast_sender_sdkFFI.h").write_bytes(header)
            (headers / "module.modulemap").write_bytes(modulemap)
            (headers / "fcast_sender_sdk.swift").write_bytes(
                TRACKED_SWIFT.read_bytes()
            )

        plist = {
            "AvailableLibraries": [
                {
                    "BinaryPath": "libfcast_sender_sdk.a",
                    "LibraryIdentifier": "ios-arm64",
                    "LibraryPath": "libfcast_sender_sdk.a",
                    "HeadersPath": "Headers",
                    "SupportedArchitectures": ["arm64"],
                    "SupportedPlatform": "ios",
                },
                {
                    "BinaryPath": "libfcast_sender_sdk.a",
                    "LibraryIdentifier": "ios-arm64-simulator",
                    "LibraryPath": "libfcast_sender_sdk.a",
                    "HeadersPath": "Headers",
                    "SupportedArchitectures": ["arm64"],
                    "SupportedPlatform": "ios",
                    "SupportedPlatformVariant": "simulator",
                },
            ],
            "CFBundlePackageType": "XFWK",
            "XCFrameworkFormatVersion": "1.0",
        }
        with (framework / "Info.plist").open("wb") as stream:
            plistlib.dump(plist, stream, sort_keys=True)

        generated = output / "FCastSenderSDK.swift"
        generated.write_bytes(TRACKED_SWIFT.read_bytes())
        source = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
        environment = json.loads(
            ENVIRONMENT_LOCK.read_text(encoding="utf-8")
        )
        metadata = {
            "buildEnvironment": environment,
            "cargoPackage": source["cargoPackage"],
            "features": source["features"],
            "rustTargets": source["rustTargets"],
            "schemaVersion": 1,
            "source": {
                "commit": source["commit"],
                "repository": source["repository"],
                "tag": source["tag"],
            },
            "sourceDateEpoch": 1784900561,
            "sourcePatched": False,
        }
        (output / "build-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return output, framework, plist, metadata

    def verify(self, output):
        completed = subprocess.CompletedProcess(
            args=["lipo"],
            returncode=0,
            stdout="arm64\n",
            stderr="",
        )
        with mock.patch(
            "scripts.verify_artifact.subprocess.run",
            return_value=completed,
        ):
            return verify_artifact(
                output=output,
                source_lock=SOURCE_LOCK,
                environment_lock=ENVIRONMENT_LOCK,
                tracked_swift=TRACKED_SWIFT,
            )

    def rewrite_plist(self, framework, plist):
        with (framework / "Info.plist").open("wb") as stream:
            plistlib.dump(plist, stream, sort_keys=True)

    def test_accepts_the_exact_two_slice_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, _, _ = self.make_fixture(directory)
            self.verify(output)

    def test_rejects_missing_and_extra_platform_slices(self):
        for mutation in ("missing", "extra"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, plist, _ = self.make_fixture(directory)
                    if mutation == "missing":
                        plist["AvailableLibraries"].pop()
                    else:
                        extra = copy.deepcopy(plist["AvailableLibraries"][0])
                        extra["LibraryIdentifier"] = "tvos-arm64"
                        extra["SupportedPlatform"] = "tvos"
                        plist["AvailableLibraries"].append(extra)
                    self.rewrite_plist(framework, plist)
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "exactly two|AvailableLibraries",
                    ):
                        self.verify(output)

    def test_rejects_wrong_variant_and_architecture(self):
        cases = (
            ("variant", "SupportedPlatformVariant", "maccatalyst"),
            ("architecture", "SupportedArchitectures", ["x86_64"]),
        )
        for label, key, value in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, plist, _ = self.make_fixture(directory)
                    plist["AvailableLibraries"][1][key] = value
                    self.rewrite_plist(framework, plist)
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        label,
                    ):
                        self.verify(output)

    def test_rejects_an_extra_lipo_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, _, _ = self.make_fixture(directory)
            completed = subprocess.CompletedProcess(
                args=["lipo"],
                returncode=0,
                stdout="arm64 x86_64\n",
                stderr="",
            )
            with mock.patch(
                "scripts.verify_artifact.subprocess.run",
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    ArtifactVerificationError,
                    "lipo.*arm64",
                ):
                    verify_artifact(
                        output=output,
                        source_lock=SOURCE_LOCK,
                        environment_lock=ENVIRONMENT_LOCK,
                        tracked_swift=TRACKED_SWIFT,
                    )

    def test_rejects_differing_headers_and_module_maps(self):
        for relative in (
            "Headers/fcast_sender_sdkFFI.h",
            "Headers/module.modulemap",
        ):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, _, _ = self.make_fixture(directory)
                    (
                        framework / "ios-arm64-simulator" / relative
                    ).write_text("different\n", encoding="utf-8")
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "identical",
                    ):
                        self.verify(output)

    def test_rejects_wrong_module_export(self):
        with tempfile.TemporaryDirectory() as directory:
            output, framework, _, _ = self.make_fixture(directory)
            modulemap = (
                framework
                / "ios-arm64"
                / "Headers"
                / "module.modulemap"
            )
            modulemap.write_text(
                "module attacker { header \"fcast_sender_sdkFFI.h\" export * }\n",
                encoding="utf-8",
            )
            simulator_modulemap = (
                framework
                / "ios-arm64-simulator"
                / "Headers"
                / "module.modulemap"
            )
            simulator_modulemap.write_bytes(modulemap.read_bytes())
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "module.*fcast_sender_sdkFFI",
            ):
                self.verify(output)

    def test_rejects_every_noncanonical_module_map_mutation(self):
        canonical = (
            "module fcast_sender_sdkFFI {\n"
            "    header \"fcast_sender_sdkFFI.h\"\n"
            "    export *\n"
            "    use \"Darwin\"\n"
            "    use \"_Builtin_stdbool\"\n"
            "    use \"_Builtin_stdint\"\n"
            "}"
        )
        mutations = (
            canonical.replace("    export *\n", '    link "sqlite3"\n    export *\n'),
            canonical.replace("    export *\n", "    attacker directive\n    export *\n"),
            canonical + "\nmodule attacker { export * }",
            canonical.replace(
                '    use "Darwin"\n',
                "",
            ),
            canonical.replace(
                '    use "Darwin"\n    use "_Builtin_stdbool"\n',
                '    use "_Builtin_stdbool"\n    use "Darwin"\n',
            ),
            canonical.replace(
                '    use "_Builtin_stdint"\n',
                '    use "_Builtin_stdint"\n    use "_Builtin_stdint"\n',
            ),
            canonical + "\ntrailing garbage",
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, _, _ = self.make_fixture(directory)
                    for identifier in ("ios-arm64", "ios-arm64-simulator"):
                        (
                            framework
                            / identifier
                            / "Headers"
                            / "module.modulemap"
                        ).write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "canonical module map",
                    ):
                        self.verify(output)

    def test_rejects_extra_framework_slice_and_header_entries(self):
        mutations = (
            ("framework", "extra", False),
            ("slice", "ios-arm64/extra", False),
            ("headers", "ios-arm64/Headers/extra", False),
            ("framework symlink", "link", True),
        )
        for label, relative, symlink in mutations:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, _, _ = self.make_fixture(directory)
                    path = framework / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if symlink:
                        path.symlink_to("Info.plist")
                    else:
                        path.write_bytes(b"extra")
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "exact contents",
                    ):
                        self.verify(output)

    def test_rejects_wrong_or_extra_plist_root_fields(self):
        cases = (
            lambda value: value.update({"Extra": True}),
            lambda value: value.update({"CFBundlePackageType": "BAD"}),
            lambda value: value.update({"XCFrameworkFormatVersion": "2.0"}),
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, plist, _ = self.make_fixture(directory)
                    mutate(plist)
                    self.rewrite_plist(framework, plist)
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "plist.*exact",
                    ):
                        self.verify(output)

    def test_rejects_symlinked_plist_and_static_library(self):
        for relative in (
            "Info.plist",
            "ios-arm64/libfcast_sender_sdk.a",
        ):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, _, _ = self.make_fixture(directory)
                    path = framework / relative
                    real = framework / "real-file"
                    real.write_bytes(path.read_bytes())
                    path.unlink()
                    path.symlink_to(real)
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "symlink|exact contents",
                    ):
                        self.verify(output)

    def test_scans_plist_and_static_libraries_for_temporary_paths(self):
        mutations = (
            ("plist", "plist"),
            ("device library", "library"),
        )
        for label, kind in mutations:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    output, framework, plist, _ = self.make_fixture(directory)
                    if kind == "plist":
                        plist["Temporary"] = "/private/tmp/attacker"
                        self.rewrite_plist(framework, plist)
                    else:
                        (
                            framework
                            / "ios-arm64"
                            / "libfcast_sender_sdk.a"
                        ).write_bytes(b"archive /private/tmp/attacker")
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "absolute temporary path",
                    ):
                        self.verify(output)

    def test_rejects_stale_generated_swift(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, _, _ = self.make_fixture(directory)
            (output / "FCastSenderSDK.swift").write_text(
                "stale\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "generated Swift.*tracked",
            ):
                self.verify(output)

    def test_rejects_absolute_temporary_path_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            output, framework, _, metadata = self.make_fixture(directory)
            metadata["source"]["repository"] = "/private/tmp/source"
            (output / "build-metadata.json").write_text(
                json.dumps(metadata, sort_keys=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "absolute temporary path",
            ):
                self.verify(output)

            output, framework, _, metadata = self.make_fixture(
                Path(directory) / "second"
            )
            header = (
                framework
                / "ios-arm64"
                / "Headers"
                / "fcast_sender_sdkFFI.h"
            )
            header.write_text(
                header.read_text(encoding="utf-8")
                + "// /private/tmp/build/source\n",
                encoding="utf-8",
            )
            simulator_header = (
                framework
                / "ios-arm64-simulator"
                / "Headers"
                / "fcast_sender_sdkFFI.h"
            )
            simulator_header.write_bytes(header.read_bytes())
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "absolute temporary path",
            ):
                self.verify(output)

    def test_rejects_metadata_disagreement_with_each_lock(self):
        cases = (
            ("source", ("source", "commit"), "0" * 40),
            ("environment", ("buildEnvironment", "rustVersion"), "9.9.9"),
        )
        for label, path, value in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    output, _, _, metadata = self.make_fixture(directory)
                    metadata[path[0]][path[1]] = value
                    (output / "build-metadata.json").write_text(
                        json.dumps(metadata, sort_keys=True),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        "metadata.*lock",
                    ):
                        self.verify(output)

    def test_rejects_unknown_missing_duplicate_and_wrong_typed_metadata(self):
        cases = (
            ("unknown", lambda value: value.update({"extra": True})),
            ("missing", lambda value: value.pop("sourcePatched")),
            (
                "wrong type",
                lambda value: value.update({"sourcePatched": 0}),
            ),
        )
        for message, mutate in cases:
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as directory:
                    output, _, _, metadata = self.make_fixture(directory)
                    mutate(metadata)
                    (output / "build-metadata.json").write_text(
                        json.dumps(metadata, sort_keys=True),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ArtifactVerificationError,
                        message,
                    ):
                        self.verify(output)

        with tempfile.TemporaryDirectory() as directory:
            output, _, _, metadata = self.make_fixture(directory)
            raw = json.dumps(metadata).replace(
                '"schemaVersion": 1',
                '"schemaVersion": 1, "schemaVersion": 1',
            )
            (output / "build-metadata.json").write_text(
                raw,
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "duplicate.*schemaVersion",
            ):
                self.verify(output)

    def test_normalizes_malformed_metadata_plist_and_enumeration_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, _, _ = self.make_fixture(directory)
            (output / "build-metadata.json").write_bytes(b"\xff")
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "build metadata",
            ):
                self.verify(output)

        with tempfile.TemporaryDirectory() as directory:
            output, framework, _, _ = self.make_fixture(directory)
            with (framework / "Info.plist").open("wb") as stream:
                plistlib.dump([], stream)
            with self.assertRaisesRegex(
                ArtifactVerificationError,
                "plist.*dictionary",
            ):
                self.verify(output)

        with tempfile.TemporaryDirectory() as directory:
            output, _, _, _ = self.make_fixture(directory)
            with mock.patch.object(
                Path,
                "iterdir",
                side_effect=PermissionError("denied"),
            ):
                with self.assertRaisesRegex(
                    ArtifactVerificationError,
                    "enumerate",
                ):
                    self.verify(output)


if __name__ == "__main__":
    unittest.main()
