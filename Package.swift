// swift-tools-version: 6.1
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let url = "https://gitlab.futo.org/videostreaming/fcast/-/jobs/91137/artifacts/raw/ios-bindings/fcast_sender_sdk.xcframework.zip"
let checksum = "e5a674c66554b71ce5893051a441575d91b9d2ddbaa5eb1401ba9f53b6acf759"

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
