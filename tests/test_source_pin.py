import json
import tempfile
import tomllib
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from scripts.source_pin import SourcePin, SourcePinError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHECKED_IN_LOCK = REPOSITORY_ROOT / "source.lock.json"
CHECKED_IN_RUST_TOOLCHAIN = REPOSITORY_ROOT / "rust-toolchain.toml"


def valid_payload():
    return {
        "schemaVersion": 1,
        "repository": "https://gitlab.futo.org/videostreaming/fcast.git",
        "tag": "sender-sdk-v0.5.0",
        "commit": "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8",
        "rustToolchain": "1.96.1",
        "rustTargets": [
            "aarch64-apple-ios",
            "aarch64-apple-ios-sim",
        ],
        "cargoPackage": "fcast-sender-sdk",
        "features": ["_ios_defaults"],
    }


class SourcePinTests(unittest.TestCase):
    def write_json(self, directory, payload):
        path = Path(directory) / "source.lock.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def assert_invalid(self, payload, message_pattern):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_json(directory, payload)
            with self.assertRaisesRegex(SourcePinError, message_pattern):
                SourcePin.load(path)

    def assert_invalid_raw(self, raw_json, message_pattern):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.lock.json"
            path.write_text(raw_json, encoding="utf-8")
            try:
                SourcePin.load(path)
            except SourcePinError as error:
                self.assertRegex(str(error), message_pattern)
            except Exception as error:
                self.fail(
                    "expected SourcePinError, "
                    f"got {type(error).__name__}: {error}"
                )
            else:
                self.fail("expected SourcePinError to be raised")

    def test_loads_checked_in_lock_with_normalized_immutable_properties(self):
        source_pin = SourcePin.load(CHECKED_IN_LOCK)

        self.assertEqual(source_pin.schema_version, 1)
        self.assertEqual(
            source_pin.repository,
            "https://gitlab.futo.org/videostreaming/fcast.git",
        )
        self.assertEqual(source_pin.tag, "sender-sdk-v0.5.0")
        self.assertEqual(
            source_pin.commit,
            "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8",
        )
        self.assertEqual(source_pin.rust_toolchain, "1.96.1")
        self.assertEqual(
            source_pin.rust_targets,
            ("aarch64-apple-ios", "aarch64-apple-ios-sim"),
        )
        self.assertEqual(source_pin.cargo_package, "fcast-sender-sdk")
        self.assertEqual(source_pin.features, ("_ios_defaults",))
        with self.assertRaises(FrozenInstanceError):
            source_pin.tag = "different"

    def test_checked_in_rust_toolchain_exactly_agrees_with_source_lock(self):
        source_pin = SourcePin.load(CHECKED_IN_LOCK)
        with CHECKED_IN_RUST_TOOLCHAIN.open("rb") as stream:
            payload = tomllib.load(stream)

        self.assertEqual(set(payload), {"toolchain"})
        self.assertEqual(
            set(payload["toolchain"]),
            {"channel", "profile", "targets"},
        )
        self.assertEqual(payload["toolchain"]["channel"], source_pin.rust_toolchain)
        self.assertEqual(payload["toolchain"]["profile"], "minimal")
        self.assertEqual(
            payload["toolchain"]["targets"],
            list(source_pin.rust_targets),
        )

    def test_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.lock.json"
            path.write_text("{not-json", encoding="utf-8")

            with self.assertRaisesRegex(SourcePinError, "valid JSON"):
                SourcePin.load(path)

    def test_rejects_invalid_utf8_with_source_pin_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.lock.json"
            path.write_bytes(b"\xff")
            try:
                SourcePin.load(path)
            except SourcePinError as error:
                self.assertRegex(str(error), "UTF-8|source pin")
            except Exception as error:
                self.fail(
                    "expected SourcePinError, "
                    f"got {type(error).__name__}: {error}"
                )
            else:
                self.fail("expected SourcePinError to be raised")

    def test_rejects_duplicate_authority_key_in_raw_json(self):
        raw_json = json.dumps(valid_payload()).replace(
            '"repository": ',
            (
                '"repository": "https://attacker.invalid/fcast.git", '
                '"repository": '
            ),
            1,
        )
        self.assert_invalid_raw(
            raw_json,
            "duplicate JSON object key.*repository",
        )

    def test_rejects_non_object_root(self):
        self.assert_invalid([], "JSON object")

    def test_rejects_missing_key(self):
        payload = valid_payload()
        del payload["commit"]
        self.assert_invalid(payload, "missing.*commit")

    def test_rejects_unknown_key(self):
        payload = valid_payload()
        payload["patches"] = []
        self.assert_invalid(payload, "unknown.*patches")

    def test_rejects_wrong_scalar_type(self):
        payload = valid_payload()
        payload["schemaVersion"] = "1"
        self.assert_invalid(payload, "schemaVersion.*integer")

    def test_rejects_boolean_as_integer(self):
        payload = valid_payload()
        payload["schemaVersion"] = True
        self.assert_invalid(payload, "schemaVersion.*integer")

    def test_rejects_wrong_list_type(self):
        payload = valid_payload()
        payload["rustTargets"] = "aarch64-apple-ios"
        self.assert_invalid(payload, "rustTargets.*array")

    def test_rejects_wrong_type_for_each_string_field(self):
        for field in (
            "repository",
            "tag",
            "commit",
            "rustToolchain",
            "cargoPackage",
        ):
            with self.subTest(field=field):
                payload = valid_payload()
                payload[field] = 7
                self.assert_invalid(payload, f"{field}.*JSON string")

    def test_rejects_non_string_rust_target_member(self):
        payload = valid_payload()
        payload["rustTargets"][1] = 7
        self.assert_invalid(payload, r"rustTargets\[1\].*JSON string")

    def test_rejects_non_string_feature_member(self):
        payload = valid_payload()
        payload["features"][0] = 7
        self.assert_invalid(payload, r"features\[0\].*JSON string")

    def test_rejects_non_https_repository(self):
        payload = valid_payload()
        payload["repository"] = "http://gitlab.futo.org/videostreaming/fcast.git"
        self.assert_invalid(payload, "repository.*HTTPS")

    def test_rejects_repository_with_credentials(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://user:password@gitlab.futo.org/videostreaming/fcast.git"
        )
        self.assert_invalid(payload, "repository.*credentials")

    def test_rejects_wrong_repository_host(self):
        payload = valid_payload()
        payload["repository"] = "https://github.com/videostreaming/fcast.git"
        self.assert_invalid(payload, "repository.*host")

    def test_rejects_wrong_repository_path(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/not-fcast.git"
        )
        self.assert_invalid(payload, "repository.*path")

    def test_rejects_repository_query(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/fcast.git?archive=main"
        )
        self.assert_invalid(payload, "repository.*query")

    def test_rejects_repository_fragment(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/fcast.git#latest"
        )
        self.assert_invalid(payload, "repository.*fragment")

    def test_rejects_archive_style_repository_url(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/fcast/-/archive/main/fcast.zip"
        )
        self.assert_invalid(payload, "repository.*path")

    def test_rejects_latest_style_repository_url(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/fcast/-/releases/permalink/latest"
        )
        self.assert_invalid(payload, "repository.*path")

    def test_rejects_redirect_style_repository_url(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://gitlab.futo.org/videostreaming/fcast.git/"
        )
        self.assert_invalid(payload, "repository.*path")

    def test_rejects_malformed_repository_url_with_source_pin_error(self):
        payload = valid_payload()
        payload["repository"] = (
            "https://[invalid/videostreaming/fcast.git"
        )
        self.assert_invalid_raw(
            json.dumps(payload),
            "repository.*valid URL",
        )

    def test_rejects_empty_tag(self):
        payload = valid_payload()
        payload["tag"] = ""
        self.assert_invalid(payload, "tag.*non-empty")

    def test_rejects_tag_containing_slash(self):
        payload = valid_payload()
        payload["tag"] = "sender-sdk/v0.5.0"
        self.assert_invalid(payload, "tag.*slash")

    def test_rejects_tag_containing_dotdot(self):
        payload = valid_payload()
        payload["tag"] = "sender-sdk-..-v0.5.0"
        self.assert_invalid(payload, r"tag.*\.\.")

    def test_rejects_tag_containing_whitespace(self):
        payload = valid_payload()
        payload["tag"] = "sender-sdk-v0.5.0 "
        self.assert_invalid(payload, "tag.*whitespace")

    def test_rejects_syntactically_valid_but_wrong_tag(self):
        payload = valid_payload()
        payload["tag"] = "sender-sdk-v0.5.1"
        self.assert_invalid(payload, "tag.*exactly.*sender-sdk-v0\\.5\\.0")

    def test_rejects_short_commit(self):
        payload = valid_payload()
        payload["commit"] = "ce3c44c"
        self.assert_invalid(payload, "commit.*40")

    def test_rejects_uppercase_commit(self):
        payload = valid_payload()
        payload["commit"] = payload["commit"].upper()
        self.assert_invalid(payload, "commit.*lowercase")

    def test_rejects_non_hex_commit(self):
        payload = valid_payload()
        payload["commit"] = "g" * 40
        self.assert_invalid(payload, "commit.*hex")

    def test_rejects_valid_lowercase_hex_but_wrong_commit(self):
        payload = valid_payload()
        payload["commit"] = "0" * 40
        self.assert_invalid(
            payload,
            "commit.*exactly.*ce3c44c44b4057d3ba050696d9d0baa3e72b46f8",
        )

    def test_rejects_wrong_rust_toolchain(self):
        payload = valid_payload()
        payload["rustToolchain"] = "stable"
        self.assert_invalid(payload, "rustToolchain.*1\\.96\\.1")

    def test_rejects_missing_rust_target(self):
        payload = valid_payload()
        payload["rustTargets"] = ["aarch64-apple-ios"]
        self.assert_invalid(payload, "rustTargets.*exactly")

    def test_rejects_reversed_rust_targets(self):
        payload = valid_payload()
        payload["rustTargets"].reverse()
        self.assert_invalid(payload, "rustTargets.*order")

    def test_rejects_duplicated_rust_target(self):
        payload = valid_payload()
        payload["rustTargets"][1] = "aarch64-apple-ios"
        self.assert_invalid(payload, "rustTargets.*duplicate")

    def test_rejects_additional_rust_target(self):
        payload = valid_payload()
        payload["rustTargets"].append("x86_64-apple-ios")
        self.assert_invalid(payload, "rustTargets.*additional")

    def test_rejects_wrong_cargo_package(self):
        payload = valid_payload()
        payload["cargoPackage"] = "fcast"
        self.assert_invalid(payload, "cargoPackage.*fcast-sender-sdk")

    def test_rejects_missing_feature(self):
        payload = valid_payload()
        payload["features"] = []
        self.assert_invalid(payload, "features.*missing")

    def test_rejects_duplicated_feature(self):
        payload = valid_payload()
        payload["features"] = ["_ios_defaults", "_ios_defaults"]
        self.assert_invalid(payload, "features.*duplicate")

    def test_rejects_additional_feature(self):
        payload = valid_payload()
        payload["features"] = ["_ios_defaults", "extra"]
        self.assert_invalid(payload, "features.*additional")


if __name__ == "__main__":
    unittest.main()
