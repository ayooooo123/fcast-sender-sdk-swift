import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY_ROOT / "scripts" / "build-ios.sh"
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify-artifact.sh"


class BuildScriptTests(unittest.TestCase):
    def test_scripts_are_strict_and_resolve_the_repository_root(self):
        for path in (BUILD_SCRIPT, VERIFY_SCRIPT):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("set -euo pipefail", text)
                self.assertIn('SCRIPT_DIR=', text)
                self.assertIn('REPOSITORY_ROOT=', text)

    def test_build_consumes_strict_lock_helpers_without_duplicating_pins(self):
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("source_pin.py", text)
        self.assertIn("release_metadata.py", text)
        self.assertIn("source.lock.json", text)
        self.assertIn("build-environment.lock.json", text)
        for forbidden_literal in (
            "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8",
            "sender-sdk-v0.5.0",
            "https://gitlab.futo.org/videostreaming/fcast.git",
            "1.96.1",
        ):
            self.assertNotIn(forbidden_literal, text)

    def test_build_uses_clean_immutable_checkout_and_exact_toolchain(self):
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mktemp -d", text)
        self.assertIn("trap cleanup EXIT", text)
        self.assertIn('git clone --no-checkout "$SOURCE_REPOSITORY"', text)
        self.assertIn('git checkout --detach "$SOURCE_TAG"', text)
        self.assertIn('git rev-parse HEAD', text)
        self.assertIn('rustup toolchain install "$RUST_TOOLCHAIN"', text)
        self.assertIn('rustup target add --toolchain "$RUST_TOOLCHAIN"', text)
        self.assertIn('cargo "+$RUST_TOOLCHAIN"', text)
        self.assertNotIn("curl", text)
        self.assertNotIn("eval", text)
        self.assertNotIn("token", text.lower())
        self.assertNotIn("password", text.lower())

    def test_build_runs_valid_host_test_and_unchanged_generator(self):
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'cargo "+$RUST_TOOLCHAIN" test --manifest-path Cargo.toml '
            '-p "$CARGO_PACKAGE" --locked --verbose',
            text,
        )
        self.assertIn(
            'cargo "+$RUST_TOOLCHAIN" utask generate-ios',
            text,
        )
        self.assertNotRegex(
            text,
            r"cargo .*test[^\n]*--features[^\n]*_ios_defaults",
        )

    def test_build_repackages_both_libraries_with_headers_in_required_order(self):
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        expected = """xcodebuild -create-xcframework \\
        -library target/aarch64-apple-ios-sim/release/libfcast_sender_sdk.a \\
        -headers ios-bindings/uniffi \\
        -library target/aarch64-apple-ios/release/libfcast_sender_sdk.a \\
        -headers ios-bindings/uniffi \\
        -output "$DISTRIBUTION_OUTPUT/fcast_sender_sdk.xcframework\""""
        self.assertIn(expected, text)
        self.assertNotIn(
            "ios-bindings/fcast_sender_sdk.xcframework",
            text,
        )

    def test_build_emits_only_the_approved_distribution_outputs(self):
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("fcast_sender_sdk.xcframework", text)
        self.assertIn("FCastSenderSDK.swift", text)
        self.assertIn("build-metadata.json", text)
        self.assertIn("verify-artifact.sh", text)
        self.assertIn("git diff --exit-code", text)
        self.assertIn("Cargo.lock", text)

    def test_verifier_wrapper_is_strict_and_delegates_to_python(self):
        text = VERIFY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("verify_artifact.py", text)
        self.assertIn('"$REPOSITORY_ROOT/source.lock.json"', text)
        self.assertIn(
            '"$REPOSITORY_ROOT/build-environment.lock.json"',
            text,
        )
        self.assertIn(
            '"$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift"',
            text,
        )

    def test_build_output_is_the_only_new_ignore_rule(self):
        lines = (REPOSITORY_ROOT / ".gitignore").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(lines, ["/.build/"])

    def test_render_package_cli_writes_the_candidate_manifest(self):
        checksum = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "Package.swift"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "release_metadata.py"),
                    "render-package",
                    "--version",
                    "0.0.8-mediastorm.1",
                    "--checksum",
                    checksum,
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn(
                "https://github.com/ayooooo123/fcast-sender-sdk-swift/"
                "releases/download/0.0.8-mediastorm.1/"
                "fcast_sender_sdk.xcframework.zip",
                rendered,
            )
            self.assertIn(checksum, rendered)


if __name__ == "__main__":
    unittest.main()
