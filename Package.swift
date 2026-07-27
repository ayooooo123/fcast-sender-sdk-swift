// swift-tools-version: 6.1

import PackageDescription

let url = "https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip"
let checksum = "f07a79b9d0b7b9a29127bf44b170bdaef699d600d0056b6013d8db8b69ed95cc"

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
