import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_KEYS = {
    "schemaVersion",
    "repository",
    "tag",
    "commit",
    "rustToolchain",
    "rustTargets",
    "cargoPackage",
    "features",
}
EXPECTED_REPOSITORY = "https://gitlab.futo.org/videostreaming/fcast.git"
EXPECTED_TAG = "sender-sdk-v0.5.0"
EXPECTED_COMMIT = "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8"
EXPECTED_RUST_TOOLCHAIN = "1.96.1"
EXPECTED_RUST_TARGETS = (
    "aarch64-apple-ios",
    "aarch64-apple-ios-sim",
)
EXPECTED_CARGO_PACKAGE = "fcast-sender-sdk"
EXPECTED_FEATURES = ("_ios_defaults",)


class SourcePinError(ValueError):
    """Raised when a source lock does not match the approved immutable pin."""


@dataclass(frozen=True)
class SourcePin:
    schema_version: int
    repository: str
    tag: str
    commit: str
    rust_toolchain: str
    rust_targets: tuple[str, ...]
    cargo_package: str
    features: tuple[str, ...]

    @classmethod
    def load(cls, path):
        lock_path = Path(path)
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SourcePinError(
                f"{lock_path} must contain valid JSON: {error.msg}"
            ) from error
        except OSError as error:
            raise SourcePinError(f"unable to read source pin {lock_path}: {error}") from error

        if type(payload) is not dict:
            raise SourcePinError("source pin root must be a JSON object")

        missing_keys = EXPECTED_KEYS - payload.keys()
        if missing_keys:
            raise SourcePinError(
                "source pin is missing required keys: "
                + ", ".join(sorted(missing_keys))
            )

        unknown_keys = payload.keys() - EXPECTED_KEYS
        if unknown_keys:
            raise SourcePinError(
                "source pin contains unknown keys: "
                + ", ".join(sorted(unknown_keys))
            )

        _require_type(payload, "schemaVersion", int, "integer")
        for key in (
            "repository",
            "tag",
            "commit",
            "rustToolchain",
            "cargoPackage",
        ):
            _require_type(payload, key, str, "string")
        for key in ("rustTargets", "features"):
            _require_type(payload, key, list, "array")
            for index, item in enumerate(payload[key]):
                if type(item) is not str:
                    raise SourcePinError(
                        f"{key}[{index}] must be a JSON string"
                    )

        if payload["schemaVersion"] != 1:
            raise SourcePinError("schemaVersion must be exactly 1")

        _validate_repository(payload["repository"])
        _validate_tag(payload["tag"])
        _validate_commit(payload["commit"])

        if payload["rustToolchain"] != EXPECTED_RUST_TOOLCHAIN:
            raise SourcePinError(
                f"rustToolchain must be exactly {EXPECTED_RUST_TOOLCHAIN}"
            )

        rust_targets = tuple(payload["rustTargets"])
        _validate_exact_sequence(
            field="rustTargets",
            actual=rust_targets,
            expected=EXPECTED_RUST_TARGETS,
        )

        if payload["cargoPackage"] != EXPECTED_CARGO_PACKAGE:
            raise SourcePinError(
                f"cargoPackage must be exactly {EXPECTED_CARGO_PACKAGE}"
            )

        features = tuple(payload["features"])
        _validate_exact_sequence(
            field="features",
            actual=features,
            expected=EXPECTED_FEATURES,
        )

        return cls(
            schema_version=payload["schemaVersion"],
            repository=payload["repository"],
            tag=payload["tag"],
            commit=payload["commit"],
            rust_toolchain=payload["rustToolchain"],
            rust_targets=rust_targets,
            cargo_package=payload["cargoPackage"],
            features=features,
        )


def _require_type(payload, key, expected_type, type_name):
    if type(payload[key]) is not expected_type:
        raise SourcePinError(f"{key} must be a JSON {type_name}")


def _validate_repository(repository):
    parsed = urlsplit(repository)
    if parsed.scheme != "https":
        raise SourcePinError("repository must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise SourcePinError("repository must not contain credentials")
    if parsed.hostname != "gitlab.futo.org":
        raise SourcePinError("repository host must be exactly gitlab.futo.org")
    if parsed.query:
        raise SourcePinError("repository must not contain a query")
    if parsed.fragment:
        raise SourcePinError("repository must not contain a fragment")
    if parsed.path != "/videostreaming/fcast.git":
        raise SourcePinError(
            "repository path must be exactly /videostreaming/fcast.git"
        )
    if repository != EXPECTED_REPOSITORY:
        raise SourcePinError(f"repository must be exactly {EXPECTED_REPOSITORY}")


def _validate_tag(tag):
    if not tag:
        raise SourcePinError("tag must be non-empty")
    if "/" in tag:
        raise SourcePinError("tag must not contain a slash")
    if ".." in tag:
        raise SourcePinError("tag must not contain ..")
    if any(character.isspace() for character in tag):
        raise SourcePinError("tag must not contain whitespace")
    if tag != EXPECTED_TAG:
        raise SourcePinError(f"tag must be exactly {EXPECTED_TAG}")


def _validate_commit(commit):
    if len(commit) != 40:
        raise SourcePinError("commit must contain exactly 40 lowercase hex characters")
    if any(character.isupper() for character in commit):
        raise SourcePinError("commit must use lowercase hex characters")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SourcePinError("commit must contain only lowercase hex characters")
    if commit != EXPECTED_COMMIT:
        raise SourcePinError(f"commit must be exactly {EXPECTED_COMMIT}")


def _validate_exact_sequence(field, actual, expected):
    if len(set(actual)) != len(actual):
        raise SourcePinError(f"{field} must not contain duplicate values")

    actual_set = set(actual)
    expected_set = set(expected)
    additional = actual_set - expected_set
    if additional:
        raise SourcePinError(
            f"{field} must not contain additional values: "
            + ", ".join(sorted(additional))
        )

    missing = expected_set - actual_set
    if missing:
        raise SourcePinError(
            f"{field} must contain exactly the required values; missing: "
            + ", ".join(sorted(missing))
        )

    if actual != expected:
        raise SourcePinError(f"{field} must use the required order")
