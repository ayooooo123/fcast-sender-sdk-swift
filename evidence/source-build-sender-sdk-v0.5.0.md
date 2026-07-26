# Unchanged FUTO iOS source-build evidence

## Result

FUTO's pinned source built both iOS arm64 slices and generated its Swift
bindings and XCFramework without any tracked source change.

The prescribed host-test feature combination (`_ios_defaults`) does not
compile the crate's test module because that feature intentionally omits
`discovery`, while `tests::async_runtime_spawn` unconditionally calls a method
that is compiled only when `discovery` is enabled. FUTO's sender CI uses
default-feature `cargo test --verbose`; the package-scoped, locked equivalent
passed unchanged with 32 unit tests and 1 doc test.

The unchanged generator also attached headers only to the `ios-arm64` library.
The simulator library is present and arm64, but its `AvailableLibraries` entry
has no `HeadersPath`. This must remain a fail-closed check in the later
artifact/API-probe work; no upstream source or generated output was patched
for this evidence task.

## Source and environment

- Build observation time (UTC): `2026-07-26T20:45:20Z`
- Temporary checkout:
  `/private/tmp/fcast-ios-source.aejS6U/source`
- Repository:
  `https://gitlab.futo.org/videostreaming/fcast.git`
- Tag: `sender-sdk-v0.5.0`
- Full commit:
  `ce3c44c44b4057d3ba050696d9d0baa3e72b46f8`
- Rust:
  `rustc 1.96.1 (31fca3adb 2026-06-26)`,
  commit `31fca3adb283cc9dfd56b49cdee9a96eb9c96ffd`,
  host `aarch64-apple-darwin`, LLVM `22.1.2`
- Cargo:
  `cargo 1.96.1 (356927216 2026-06-26)`,
  commit `356927216a2d746168cf76e5e88cc3f4b58e029d`
- Xcode: `Xcode 26.6`, build `17F113`
- Swift:
  `Apple Swift version 6.3.3
  (swiftlang-6.3.3.1.3 clang-2100.1.1.101)`
- Installed pinned targets:
  `aarch64-apple-ios`, `aarch64-apple-ios-sim`

Version evidence was collected with these exact commands:

```text
env RUSTUP_TOOLCHAIN=1.96.1 rustc --version --verbose
exit 0

env RUSTUP_TOOLCHAIN=1.96.1 cargo --version --verbose
exit 0

xcodebuild -version
exit 0

swift --version
exit 0
```

## Checkout commands

The first toolchain metadata request ended with a transient TLS handshake EOF
(exit 1). Repeating the exact command succeeded.

```text
rustup toolchain install 1.96.1 --profile minimal
first attempt: exit 1 (TLS handshake EOF fetching channel metadata)
second attempt: exit 0

rustup target add --toolchain 1.96.1 \
  aarch64-apple-ios aarch64-apple-ios-sim
exit 0

mktemp -d /private/tmp/fcast-ios-source.XXXXXX
exit 0
output: /private/tmp/fcast-ios-source.aejS6U

git clone --filter=blob:none --no-checkout \
  https://gitlab.futo.org/videostreaming/fcast.git source
exit 0

git -C source fetch --depth=1 origin tag sender-sdk-v0.5.0
exit 0

git -C source checkout --detach \
  ce3c44c44b4057d3ba050696d9d0baa3e72b46f8
exit 0

test "$(git -C source rev-parse HEAD)" = \
  "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8"
exit 0

test "$(git -C source rev-list -n 1 sender-sdk-v0.5.0)" = \
  "ce3c44c44b4057d3ba050696d9d0baa3e72b46f8"
exit 0

git -C source status --short
exit 0, no output
```

Because the clone was filtered, the first sandboxed checkout attempted to
materialize promised blobs and failed DNS resolution (exit 128). Running the
same detached checkout with network access produced the successful checkout
recorded above.

## Sender test evidence

The exact prescribed feature command was:

```text
env RUSTUP_TOOLCHAIN=1.96.1 \
  cargo test --manifest-path source/Cargo.toml \
  -p fcast-sender-sdk --locked \
  --no-default-features --features _ios_defaults
```

It had two transport-only attempts before reaching compilation:

- exit 101: Cargo/libgit2 SSL handshake `-9806` while fetching pinned
  `image-rs` revision `9ee78f3f`;
- exit 101 with `CARGO_NET_GIT_FETCH_WITH_CLI=true`: crates.io transfers
  exhausted Cargo's default low-speed retries while fetching `askama`.

The complete compile-reaching command with Cargo's Git CLI and
transport-tolerant HTTP settings was:

```text
env RUSTUP_TOOLCHAIN=1.96.1 \
  CARGO_NET_GIT_FETCH_WITH_CLI=true \
  CARGO_HTTP_TIMEOUT=600 \
  CARGO_HTTP_LOW_SPEED_LIMIT=1 \
  CARGO_NET_RETRY=5 \
  cargo test --manifest-path source/Cargo.toml \
  -p fcast-sender-sdk --locked \
  --no-default-features --features _ios_defaults
exit 101
```

It reached compilation, then failed with:

```text
error[E0599]: no method named `spawn` found for enum `AsyncRuntime`
  --> sdk/sender/fcast-sender-sdk/src/lib.rs:629:21
```

Inspection showed `_ios_defaults` expands through `_mobile_defaults` to
`fcast,chromecast,uniffi,logging,discovery_types`, but not `discovery`.
`AsyncRuntime::spawn` is guarded by
`#[cfg(all(feature = "discovery", any_protocol))]`, while the crate's
`#[cfg(test)] tests::async_runtime_spawn` test is not guarded by `discovery`.

FUTO's `sdk/sender/.gitlab-ci.yml` declares `cargo test --verbose` for the
sender test job. The relevant package-scoped locked target was therefore run
unchanged:

```text
env RUSTUP_TOOLCHAIN=1.96.1 \
  CARGO_NET_GIT_FETCH_WITH_CLI=true \
  CARGO_HTTP_TIMEOUT=600 \
  CARGO_HTTP_LOW_SPEED_LIMIT=1 \
  CARGO_NET_RETRY=5 \
  cargo test --manifest-path source/Cargo.toml \
  -p fcast-sender-sdk --locked --verbose
exit 0
```

Observed test totals:

```text
running 32 tests
test result: ok. 32 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

running 1 test
test sdk/sender/fcast-sender-sdk/src/lib.rs - (line 18) - compile ... ok
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

## iOS generator evidence

Run from the clean `source/` checkout so the repository-local `utask` alias
was active:

```text
env RUSTUP_TOOLCHAIN=1.96.1 cargo utask generate-ios
exit 0
```

The command built:

```text
cargo build -p fcast-sender-sdk --profile=release \
  --target=aarch64-apple-ios-sim \
  --no-default-features --features _ios_defaults
exit 0

cargo build -p fcast-sender-sdk --profile=release \
  --target=aarch64-apple-ios \
  --no-default-features --features _ios_defaults
exit 0

xcodebuild -create-xcframework \
  -library target/aarch64-apple-ios-sim/release/libfcast_sender_sdk.a \
  -library target/aarch64-apple-ios/release/libfcast_sender_sdk.a \
  -headers ios-bindings/uniffi \
  -output ios-bindings/fcast_sender_sdk.xcframework
exit 0
```

`xcodebuild` reported:

```text
xcframework successfully written out to:
/private/tmp/fcast-ios-source.aejS6U/source/ios-bindings/fcast_sender_sdk.xcframework
```

The only generator warning observed was two unused imports in
`xtask/src/sender.rs`.

## Generated binding checks

All required existence checks exited 0:

```text
test -d ios-bindings/fcast_sender_sdk.xcframework
test -f ios-bindings/uniffi/fcast_sender_sdk.swift
test -f ios-bindings/uniffi/fcast_sender_sdkFFI.h
test -f ios-bindings/uniffi/module.modulemap
```

Observed sizes and SHA-256 hashes:

```text
wc -c \
  ios-bindings/uniffi/fcast_sender_sdk.swift \
  ios-bindings/uniffi/fcast_sender_sdkFFI.h \
  ios-bindings/uniffi/module.modulemap
exit 0

shasum -a 256 \
  ios-bindings/uniffi/fcast_sender_sdk.swift \
  ios-bindings/uniffi/fcast_sender_sdkFFI.h \
  ios-bindings/uniffi/module.modulemap
exit 0
```

```text
237253 ios-bindings/uniffi/fcast_sender_sdk.swift
117572 ios-bindings/uniffi/fcast_sender_sdkFFI.h
   148 ios-bindings/uniffi/module.modulemap

11f1a0d302f38c405ab90435e2491b0e07e9f17d0c55fd1b78b7e1602d24159a
  ios-bindings/uniffi/fcast_sender_sdk.swift
79bfc8a6af630a545d70736c8f981ec8d6a9b0cae8508dd840dd3358f1b44433
  ios-bindings/uniffi/fcast_sender_sdkFFI.h
eed988bc35aa92717f3c17f81f1ca7f7ea1adcde554223fa047376d21218ed58
  ios-bindings/uniffi/module.modulemap
```

The generated module map declares `module fcast_sender_sdkFFI`, references
`fcast_sender_sdkFFI.h`, exports all symbols, and uses Darwin's builtin
boolean and integer modules.

## XCFramework inspection

Inspection commands all exited 0:

```text
find ios-bindings/fcast_sender_sdk.xcframework \
  -maxdepth 4 -type f -print | sort
plutil -p ios-bindings/fcast_sender_sdk.xcframework/Info.plist
file ios-bindings/fcast_sender_sdk.xcframework/*/libfcast_sender_sdk.a
lipo -info ios-bindings/fcast_sender_sdk.xcframework/*/libfcast_sender_sdk.a
```

Sorted files:

```text
ios-bindings/fcast_sender_sdk.xcframework/Info.plist
ios-bindings/fcast_sender_sdk.xcframework/ios-arm64-simulator/libfcast_sender_sdk.a
ios-bindings/fcast_sender_sdk.xcframework/ios-arm64/Headers/fcast_sender_sdk.swift
ios-bindings/fcast_sender_sdk.xcframework/ios-arm64/Headers/fcast_sender_sdkFFI.h
ios-bindings/fcast_sender_sdk.xcframework/ios-arm64/Headers/module.modulemap
ios-bindings/fcast_sender_sdk.xcframework/ios-arm64/libfcast_sender_sdk.a
```

`Info.plist` reports `XCFrameworkFormatVersion` `1.0`,
`CFBundlePackageType` `XFWK`, and these slices:

| Library identifier | Architecture | Platform | Variant | HeadersPath |
| --- | --- | --- | --- | --- |
| `ios-arm64` | `arm64` | `ios` | none | `Headers` |
| `ios-arm64-simulator` | `arm64` | `ios` | `simulator` | absent |

`file` output:

```text
ios-arm64-simulator/libfcast_sender_sdk.a: current ar archive
ios-arm64/libfcast_sender_sdk.a: current ar archive
```

`lipo -info` output:

```text
Non-fat file: ios-arm64-simulator/libfcast_sender_sdk.a is architecture: arm64
Non-fat file: ios-arm64/libfcast_sender_sdk.a is architecture: arm64
```

## Unchanged-source proof

After tests and generation:

```text
git status --short --untracked-files=all
exit 0, no output

git diff --exit-code
exit 0

git diff --exit-code -- Cargo.lock
exit 0

git status --short --ignored -- ios-bindings target
exit 0
!! ios-bindings/
!! target/
```

There were no tracked or untracked source changes. The generated
`ios-bindings/` and Cargo `target/` directories are ignored by the upstream
repository. No XCFramework or FUTO source file was copied into this
distribution repository.
