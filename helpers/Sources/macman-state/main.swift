// macman-state — reports session lock state and TCC permission grants as JSON.
//
// This is the gate MACman consults before every session: it decides which tool
// tier is available (see DESIGN.md §6.3) and which permissions are still missing.
//
//   macman-state              → full report
//   macman-state lock         → lock state only
//   macman-state permissions  → permission grants only

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

// MARK: - Report shape

/// Capability tier available right now. Mirrors the table in DESIGN.md §6.3.
enum Tier: String, Codable {
    /// Screen unlocked: every tool tier is available.
    case full
    /// Screen locked: shell, files, and most AppleScript work; no UI automation.
    case headless
    /// No usable session on the console at all.
    case unavailable
}

struct LockState: Codable {
    var onConsole: Bool
    var screenLocked: Bool
    var userName: String?
    var tier: Tier
}

struct Permissions: Codable {
    var accessibility: Bool
    var screenRecording: Bool
    /// Full Disk Access is not directly queryable; probed by reading chat.db.
    var fullDiskAccess: Bool
}

struct Report: Codable {
    var lock: LockState
    var permissions: Permissions
    var frontmostApp: String?
    var hostBinary: String
}

// MARK: - Lock state

/// Reads the current console session via CoreGraphics.
///
/// `CGSSessionScreenIsLocked` is absent (rather than false) when unlocked, so a
/// missing key is treated as unlocked.
func readLockState() -> LockState {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else {
        return LockState(onConsole: false, screenLocked: true, userName: nil, tier: .unavailable)
    }

    let onConsole = session["kCGSSessionOnConsoleKey"] as? Bool ?? false
    let locked = session["CGSSessionScreenIsLocked"] as? Bool ?? false
    let user = session["kCGSSessionUserNameKey"] as? String

    let tier: Tier
    if !onConsole {
        tier = .unavailable
    } else if locked {
        tier = .headless
    } else {
        tier = .full
    }

    return LockState(onConsole: onConsole, screenLocked: locked, userName: user, tier: tier)
}

// MARK: - Permissions

/// Probes Full Disk Access by attempting to read the Messages database.
///
/// There is no API for this; an actual read is the only reliable check. We open
/// and read a single byte rather than trusting `isReadableFile`, which reports
/// true for paths TCC will still block at open time.
func probeFullDiskAccess() -> Bool {
    let chatDB = FileManager.default
        .homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Messages/chat.db")

    guard let handle = try? FileHandle(forReadingFrom: chatDB) else { return false }
    defer { try? handle.close() }
    return (try? handle.read(upToCount: 1)) != nil
}

func readPermissions() -> Permissions {
    Permissions(
        accessibility: AXIsProcessTrusted(),
        screenRecording: CGPreflightScreenCaptureAccess(),
        fullDiskAccess: probeFullDiskAccess()
    )
}

// MARK: - Output

func emit<T: Encodable>(_ value: T) -> Never {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    guard let data = try? encoder.encode(value),
          let text = String(data: data, encoding: .utf8)
    else {
        FileHandle.standardError.write(Data("macman-state: failed to encode report\n".utf8))
        exit(1)
    }
    print(text)
    exit(0)
}

// MARK: - Entry point

switch CommandLine.arguments.dropFirst().first {
case "lock":
    emit(readLockState())

case "permissions":
    emit(readPermissions())

case nil, "report":
    // `hostBinary` matters because TCC grants attach to the *calling* process,
    // not to this helper — knowing who we are makes permission errors debuggable.
    emit(Report(
        lock: readLockState(),
        permissions: readPermissions(),
        frontmostApp: NSWorkspace.shared.frontmostApplication?.localizedName,
        hostBinary: ProcessInfo.processInfo.processName
    ))

case let other?:
    FileHandle.standardError.write(Data("""
        macman-state: unknown subcommand '\(other)'
        usage: macman-state [report|lock|permissions]

        """.utf8))
    exit(2)
}
