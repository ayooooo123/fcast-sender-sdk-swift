// swift-tools-version: 6.1

import PackageDescription

let url = "https://github.com/ayooooo123/fcast-sender-sdk-swift/releases/download/0.0.8-mediastorm.1/fcast_sender_sdk.xcframework.zip"
let checksum = "1b55d676f8999aae0427bbe221a91d3992747aba903038bb4618bf390a75d01a"

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
