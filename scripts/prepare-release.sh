#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
USAGE="usage: $0 --version 0.0.8-mediastorm.1 --input-commit <40-char-commit> --dry-run"

if [[ $# -ne 5 ]] ||
    [[ "$1" != "--version" ]] ||
    [[ "$3" != "--input-commit" ]] ||
    [[ "$5" != "--dry-run" ]]; then
    echo "$USAGE" >&2
    exit 64
fi
RELEASE_VERSION="$2"
RELEASE_INPUT_COMMIT="$4"
if [[ "$RELEASE_VERSION" != "0.0.8-mediastorm.1" ]]; then
    echo "first release version must be exactly 0.0.8-mediastorm.1" >&2
    exit 64
fi
if [[ ! "$RELEASE_INPUT_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    echo "release input commit must be exactly 40 lowercase hexadecimal characters" >&2
    exit 64
fi

cd "$REPOSITORY_ROOT"
ACTUAL_HEAD="$(git rev-parse HEAD)"
if [[ "$ACTUAL_HEAD" != "$RELEASE_INPUT_COMMIT" ]]; then
    echo "release input commit must equal HEAD" >&2
    exit 65
fi
if [[ -n "$(git status --short --untracked-files=no)" ]]; then
    echo "release preparation requires a clean tracked tree" >&2
    exit 65
fi
if git show-ref --verify --quiet "refs/tags/$RELEASE_VERSION"; then
    echo "local release tag already exists: $RELEASE_VERSION" >&2
    exit 65
fi
git show -s --format='%H' "$RELEASE_INPUT_COMMIT" |
    grep -Fx "$RELEASE_INPUT_COMMIT" >/dev/null

for tool in bash cmp cp git grep mktemp python3 shasum swift unzip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "required release tool is unavailable: $tool" >&2
        exit 69
    fi
done

export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests -v
for shell_script in "$SCRIPT_DIR"/*.sh; do
    bash -n "$shell_script"
done

BUILD_ONE="$REPOSITORY_ROOT/.build/release-build-1"
BUILD_TWO="$REPOSITORY_ROOT/.build/release-build-2"
ARCHIVE_ONE="$REPOSITORY_ROOT/.build/release-build-1.zip"
ARCHIVE_TWO="$REPOSITORY_ROOT/.build/release-build-2.zip"
RELEASE_DIRECTORY="$REPOSITORY_ROOT/.build/release"
FINAL_ARCHIVE="$RELEASE_DIRECTORY/fcast_sender_sdk.xcframework.zip"
BASE_ASSET_PROVENANCE="$RELEASE_DIRECTORY/provenance.json"

rm -rf -- \
    "$BUILD_ONE" \
    "$BUILD_TWO" \
    "$ARCHIVE_ONE" \
    "$ARCHIVE_TWO" \
    "$RELEASE_DIRECTORY"
mkdir -p "$REPOSITORY_ROOT/.build" "$RELEASE_DIRECTORY"

./scripts/build-ios.sh --output .build/release-build-1
./scripts/build-ios.sh --output .build/release-build-2
./scripts/verify-artifact.sh "$BUILD_ONE"
./scripts/verify-artifact.sh "$BUILD_TWO"

cmp "$BUILD_ONE/FCastSenderSDK.swift" "$BUILD_TWO/FCastSenderSDK.swift"
SOURCE_DATE_EPOCH_ONE="$(
    python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sourceDateEpoch"])' \
        "$BUILD_ONE/build-metadata.json"
)"
SOURCE_DATE_EPOCH_TWO="$(
    python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sourceDateEpoch"])' \
        "$BUILD_TWO/build-metadata.json"
)"
if [[ ! "$SOURCE_DATE_EPOCH_ONE" =~ ^[0-9]+$ ]] ||
    [[ "$SOURCE_DATE_EPOCH_ONE" != "$SOURCE_DATE_EPOCH_TWO" ]]; then
    echo "clean builds disagree on the locked SOURCE_DATE_EPOCH" >&2
    exit 66
fi
SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH_ONE"

python3 scripts/archive.py \
    --source "$BUILD_ONE/fcast_sender_sdk.xcframework" \
    --output "$ARCHIVE_ONE" \
    --source-epoch "$SOURCE_DATE_EPOCH"
python3 scripts/archive.py \
    --source "$BUILD_TWO/fcast_sender_sdk.xcframework" \
    --output "$ARCHIVE_TWO" \
    --source-epoch "$SOURCE_DATE_EPOCH"
cmp "$ARCHIVE_ONE" "$ARCHIVE_TWO"

ARCHIVE_SHA_ONE="$(shasum -a 256 "$ARCHIVE_ONE" | awk '{print $1}')"
ARCHIVE_SHA_TWO="$(shasum -a 256 "$ARCHIVE_TWO" | awk '{print $1}')"
SWIFTPM_CHECKSUM_ONE="$(swift package compute-checksum "$ARCHIVE_ONE")"
SWIFTPM_CHECKSUM_TWO="$(swift package compute-checksum "$ARCHIVE_TWO")"
if [[ "$ARCHIVE_SHA_ONE" != "$ARCHIVE_SHA_TWO" ]] ||
    [[ "$ARCHIVE_SHA_ONE" != "$SWIFTPM_CHECKSUM_ONE" ]] ||
    [[ "$ARCHIVE_SHA_ONE" != "$SWIFTPM_CHECKSUM_TWO" ]] ||
    [[ ! "$ARCHIVE_SHA_ONE" =~ ^[0-9a-f]{64}$ ]]; then
    echo "normalized archives, Python/SHA, and SwiftPM checksums disagree" >&2
    exit 66
fi

cmp \
    "$BUILD_ONE/FCastSenderSDK.swift" \
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift"
cp \
    "$BUILD_ONE/FCastSenderSDK.swift" \
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift"
python3 scripts/release_metadata.py render-package \
    --version "$RELEASE_VERSION" \
    --checksum "$ARCHIVE_SHA_ONE" \
    --output "$REPOSITORY_ROOT/Package.swift"
python3 scripts/release_metadata.py render-provenance \
    --version "$RELEASE_VERSION" \
    --release-input-commit "$RELEASE_INPUT_COMMIT" \
    --checksum "$ARCHIVE_SHA_ONE" \
    --source-lock "$REPOSITORY_ROOT/source.lock.json" \
    --environment-lock "$REPOSITORY_ROOT/build-environment.lock.json" \
    --output "$REPOSITORY_ROOT/provenance.json"

cp "$ARCHIVE_ONE" "$FINAL_ARCHIVE"
cp "$REPOSITORY_ROOT/provenance.json" "$BASE_ASSET_PROVENANCE"
python3 scripts/release_metadata.py verify-provenance \
    --provenance "$BASE_ASSET_PROVENANCE" \
    --package "$REPOSITORY_ROOT/Package.swift" \
    --source-lock "$REPOSITORY_ROOT/source.lock.json" \
    --release-input-commit "$RELEASE_INPUT_COMMIT" \
    --archive "$FINAL_ARCHIVE"

FINAL_VERIFY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fcast-final-archive.XXXXXX")"
cleanup() {
    rm -rf -- "$FINAL_VERIFY_ROOT"
}
trap cleanup EXIT
mkdir -p "$FINAL_VERIFY_ROOT/artifact"
unzip -q "$FINAL_ARCHIVE" -d "$FINAL_VERIFY_ROOT/artifact"
cp \
    "$BUILD_ONE/FCastSenderSDK.swift" \
    "$BUILD_ONE/build-metadata.json" \
    "$FINAL_VERIFY_ROOT/artifact/"
./scripts/verify-artifact.sh "$FINAL_VERIFY_ROOT/artifact"
./scripts/verify-local-package.sh "$FINAL_VERIFY_ROOT/artifact"

printf '%s\n' "candidate file status:"
git status --short --untracked-files=all -- \
    Package.swift \
    provenance.json \
    Sources/FCastSenderSDK/FCastSenderSDK.swift
printf '%s\n' "archive checksum: $ARCHIVE_SHA_ONE"
printf '%s\n' "next commands:"
printf '%s\n' \
    "git add Package.swift provenance.json Sources/FCastSenderSDK/FCastSenderSDK.swift" \
    "git commit -m \"release: prepare 0.0.8-mediastorm.1\""
