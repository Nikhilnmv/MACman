// swift-tools-version: 6.0
import PackageDescription

// A separate package from `helpers/` because this produces a different kind of
// artifact. The helpers are bare executables the daemon shells out to; this is
// an application that must end up inside a bundle with an Info.plist, an icon
// and a code signature.
//
// SwiftPM builds the executable; `build.sh` assembles MACman.app around it.
// Deliberately no .xcodeproj — a build script is readable, diffable and
// scriptable, which an Xcode project is not.
let package = Package(
    name: "MACman",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "MACman", path: "Sources/MACman"),
    ]
)
