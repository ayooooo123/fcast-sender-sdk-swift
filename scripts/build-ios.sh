#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

if [[ $# -ne 2 ]]; then
    echo "usage: $0 --output <repo-.build-distribution-output>" >&2
    exit 64
fi
if [[ "$1" != "--output" || -z "$2" ]]; then
    echo "usage: $0 --output <repo-.build-distribution-output>" >&2
    exit 64
fi
REQUESTED_OUTPUT="$2"
shift 2

if [[ "${MEDIASTORM_SANITIZED_BUILD:-}" != "1" ]]; then
    python3 "$SCRIPT_DIR/build_boundary.py" check-environment
    exec python3 "$SCRIPT_DIR/build_boundary.py" re-exec \
        --script "$0" \
        --argument "$REQUESTED_OUTPUT"
fi
python3 "$SCRIPT_DIR/build_boundary.py" check-sanitized-environment
export PYTHONDONTWRITEBYTECODE=1

for tool in git rustup cargo xcodebuild xcode-select lipo swift sw_vers python3 shasum; do
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

DEVELOPER_ROOT="$(xcode-select -p)"
python3 "$SCRIPT_DIR/build_boundary.py" \
    check-developer-root --path "$DEVELOPER_ROOT"

BUILD_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/fcast-ios-distribution.XXXXXX")"
OUTPUT_PREPARED=0
cleanup() {
    if [[ "$OUTPUT_PREPARED" == "1" ]]; then
        output_helper discard || true
    fi
    rm -rf "$BUILD_TEMP"
}
trap cleanup EXIT

BUILD_TEMP="$(cd "$BUILD_TEMP" && pwd -P)"
CARGO_HOME="$BUILD_TEMP/cargo-home"
CARGO_ENCODED_RUSTFLAGS="--remap-path-prefix=$BUILD_TEMP=/fcast-build"
CFLAGS="-ffile-prefix-map=$BUILD_TEMP=/fcast-build -fdebug-prefix-map=$BUILD_TEMP=/fcast-build -ffile-prefix-map=$DEVELOPER_ROOT=/xcode -fdebug-prefix-map=$DEVELOPER_ROOT=/xcode"
export CARGO_HOME
export CARGO_ENCODED_RUSTFLAGS
export CFLAGS
mkdir -p "$CARGO_HOME"

OUTPUT_PREPARATION="$(
    python3 "$SCRIPT_DIR/output_directory.py" prepare \
        --root "$REPOSITORY_ROOT" \
        --requested "$REQUESTED_OUTPUT"
)"
IFS=$'\t' read -r \
    OUTPUT_FINAL_NAME OUTPUT_STAGING_NAME OUTPUT_BUILD_DEVICE \
    OUTPUT_BUILD_INODE OUTPUT_STAGING_DEVICE OUTPUT_STAGING_INODE \
    OUTPUT_FINAL_DEVICE OUTPUT_FINAL_INODE \
    <<< "$OUTPUT_PREPARATION"
DISTRIBUTION_OUTPUT="$REPOSITORY_ROOT/.build/$OUTPUT_STAGING_NAME"

output_helper() {
    python3 "$SCRIPT_DIR/output_directory.py" "$1" \
        --root "$REPOSITORY_ROOT" \
        --final-name "$OUTPUT_FINAL_NAME" \
        --staging-name "$OUTPUT_STAGING_NAME" \
        --build-device "$OUTPUT_BUILD_DEVICE" \
        --build-inode "$OUTPUT_BUILD_INODE" \
        --staging-device "$OUTPUT_STAGING_DEVICE" \
        --staging-inode "$OUTPUT_STAGING_INODE" \
        --final-device "$OUTPUT_FINAL_DEVICE" \
        --final-inode "$OUTPUT_FINAL_INODE"
}
OUTPUT_PREPARED=1

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

xcodebuild -create-xcframework \
        -library target/aarch64-apple-ios-sim/release/libfcast_sender_sdk.a \
        -headers ios-bindings/uniffi \
        -library target/aarch64-apple-ios/release/libfcast_sender_sdk.a \
        -headers ios-bindings/uniffi \
        -output "$DISTRIBUTION_OUTPUT/fcast_sender_sdk.xcframework"
python3 "$REPOSITORY_ROOT/scripts/release_metadata.py" \
    normalize-xcframework-plist \
    --plist "$DISTRIBUTION_OUTPUT/fcast_sender_sdk.xcframework/Info.plist"

cp ios-bindings/uniffi/fcast_sender_sdk.swift \
    "$DISTRIBUTION_OUTPUT/FCastSenderSDK.swift"
python3 "$REPOSITORY_ROOT/scripts/release_metadata.py" render-build-metadata \
    --source-lock "$REPOSITORY_ROOT/source.lock.json" \
    --environment-lock "$REPOSITORY_ROOT/build-environment.lock.json" \
    --source-date-epoch "$SOURCE_DATE_EPOCH" \
    --output "$DISTRIBUTION_OUTPUT/build-metadata.json"

"$REPOSITORY_ROOT/scripts/verify-artifact.sh" "$DISTRIBUTION_OUTPUT"
output_helper publish
