// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "envd",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "EnvdCore"),
        .executableTarget(name: "envd", dependencies: ["EnvdCore"]),
        .testTarget(name: "EnvdCoreTests", dependencies: ["EnvdCore"]),
    ]
)
