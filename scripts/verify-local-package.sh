#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <artifact-output-directory>" >&2
    exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
ARTIFACT_OUTPUT="$1"

for tool in cp mktemp swift xcrun; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "required tool is unavailable: $tool" >&2
        exit 69
    fi
done

REQUIRED_FILES=(
    "$SCRIPT_DIR/verify-artifact.sh"
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/Discovery.swift"
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift"
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/MediaStormCompatibility.swift"
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/UpstreamApiProbe.swift"
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/BonjourEndpointResolver.swift"
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/main.swift"
)
for required_file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$required_file" ]]; then
        echo "required file is unavailable: $required_file" >&2
        exit 66
    fi
done

"$SCRIPT_DIR/verify-artifact.sh" "$ARTIFACT_OUTPUT"

LOCAL_PACKAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mediastorm-fcast-local-package.XXXXXX")"
cleanup() {
    rm -rf -- "$LOCAL_PACKAGE_ROOT"
}
trap cleanup EXIT

LOCAL_PACKAGE_ROOT="$(cd "$LOCAL_PACKAGE_ROOT" && pwd -P)"
case "$LOCAL_PACKAGE_ROOT/" in
    "$REPOSITORY_ROOT/"*)
        echo "temporary package must be outside the repository" >&2
        exit 73
        ;;
esac
LOCAL_PROBE_BUILD="$LOCAL_PACKAGE_ROOT/.build"

mkdir -p \
    "$LOCAL_PACKAGE_ROOT/Artifacts" \
    "$LOCAL_PACKAGE_ROOT/Sources/FCastSenderSDK" \
    "$LOCAL_PACKAGE_ROOT/Sources/MediaStormAPIProbe"

cp -R \
    "$ARTIFACT_OUTPUT/fcast_sender_sdk.xcframework" \
    "$LOCAL_PACKAGE_ROOT/Artifacts/fcast_sender_sdk.xcframework"
cp \
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/Discovery.swift" \
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift" \
    "$REPOSITORY_ROOT/Sources/FCastSenderSDK/MediaStormCompatibility.swift" \
    "$LOCAL_PACKAGE_ROOT/Sources/FCastSenderSDK/"
cp \
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/UpstreamApiProbe.swift" \
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/BonjourEndpointResolver.swift" \
    "$REPOSITORY_ROOT/Probes/MediaStormAPIProbe/main.swift" \
    "$LOCAL_PACKAGE_ROOT/Sources/MediaStormAPIProbe/"

cat > "$LOCAL_PACKAGE_ROOT/Package.swift" <<'SWIFT_PACKAGE'
// swift-tools-version: 6.1

import PackageDescription

let package = Package(
    name: "MediaStormAPIProbe",
    platforms: [
        .iOS(.v16)
    ],
    products: [
        .library(
            name: "MediaStormAPIProbe",
            targets: ["MediaStormAPIProbe"]
        )
    ],
    targets: [
        .binaryTarget(
            name: "fcast_sender_sdkFFI",
            path: "Artifacts/fcast_sender_sdk.xcframework"
        ),
        .target(
            name: "FCastSenderSDK",
            dependencies: ["fcast_sender_sdkFFI"],
            path: "Sources/FCastSenderSDK"
        ),
        .target(
            name: "MediaStormAPIProbe",
            dependencies: ["FCastSenderSDK"],
            path: "Sources/MediaStormAPIProbe",
            exclude: ["main.swift"]
        )
    ]
)
SWIFT_PACKAGE

LOCAL_PROBE_SIMULATOR_SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
if [[ "$LOCAL_PROBE_SIMULATOR_SDK" != /* || ! -d "$LOCAL_PROBE_SIMULATOR_SDK" ]]; then
    echo "invalid iPhone Simulator SDK path: $LOCAL_PROBE_SIMULATOR_SDK" >&2
    exit 69
fi

(
    cd "$LOCAL_PACKAGE_ROOT"
    swift build \
        --triple arm64-apple-ios16.0-simulator \
        --sdk "$LOCAL_PROBE_SIMULATOR_SDK" \
        --product MediaStormAPIProbe \
        --scratch-path "$LOCAL_PROBE_BUILD"
)
