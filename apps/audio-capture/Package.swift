// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AudioCapture",
    platforms: [.macOS("15.0")],
    products: [
        .executable(name: "audio-capture", targets: ["AudioCapture"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser", from: "1.3.0")
    ],
    targets: [
        .executableTarget(
            name: "AudioCapture",
            dependencies: [
                .product(name: "ArgumentParser", package: "swift-argument-parser")
            ]
        )
    ]
)
