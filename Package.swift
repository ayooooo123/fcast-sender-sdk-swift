// swift-tools-version: 6.1

import PackageDescription

let url = "https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip"
let checksum = "40968f03ef7e95acb178ea458be796cdf7c8b0e9ca0ffc4c262487176f6e9ed6"

let package = Package(
    name: "FCastSenderSDK",
    platforms: [
        .iOS(.v16)
    ],
    products: [
        .library(name: "FCastSenderSDK", targets: ["FCastSenderSDK"])
    ],
    targets: [
        .binaryTarget(name: "fcast_sender_sdkFFI", url: url, checksum: checksum),
        .target(
            name: "FCastSenderSDK",
            dependencies: [.target(name: "fcast_sender_sdkFFI")]
        )
    ]
)
