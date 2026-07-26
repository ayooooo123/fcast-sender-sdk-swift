import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "verify-local-package.sh"
EXPECTED_SWIFT_ARGUMENTS = [
    "build",
    "--triple",
    "arm64-apple-ios16.0-simulator",
    "--sdk",
    "__SDK__",
    "--product",
    "MediaStormAPIProbe",
    "--scratch-path",
    "__BUILD__",
]
EXPECTED_XCRUN_ARGUMENTS = [
    "--sdk",
    "iphonesimulator",
    "--show-sdk-path",
]


def validate_manifest(manifest):
    required_fragments = (
        'let package = Package(\n    name: "MediaStormAPIProbe",',
        '.library(\n'
        '            name: "MediaStormAPIProbe",\n'
        '            targets: ["MediaStormAPIProbe"]\n'
        "        )",
        '.binaryTarget(\n'
        '            name: "fcast_sender_sdkFFI",\n'
        '            path: "Artifacts/fcast_sender_sdk.xcframework"\n'
        "        )",
        '.target(\n'
        '            name: "FCastSenderSDK",\n'
        '            dependencies: ["fcast_sender_sdkFFI"],\n'
        '            path: "Sources/FCastSenderSDK"\n'
        "        )",
        '.target(\n'
        '            name: "MediaStormAPIProbe",\n'
        '            dependencies: ["FCastSenderSDK"],\n'
        '            path: "Sources/MediaStormAPIProbe",\n'
        '            exclude: ["main.swift"]\n'
        "        )",
    )
    for fragment in required_fragments:
        if manifest.count(fragment) != 1:
            raise ValueError("exact library target graph")
    if manifest.count(".library(") != 1:
        raise ValueError("library product count")
    if manifest.count(".binaryTarget(") != 1:
        raise ValueError("binary target count")
    if manifest.count(".target(") != 2:
        raise ValueError("source target count")
    if ".executableTarget(" in manifest:
        raise ValueError("host-only executable target")
    if ".package(" in manifest or "url:" in manifest:
        raise ValueError("remote dependency")


def validate_swift_arguments(arguments):
    if len(arguments) != len(EXPECTED_SWIFT_ARGUMENTS):
        raise ValueError("swift argument count")
    normalized = [
        "__SDK__" if index == 4 else "__BUILD__" if index == 8 else argument
        for index, argument in enumerate(arguments)
    ]
    if normalized != EXPECTED_SWIFT_ARGUMENTS:
        raise ValueError("exact swift cross-compile command")


class LocalPackageScriptTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), f"missing {SCRIPT}")

    def run_with_side_effect_sentinels(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            side_effect_log = fixture / "side-effects"
            for name in ("python3", "mktemp", "swift", "xcrun", "cp"):
                executable = fake_bin / name
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' {name!r} "
                    '>> "$MEDIASTORM_TEST_SIDE_EFFECT_LOG"\n'
                    "exit 97\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            result = subprocess.run(
                [str(SCRIPT), *arguments],
                cwd=REPOSITORY_ROOT,
                env={
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(fixture / "home"),
                    "TMPDIR": f"{fixture}/",
                    "MEDIASTORM_TEST_SIDE_EFFECT_LOG": str(side_effect_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            observed = (
                side_effect_log.read_text(encoding="utf-8").splitlines()
                if side_effect_log.exists()
                else []
            )
            return result, observed

    def assert_cli_rejected_before_side_effects(self, arguments):
        result, observed = self.run_with_side_effect_sentinels(arguments)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(
            result.stderr,
            f"usage: {SCRIPT} <artifact-output-directory>\n",
        )
        self.assertEqual(observed, [])

    def test_rejects_no_arguments_before_temp_or_work(self):
        self.assert_cli_rejected_before_side_effects([])

    def test_rejects_extra_arguments_before_temp_or_work(self):
        self.assert_cli_rejected_before_side_effects(
            [".build/distribution", "extra"]
        )

    def test_missing_verifier_metadata_fails_before_local_package_build(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact"
            output.mkdir()
            result = subprocess.run(
                [str(SCRIPT), str(output)],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build-metadata.json", result.stderr)

    def make_behavior_fixture(self, directory):
        fixture = Path(directory)
        repository = fixture / "repository"
        capture = fixture / "capture"
        fake_bin = fixture / "bin"
        output = fixture / "artifact"
        for path in (
            repository / "scripts",
            repository / "Sources" / "FCastSenderSDK",
            repository / "Probes" / "MediaStormAPIProbe",
            fake_bin,
            capture,
            output / "fcast_sender_sdk.xcframework",
        ):
            path.mkdir(parents=True, exist_ok=True)

        shutil.copy2(SCRIPT, repository / "scripts" / SCRIPT.name)
        for relative in (
            "Sources/FCastSenderSDK/Discovery.swift",
            "Sources/FCastSenderSDK/FCastSenderSDK.swift",
            "Sources/FCastSenderSDK/MediaStormCompatibility.swift",
            "Probes/MediaStormAPIProbe/UpstreamApiProbe.swift",
            "Probes/MediaStormAPIProbe/BonjourEndpointResolver.swift",
            "Probes/MediaStormAPIProbe/main.swift",
        ):
            source = REPOSITORY_ROOT / relative
            self.assertTrue(source.is_file(), f"missing {source}")
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        verifier = repository / "scripts" / "verify-artifact.sh"
        verifier.write_text(
            "#!/bin/sh\n"
            'test "$#" -eq 1\n'
            'test -f "$1/build-metadata.json"\n',
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        (output / "build-metadata.json").write_text("{}\n", encoding="utf-8")
        (output / "FCastSenderSDK.swift").write_bytes(
            (
                repository
                / "Sources"
                / "FCastSenderSDK"
                / "FCastSenderSDK.swift"
            ).read_bytes()
        )
        (output / "fcast_sender_sdk.xcframework" / "marker").write_text(
            "artifact\n",
            encoding="utf-8",
        )

        xcrun = fake_bin / "xcrun"
        fake_sdk = fixture / "iPhoneSimulator.sdk"
        fake_sdk.mkdir()
        xcrun.write_text(
            "#!/bin/sh\n"
            'set -eu\n'
            ': > "$MEDIASTORM_TEST_CAPTURE/xcrun-args"\n'
            'for argument in "$@"; do\n'
            '  printf "%s\\n" "$argument" '
            '>> "$MEDIASTORM_TEST_CAPTURE/xcrun-args"\n'
            "done\n"
            'printf "%s\\n" "$MEDIASTORM_TEST_FAKE_SDK"\n',
            encoding="utf-8",
        )
        xcrun.chmod(0o755)

        swift = fake_bin / "swift"
        swift.write_text(
            "#!/bin/sh\n"
            'set -eu\n'
            'cp Package.swift "$MEDIASTORM_TEST_CAPTURE/Package.swift"\n'
            'pwd > "$MEDIASTORM_TEST_CAPTURE/pwd"\n'
            ': > "$MEDIASTORM_TEST_CAPTURE/swift-args"\n'
            'for argument in "$@"; do\n'
            '  printf "%s\\n" "$argument" '
            '>> "$MEDIASTORM_TEST_CAPTURE/swift-args"\n'
            "done\n"
            'find . -type f -print | LC_ALL=C sort '
            '> "$MEDIASTORM_TEST_CAPTURE/files"\n',
            encoding="utf-8",
        )
        swift.chmod(0o755)
        return repository, capture, fake_bin, output

    def run_behavior_fixture(self, repository, capture, fake_bin, output):
        return subprocess.run(
            [
                str(repository / "scripts" / "verify-local-package.sh"),
                str(output),
            ],
            cwd=repository,
            env={
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "MEDIASTORM_TEST_CAPTURE": str(capture),
                "MEDIASTORM_TEST_FAKE_SDK": str(
                    capture.parent / "iPhoneSimulator.sdk"
                ),
            },
            text=True,
            capture_output=True,
            check=False,
        )

    def test_generates_exact_local_library_graph_and_copies_audited_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, capture, fake_bin, output = self.make_behavior_fixture(
                directory
            )
            result = self.run_behavior_fixture(
                repository, capture, fake_bin, output
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = (capture / "Package.swift").read_text(encoding="utf-8")
            try:
                validate_manifest(manifest)
            except ValueError as error:
                self.fail(str(error))
            files = (capture / "files").read_text(encoding="utf-8")
            for expected in (
                "./Artifacts/fcast_sender_sdk.xcframework/marker",
                "./Sources/FCastSenderSDK/Discovery.swift",
                "./Sources/FCastSenderSDK/FCastSenderSDK.swift",
                "./Sources/FCastSenderSDK/MediaStormCompatibility.swift",
                "./Sources/MediaStormAPIProbe/UpstreamApiProbe.swift",
                "./Sources/MediaStormAPIProbe/BonjourEndpointResolver.swift",
                "./Sources/MediaStormAPIProbe/main.swift",
            ):
                self.assertIn(expected + "\n", files)

    def test_runs_exact_cross_compile_and_cleans_disposable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, capture, fake_bin, output = self.make_behavior_fixture(
                directory
            )
            result = self.run_behavior_fixture(
                repository, capture, fake_bin, output
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (capture / "xcrun-args").read_text(
                    encoding="utf-8"
                ).splitlines(),
                EXPECTED_XCRUN_ARGUMENTS,
            )
            arguments = (capture / "swift-args").read_text(
                encoding="utf-8"
            ).splitlines()
            try:
                validate_swift_arguments(arguments)
            except ValueError as error:
                self.fail(str(error))
            disposable_package = Path(
                (capture / "pwd").read_text(encoding="utf-8").strip()
            )
            self.assertFalse(disposable_package.exists())

    def test_manifest_validator_rejects_wrong_graph_and_remote_inputs(self):
        valid = (
            'let package = Package(\n'
            '    name: "MediaStormAPIProbe",\n'
            '.library(\n'
            '            name: "MediaStormAPIProbe",\n'
            '            targets: ["MediaStormAPIProbe"]\n'
            "        ),\n"
            '.binaryTarget(\n'
            '            name: "fcast_sender_sdkFFI",\n'
            '            path: "Artifacts/fcast_sender_sdk.xcframework"\n'
            "        ),\n"
            '.target(\n'
            '            name: "FCastSenderSDK",\n'
            '            dependencies: ["fcast_sender_sdkFFI"],\n'
            '            path: "Sources/FCastSenderSDK"\n'
            "        ),\n"
            '.target(\n'
            '            name: "MediaStormAPIProbe",\n'
            '            dependencies: ["FCastSenderSDK"],\n'
            '            path: "Sources/MediaStormAPIProbe",\n'
            '            exclude: ["main.swift"]\n'
            "        )\n"
        )
        validate_manifest(valid)
        mutations = (
            valid.replace(
                'exclude: ["main.swift"]',
                'exclude: []',
            ),
            valid.replace(
                'dependencies: ["FCastSenderSDK"]',
                "dependencies: []",
            ),
            valid.replace(
                'path: "Artifacts/fcast_sender_sdk.xcframework"',
                'url: "https://example.invalid/a.zip", checksum: "bad"',
            ),
            valid + '.package(url: "https://example.invalid/repo", from: "1.0.0")',
            valid.replace(".target(", ".executableTarget(", 1),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_manifest(mutation)

    def test_swift_command_validator_rejects_omission_and_mutation(self):
        valid = [
            (
                "/fake/iPhoneSimulator.sdk"
                if argument == "__SDK__"
                else "/tmp/build"
                if argument == "__BUILD__"
                else argument
            )
            for argument in EXPECTED_SWIFT_ARGUMENTS
        ]
        validate_swift_arguments(valid)
        mutations = (
            valid[:-1],
            [
                "x86_64-apple-ios16.0-simulator"
                if argument == "arm64-apple-ios16.0-simulator"
                else argument
                for argument in valid
            ],
            [
                "WrongProbe" if argument == "MediaStormAPIProbe" else argument
                for argument in valid
            ],
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_swift_arguments(mutation)

    def test_script_is_strict_uses_verifier_and_has_no_network_or_xcodebuild(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn("SCRIPT_DIR=", text)
        self.assertIn("REPOSITORY_ROOT=", text)
        self.assertIn('"$SCRIPT_DIR/verify-artifact.sh" "$ARTIFACT_OUTPUT"', text)
        self.assertIn("mktemp -d", text)
        self.assertIn("trap cleanup EXIT", text)
        self.assertLess(
            text.index("trap cleanup EXIT"),
            text.index('case "$LOCAL_PACKAGE_ROOT/"'),
        )
        canonicalization = (
            'LOCAL_PACKAGE_ROOT="$(cd "$LOCAL_PACKAGE_ROOT" && pwd -P)"'
        )
        self.assertIn(canonicalization, text)
        self.assertLess(
            text.index(canonicalization),
            text.index('case "$LOCAL_PACKAGE_ROOT/"'),
        )
        self.assertIn(
            "xcrun --sdk iphonesimulator --show-sdk-path",
            text,
        )
        for forbidden in (
            "eval",
            "curl",
            "wget",
            "xcodebuild",
            "swift package",
            ".package(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
