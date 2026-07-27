# FCastSenderSDK 0.0.8-mediastorm.1 publication evidence

## Result

The immutable GitHub release, its cryptographic release attestation, a clean
remote SwiftPM resolution, the complete MediaStorm iOS API compile probe, and
independently downloaded release bytes all passed verification.

Evidence was collected at `2026-07-27T03:52:31Z`. The release tag remains on
the release commit; this evidence commit does not move it.

## Publication

- Release:
  <https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/tag/0.0.8-mediastorm.1>
- Release state: immutable, published prerelease
- Published at: `2026-07-27T03:33:53Z`
- Distribution input commit:
  `97c20655f260b46bc7b9868658eadf752e89c3bb`
- Release tag commit:
  `fdb2f72cfa311280480d97c49e80805aca29c17d`
- Distribution CI:
  <https://github.com/ayooooo123/fcast-sender-sdk-swift/actions/runs/30233754729>
- Pinned FUTO source:
  `https://gitlab.futo.org/videostreaming/fcast.git`
  tag `sender-sdk-v0.5.0`,
  commit `ce3c44c44b4057d3ba050696d9d0baa3e72b46f8`
- XCFramework ZIP size: `38134098` bytes
- XCFramework ZIP SHA-256 and SwiftPM checksum:
  `f07a79b9d0b7b9a29127bf44b170bdaef699d600d0056b6013d8db8b69ed95cc`
- Published provenance SHA-256:
  `0a9dd91422c8d0ee3358a38742b4a6dd6f14f58e259f35b5b0e3ef7c71473b42`

The GitHub release API reported `immutable: true`, the exact target commit,
and the two asset digests above. `git ls-remote` independently resolved
`refs/tags/0.0.8-mediastorm.1` to the exact release tag commit.

## GitHub release attestation

GitHub CLI `2.89.0` cryptographically verified the release:

```text
gh release verify 0.0.8-mediastorm.1 \
  --repo ayooooo123/fcast-sender-sdk-swift
result: PASS
verified tag: 0.0.8-mediastorm.1
verified ZIP SHA-256:
f07a79b9d0b7b9a29127bf44b170bdaef699d600d0056b6013d8db8b69ed95cc
verified provenance SHA-256:
0a9dd91422c8d0ee3358a38742b4a6dd6f14f58e259f35b5b0e3ef7c71473b42

gh release verify-asset 0.0.8-mediastorm.1 \
  fcast_sender_sdk.xcframework.zip \
  --repo ayooooo123/fcast-sender-sdk-swift
result: PASS (Verification succeeded)
verified tag commit:
sha1:fdb2f72cfa311280480d97c49e80805aca29c17d
```

Default transport from the verification host stalled while reaching
GitHub's Azure-hosted bundle URL and TUF CDN. Repeating the exact commands
through a temporary loopback CONNECT tunnel that forced upstream IPv4 while
preserving end-to-end TLS completed successfully. The workaround changed
transport only; GitHub CLI still performed its normal TUF and attestation
verification.

As an independent cross-check, the exact GitHub CLI `2.89.0` release verifier
library and policy verified the API's inline release bundle against the
TUF-verified GitHub trusted root. It matched the exact release commit and both
published asset digests.

## Isolated remote SwiftPM consumer

The authoritative consumer used a new temporary package and empty `scratch`,
`cache`, `configuration`, `security`, `clones`, and `DerivedData` directories.
The dependency declaration was:

```swift
.package(
  url: "https://github.com/ayooooo123/fcast-sender-sdk-swift",
  exact: "0.0.8-mediastorm.1"
)
```

Because the repository and artifact are public, SwiftPM operations used
`--disable-keychain --disable-netrc`. This avoided a verification-host
Keychain credential lookup deadlock without supplying credentials or changing
the remote URLs.

`swift package resolve` downloaded the binary directly from:

```text
https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip
```

It completed without a GitLab CI URL, redirect fallback, or checksum warning.
`swift package show-dependencies` reported the exact release version.
The isolated `Package.resolved` check passed with:

```text
identity: fcast-sender-sdk-swift
version: 0.0.8-mediastorm.1
revision: fdb2f72cfa311280480d97c49e80805aca29c17d
```

Xcode independently resolved the same remote package graph and exact version
using the isolated clones and package cache.

## MediaStorm iOS API probe

All three files from `Probes/MediaStormAPIProbe/` were copied byte-for-byte
and audited with `cmp`. The compile package used the validated library-target
shape from the local API gate and excluded the harmless `main.swift` sentinel.
Keeping `main.swift` in a SwiftPM target causes host-executable inference and
does not produce an iOS-supported package scheme.

The verification host did not have an iOS 26.5 Simulator runtime matching its
bundled 26.5 SDK, so the compile used the existing no-runtime cross-build gate:

```text
swift build \
  --disable-keychain --disable-netrc \
  --disable-automatic-resolution \
  --package-path <isolated-probe> \
  --scratch-path <isolated-probe>/scratch \
  --cache-path <isolated-probe>/cache \
  --config-path <isolated-probe>/configuration \
  --security-path <isolated-probe>/security \
  --triple arm64-apple-ios16.0-simulator \
  --sdk /Applications/Xcode.app/Contents/Developer/Platforms/\
iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator26.5.sdk \
  --product RemoteMediaStormFCastProbe
```

Result: `PASS`. It compiled the released `FCastSenderSDK`,
`UpstreamApiProbe.swift`, and `BonjourEndpointResolver.swift` against the
arm64 iOS Simulator XCFramework slice. The only diagnostic was an existing
`String(cString:)` deprecation warning in the canonical resolver.

## Independent release-byte verification

Both assets were downloaded independently with `gh release download`.
`swift package compute-checksum` and `shasum -a 256` returned the exact ZIP
checksum above. It matched:

- tagged `Package.swift`;
- published GitHub asset metadata;
- provenance `archiveSHA256`;
- provenance `package.swiftPMChecksum`.

`PYTHONDONTWRITEBYTECODE=1 python3 scripts/release_metadata.py
verify-provenance` passed with the exact source lock, distribution input
commit, release tag commit, package manifest, downloaded provenance, and
downloaded archive.

## Tool versions

Release provenance records:

```text
Rust 1.96.1
Xcode 26.6 (17F113)
Swift 6.3.3 (swiftlang-6.3.3.1.3)
Info-ZIP 3.0
runner: macos-26
```

Verification host:

```text
GitHub CLI 2.89.0
Apple Swift 6.3.3 (swiftlang-6.3.3.1.3 clang-2100.1.1.101)
Xcode 26.6 (17F113)
Git 2.50.1 (Apple Git-155)
Python 3.11.9
jq 1.8.1
shasum 6.02
```
