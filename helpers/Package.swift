// swift-tools-version: 6.0
import PackageDescription

// Note: this machine builds against Command Line Tools, whose PackageDescription
// predates `swiftLanguageMode` / the `swiftLanguageVersions:` overload. Both are
// omitted deliberately — adding either breaks manifest linking.
let package = Package(
    name: "macman-helpers",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "macman-state", path: "Sources/macman-state"),
        .executableTarget(name: "macman-ax", path: "Sources/macman-ax"),
        .executableTarget(name: "macman-local", path: "Sources/macman-local"),
    ]
)
