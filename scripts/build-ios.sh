#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <repo-.build-distribution-output>" >&2
    exit 64
fi

for tool in git rustup cargo xcodebuild lipo swift sw_vers python3 shasum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "required build tool is unavailable: $tool" >&2
        exit 69
    fi
done
if [[ ! -x /usr/bin/zip ]]; then
    echo "required build tool is unavailable: /usr/bin/zip" >&2
    exit 69
fi

# source_pin.py is the sole validator for the immutable source values consumed
# through release_metadata.py's strict build-inputs interface.
test -f "$REPOSITORY_ROOT/scripts/source_pin.py"
BUILD_INPUTS="$(
    python3 "$REPOSITORY_ROOT/scripts/release_metadata.py" build-inputs \
        --source-lock "$REPOSITORY_ROOT/source.lock.json" \
        --environment-lock "$REPOSITORY_ROOT/build-environment.lock.json"
)"
IFS=$'\t' read -r \
    SOURCE_REPOSITORY SOURCE_TAG SOURCE_COMMIT RUST_TOOLCHAIN \
    RUST_TARGETS_CSV CARGO_PACKAGE EXPECTED_RUNNER EXPECTED_XCODE_VERSION \
    EXPECTED_XCODE_BUILD EXPECTED_SWIFT_VERSION EXPECTED_SWIFT_REVISION \
    EXPECTED_RUST_VERSION EXPECTED_ZIP_VERSION <<< "$BUILD_INPUTS"

if [[ "$RUST_TOOLCHAIN" != "$EXPECTED_RUST_VERSION" ]]; then
    echo "source and environment Rust versions disagree" >&2
    exit 65
fi

ACTUAL_MACOS_VERSION="$(sw_vers -productVersion)"
ACTUAL_RUNNER="macos-${ACTUAL_MACOS_VERSION%%.*}"
if [[ "$ACTUAL_RUNNER" != "$EXPECTED_RUNNER" ]]; then
    echo "runner mismatch: expected $EXPECTED_RUNNER, got $ACTUAL_RUNNER" >&2
    exit 65
fi

XCODE_OUTPUT="$(xcodebuild -version)"
if [[ "$XCODE_OUTPUT" != $'Xcode '"$EXPECTED_XCODE_VERSION"$'\nBuild version '"$EXPECTED_XCODE_BUILD" ]]; then
    echo "Xcode mismatch" >&2
    exit 65
fi

SWIFT_OUTPUT="$(swift --version)"
if [[ "$SWIFT_OUTPUT" != *"Apple Swift version $EXPECTED_SWIFT_VERSION ($EXPECTED_SWIFT_REVISION "* ]]; then
    echo "Swift mismatch" >&2
    exit 65
fi

RUST_OUTPUT="$(rustup run "$RUST_TOOLCHAIN" rustc --version)"
if [[ "$RUST_OUTPUT" != "rustc $EXPECTED_RUST_VERSION ("* ]]; then
    echo "Rust mismatch" >&2
    exit 65
fi

ZIP_OUTPUT="$(/usr/bin/zip -v)"
if [[ "$ZIP_OUTPUT" != *"This is Zip $EXPECTED_ZIP_VERSION "* ]]; then
    echo "Info-ZIP mismatch" >&2
    exit 65
fi

DISTRIBUTION_OUTPUT="$(
    python3 - "$REPOSITORY_ROOT" "$1" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
requested = Path(sys.argv[2])
if not requested.is_absolute():
    requested = root / requested
build_root = root / ".build"
if build_root.is_symlink():
    raise SystemExit("repo .build must not be a symlink")
resolved_build = build_root.resolve()
resolved_output = requested.resolve()
if resolved_output == resolved_build or resolved_build not in resolved_output.parents:
    raise SystemExit("distribution output must be a child of repo .build")
current = build_root
for component in resolved_output.relative_to(resolved_build).parts:
    current = current / component
    if current.is_symlink():
        raise SystemExit(f"distribution output path must not contain symlinks: {current}")
print(resolved_output)
PY
)"

BUILD_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/fcast-ios-distribution.XXXXXX")"
cleanup() {
    rm -rf "$BUILD_TEMP"
}
trap cleanup EXIT

SOURCE_CHECKOUT="$BUILD_TEMP/source"
GIT_TERMINAL_PROMPT=0 git clone --no-checkout "$SOURCE_REPOSITORY" "$SOURCE_CHECKOUT"
cd "$SOURCE_CHECKOUT"
git checkout --detach "$SOURCE_TAG"
if [[ "$(git rev-list -n 1 "$SOURCE_TAG")" != "$SOURCE_COMMIT" ]]; then
    echo "source tag does not resolve to the locked commit" >&2
    exit 65
fi
if [[ "$(git rev-parse HEAD)" != "$SOURCE_COMMIT" ]]; then
    echo "source HEAD does not match the locked commit" >&2
    exit 65
fi

SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
INITIAL_CARGO_LOCK_SHA="$(shasum -a 256 Cargo.lock)"

rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal
IFS=',' read -r RUST_TARGET_DEVICE RUST_TARGET_SIMULATOR <<< "$RUST_TARGETS_CSV"
rustup target add --toolchain "$RUST_TOOLCHAIN" \
    "$RUST_TARGET_DEVICE" "$RUST_TARGET_SIMULATOR"

cargo "+$RUST_TOOLCHAIN" test --manifest-path Cargo.toml -p "$CARGO_PACKAGE" --locked --verbose
cargo "+$RUST_TOOLCHAIN" utask generate-ios

for required in \
    target/aarch64-apple-ios-sim/release/libfcast_sender_sdk.a \
    target/aarch64-apple-ios/release/libfcast_sender_sdk.a \
    ios-bindings/uniffi/fcast_sender_sdk.swift \
    ios-bindings/uniffi/fcast_sender_sdkFFI.h \
    ios-bindings/uniffi/module.modulemap; do
    if [[ ! -f "$required" ]]; then
        echo "unchanged generator did not emit required file: $required" >&2
        exit 66
    fi
done

git diff --exit-code
if [[ "$(shasum -a 256 Cargo.lock)" != "$INITIAL_CARGO_LOCK_SHA" ]]; then
    echo "unchanged generator modified Cargo.lock" >&2
    exit 66
fi
if [[ -n "$(git status --short --untracked-files=no)" ]]; then
    echo "unchanged generator modified tracked source" >&2
    exit 66
fi

mkdir -p "$REPOSITORY_ROOT/.build"
if [[ -e "$DISTRIBUTION_OUTPUT" ]]; then
    rm -rf "$DISTRIBUTION_OUTPUT"
fi
mkdir -p "$DISTRIBUTION_OUTPUT"

xcodebuild -create-xcframework \
        -library target/aarch64-apple-ios-sim/release/libfcast_sender_sdk.a \
        -headers ios-bindings/uniffi \
        -library target/aarch64-apple-ios/release/libfcast_sender_sdk.a \
        -headers ios-bindings/uniffi \
        -output "$DISTRIBUTION_OUTPUT/fcast_sender_sdk.xcframework"

cp ios-bindings/uniffi/fcast_sender_sdk.swift \
    "$DISTRIBUTION_OUTPUT/FCastSenderSDK.swift"
python3 "$REPOSITORY_ROOT/scripts/release_metadata.py" render-build-metadata \
    --source-lock "$REPOSITORY_ROOT/source.lock.json" \
    --environment-lock "$REPOSITORY_ROOT/build-environment.lock.json" \
    --source-date-epoch "$SOURCE_DATE_EPOCH" \
    --output "$DISTRIBUTION_OUTPUT/build-metadata.json"

"$REPOSITORY_ROOT/scripts/verify-artifact.sh" "$DISTRIBUTION_OUTPUT"
