// swift-tools-version: 6.1
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let url = "https://gitlab.futo.org/videostreaming/fcast/-/jobs/91111/artifacts/raw/ios-bindings/fcast_sender_sdk.xcframework.zip"
let checksum = "782316ae771fe82b72e373eb57431053e62e630fa2d53f6f997ed62b192e6e02"

let package = Package(
    name: "FCastSenderSDK",
    platforms: [
        .iOS(.v16)
    ],
    products: [
        // Products define the executables and libraries a package produces, making them visible to other packages.
        .library(
            name: "FCastSenderSDK",
            targets: ["FCastSenderSDK"]),
    ],
    targets: [
        .binaryTarget(name: "fcast_sender_sdkFFI", url: url, checksum: checksum),
        .target(
            name: "FCastSenderSDK",
            dependencies: [.target(name: "fcast_sender_sdkFFI")]
        )
    ]
)
