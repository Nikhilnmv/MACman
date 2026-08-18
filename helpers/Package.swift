// swift-tools-version: 6.0
import PackageDescription

// Note: this machine builds against Command Line Tools, whose PackageDescription
// predates `swiftLanguageMode` / the `swiftLanguageVersions:` overload. Both are
// omitted deliberately — adding either breaks manifest linking.
/// Link an Info.plist into a command-line executable.
///
/// macOS TCC reads the usage description from a `__TEXT,__info_plist` section.
/// An app bundle carries one; a bare executable does not, and touching a
/// privacy-sensitive API without it gets the process **killed** — SIGABRT, no
/// prompt, no error. Found the hard way in macman-speech: it only misbehaves
/// when permission is `notDetermined`, so it is invisible on any Mac where the
/// permission was already granted, and fatal on every fresh install.
///
/// Paths are relative to the package root, which is where `swift build` runs.
func embedInfoPlist(_ target: String) -> [LinkerSetting] {
    [.unsafeFlags([
        "-Xlinker", "-sectcreate",
        "-Xlinker", "__TEXT",
        "-Xlinker", "__info_plist",
        "-Xlinker", "Sources/\(target)/Info.plist",
    ])]
}

let package = Package(
    name: "macman-helpers",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "macman-state", path: "Sources/macman-state"),
        .executableTarget(name: "macman-ax", path: "Sources/macman-ax"),
        .executableTarget(name: "macman-local", path: "Sources/macman-local"),
        // `exclude` because Info.plist is a *linker input*, not a resource.
        // SwiftPM otherwise warns about an unhandled file on every build, and
        // bundling it would embed a second, ignored copy.
        .executableTarget(name: "macman-speech", path: "Sources/macman-speech",
                          exclude: ["Info.plist"],
                          linkerSettings: embedInfoPlist("macman-speech")),
        .executableTarget(name: "macman-audio", path: "Sources/macman-audio",
                          exclude: ["Info.plist"],
                          linkerSettings: embedInfoPlist("macman-audio")),
    ]
)
