// swift-tools-version: 6.1

import PackageDescription

let url = "https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip"
let checksum = "e2cf3a46abc37abc855c9dc7c0a55c622f203117345bd586558891113cdae472"

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
