// swift-tools-version: 6.0
// Native macOS demo for the acoustic-vocabularies sound engine (see ../README.md).
import PackageDescription

let package = Package(
    name: "AVSoundDemo",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "AVSoundDemo", targets: ["AVSoundDemo"]),
        .library(name: "AVSoundDemoCore", targets: ["AVSoundDemoCore"]),
        .library(name: "AVSoundSpec", targets: ["AVSoundSpec"]),
    ],
    targets: [
        // Bridge client, protocol models, WAV/hash verification, audio playback.
        .target(name: "AVSoundDemoCore"),
        // Independent Swift port of the renderer spec (conformance cross-check).
        .target(name: "AVSoundSpec"),
        // SwiftUI app.
        .executableTarget(name: "AVSoundDemo", dependencies: ["AVSoundDemoCore", "AVSoundSpec"]),
        .testTarget(name: "AVSoundDemoCoreTests", dependencies: ["AVSoundDemoCore"]),
        .testTarget(name: "AVSoundSpecTests", dependencies: ["AVSoundSpec"]),
        // App models (AppModel and the sections) and the self-check exit status.
        .testTarget(name: "AVSoundDemoAppTests", dependencies: ["AVSoundDemo", "AVSoundDemoCore"]),
    ]
)
