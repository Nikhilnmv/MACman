// Owns the MACman daemon: starts it, talks to it, and reports what it says.
//
// The daemon runs as a **child of this app**, which is the entire reason the
// app exists. macOS attributes a permission to the responsible process — the
// app that launched the one asking. Started from Terminal, MACman's Full Disk
// Access belongs to Terminal, and granting it there hands the same access to
// every script the user ever runs in a shell. Started from here, it belongs to
// MACman alone and can be revoked on its own.
//
// A LaunchAgent would undo that: the responsible process becomes `launchd`,
// the permission attaches to a bare binary with no bundle, and we are back to
// where we started. So the daemon is a child, and it dies with the app.

import Foundation
import SwiftUI

/// One decoded status line from the daemon.
struct DaemonStatus: Decodable, Equatable {
    var running: Bool = false
    var engine: String = "starting…"
    var tools: Bool = false
    var fullDiskAccess: Bool = false
    var tasksToday: Int = 0
    var sentOut: Int = 0
    var detail: String = ""
    var error: String?
    /// True only when the iMessage poller is actually running. Separate from
    /// the app being alive, because for a while those were conflated and the
    /// menu bar said "Running" while nothing was listening.
    var listening: Bool = false
    var listenDetail: String = ""
}

@MainActor
final class DaemonController: ObservableObject {

    enum State: Equatable {
        case stopped
        case starting
        case running
        case failed(String)
    }

    struct Note: Equatable {
        let text: String
        let ok: Bool
    }

    @Published private(set) var state: State = .stopped
    @Published private(set) var status = DaemonStatus()
    @Published private(set) var settings = SettingsSnapshot()
    @Published private(set) var activity = ActivitySnapshot()
    @Published private(set) var setupStatus = SetupStatus()
    /// The TOTP provisioning URI, held only while the code step is on screen.
    /// This is the one secret that travels outward, so it is cleared as soon
    /// as a code verifies rather than lingering in memory for the session.
    @Published private(set) var provisionURI: String?
    @Published private(set) var provisionNote: String?
    @Published private(set) var codeVerified: Bool?
    @Published private(set) var selfTest: SelfTestResult?
    @Published private(set) var selfTestRunning = false
    /// Result of the last settings change, shown briefly in the window. A
    /// rejected value must say why — silently reverting a field the user typed
    /// is the most confusing thing a settings pane can do.
    @Published private(set) var settingsNote: Note?
    /// Last few lines the daemon wrote to stderr. Kept because a daemon that
    /// dies silently is the worst failure this app can have — the menu bar
    /// would still look fine.
    @Published private(set) var lastError: String = ""

    private var process: Process?
    private var stdinPipe: Pipe?
    /// Partial line carried between reads: a pipe does not respect message
    /// boundaries, so JSON arrives split at arbitrary points.
    private var buffer = Data()

    // MARK: - Lifecycle

    func start() {
        guard process == nil else { return }
        state = .starting

        guard let python = Self.pythonExecutable() else {
            state = .failed("Could not find the MACman runtime.")
            return
        }

        let task = Process()
        task.executableURL = python.interpreter
        task.arguments = ["-m", "macman.main", "bridge"]
        task.currentDirectoryURL = python.workingDirectory

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = python.workingDirectory.path
        // Unbuffered, or status lines sit in a pipe buffer and the menu bar
        // shows stale state for as long as it takes to fill 4 KB.
        environment["PYTHONUNBUFFERED"] = "1"
        // The daemon infers the helpers' location from its own file path,
        // which is the repository layout and is wrong inside a bundle.
        if let helpers = python.helpers {
            environment["MACMAN_HELPERS_BIN"] = helpers.path
        }
        task.environment = environment

        let outPipe = Pipe()
        let errPipe = Pipe()
        let inPipe = Pipe()
        task.standardOutput = outPipe
        task.standardError = errPipe
        task.standardInput = inPipe
        stdinPipe = inPipe

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            Task { @MainActor in self?.absorb(chunk) }
        }

        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty,
                  let text = String(data: chunk, encoding: .utf8) else { return }
            Task { @MainActor in self?.recordError(text) }
        }

        task.terminationHandler = { [weak self] finished in
            Task { @MainActor in self?.daemonExited(code: finished.terminationStatus) }
        }

        do {
            try task.run()
            process = task
        } catch {
            state = .failed("Could not start MACman: \(error.localizedDescription)")
        }
    }

    /// Ask the daemon to stop, then make sure it did.
    ///
    /// A clean shutdown first, because the daemon may be mid-task. The kill is
    /// a backstop: an orphaned daemon would keep running under permissions the
    /// user can no longer see or revoke from the menu bar.
    func stop() {
        send(["type": "shutdown"])
        let task = process
        process = nil
        stdinPipe = nil

        DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
            if task?.isRunning == true { task?.terminate() }
        }
        state = .stopped
        status = DaemonStatus()
    }

    func restart() {
        stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.start()
        }
    }

    func refresh() { send(["type": "ping"]) }

    func startListening() { send(["type": "start_listening"]) }
    func stopListening() { send(["type": "stop_listening"]) }

    // MARK: - Settings

    func loadSettings() { send(["type": "settings"]) }

    func loadActivity() { send(["type": "activity", "limit": 200]) }

    // MARK: - Setup

    func loadSetupStatus() { send(["type": "setup_status"]) }

    func provisionCode(force: Bool) {
        provisionNote = nil
        send(["type": "provision_code", "force": force])
    }

    func verifyCode(_ code: String) {
        send(["type": "verify_code", "code": code])
    }

    func runSelfTest() {
        selfTestRunning = true
        selfTest = nil
        send(["type": "self_test"])
    }

    func openPermission(_ key: String) {
        send(["type": "open_permission", "key": key])
    }

    func setField(_ field: String, _ value: Any) {
        send(["type": "settings_set", "field": field, "value": value])
    }

    // Both send an *intent*, never a computed list.
    //
    // These used to send `settings.allowed_handles + [new]`, working out the
    // result here and posting it. That loses data whenever this copy of the
    // list is stale or has not arrived yet — the snapshot starts empty and
    // fills in asynchronously, so adding a handle in a freshly opened window
    // could write a list containing only that handle and silently discard the
    // rest. It emptied a real allowlist during testing.
    //
    // The daemon owns the file; it should be the only thing deciding what the
    // list becomes.
    func addHandle(_ handle: String) {
        let trimmed = handle.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        send(["type": "add_handle", "handle": trimmed])
    }

    func removeHandle(_ handle: String) {
        send(["type": "remove_handle", "handle": handle])
    }

    /// The key crosses the pipe to the daemon, which puts it in the Keychain.
    /// It is never written to the config file, never echoed back, and never
    /// recorded in the audit log.
    func setCloudKey(_ key: String) {
        send(["type": "set_cloud_key", "key": key])
    }

    func clearCloudKey() { send(["type": "clear_cloud_key"]) }

    func revokeCredentials() { send(["type": "revoke_credentials"]) }

    func removePreApproval(_ index: Int) {
        send(["type": "remove_pre_approval", "index": index])
    }

    /// Show the consent dialog on a fabricated disclosure.
    ///
    /// Exposed in the menu rather than hidden behind a debug flag: someone
    /// deciding whether to trust MACman with a cloud key should be able to see
    /// exactly what it will ask them, before anything real depends on it. It
    /// also exercises the full round trip, so a broken dialog is found here
    /// rather than the first time a task needs approval.
    func testConsent() { send(["type": "consent_selftest"]) }

    /// Tell the daemon its settings changed on disk.
    func reload() { send(["type": "reload"]) }

    // MARK: - Protocol

    /// Takes `Any`, not `String`, so a boolean crosses the pipe as a JSON
    /// boolean. Sending `"false"` as a string would arrive in Python as the
    /// *string* "false", and `bool("false")` is `True` — a refusal read as
    /// approval, in the one place that must never fail open.
    private func send(_ message: [String: Any]) {
        guard let stdinPipe,
              let data = try? JSONSerialization.data(withJSONObject: message)
        else { return }
        var line = data
        line.append(0x0A)
        // The daemon may already be gone; writing to a closed pipe raises
        // SIGPIPE, which would take the whole app down with it.
        try? stdinPipe.fileHandleForWriting.write(contentsOf: line)
    }

    private func absorb(_ chunk: Data) {
        buffer.append(chunk)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            buffer = buffer[buffer.index(after: newline)...]
            handle(line: Data(line))
        }
    }

    private func handle(line: Data) {
        guard let object = try? JSONSerialization.jsonObject(with: line)
                as? [String: Any],
              let kind = object["type"] as? String else { return }

        switch kind {
        case "ready":
            state = .running

        case "status":
            if let decoded = try? JSONDecoder().decode(DaemonStatus.self, from: line) {
                status = decoded
                state = decoded.running ? .running : .failed(decoded.error ?? "stopped")
            }

        case "settings":
            if let decoded = try? JSONDecoder().decode(SettingsSnapshot.self,
                                                       from: line) {
                settings = decoded
            }

        case "activity":
            if let decoded = try? JSONDecoder().decode(ActivitySnapshot.self,
                                                       from: line) {
                activity = decoded
            }

        case "setup_status":
            if let decoded = try? JSONDecoder().decode(SetupStatus.self, from: line) {
                setupStatus = decoded
            }

        case "provision_result":
            if object["ok"] as? Bool == true {
                provisionURI = object["uri"] as? String
                codeVerified = nil
            } else {
                provisionNote = object["detail"] as? String
            }

        case "verify_result":
            let ok = object["ok"] as? Bool ?? false
            codeVerified = ok
            if ok {
                // Verified, so the secret has served its purpose. Drop it
                // rather than keeping it addressable for the whole session.
                provisionURI = nil
                loadSetupStatus()
            }

        case "self_test_result":
            selfTestRunning = false
            if let decoded = try? JSONDecoder().decode(SelfTestResult.self,
                                                       from: line) {
                selfTest = decoded
            }
            loadSetupStatus()

        case "settings_result":
            let ok = object["ok"] as? Bool ?? false
            let detail = object["detail"] as? String ?? ""
            settingsNote = Note(text: detail, ok: ok)
            // Successes fade; failures stay until the next attempt, because a
            // rejected value is something the user still has to fix.
            if ok {
                Task { @MainActor in
                    try? await Task.sleep(for: .seconds(4))
                    if self.settingsNote?.text == detail { self.settingsNote = nil }
                }
            }

        case "consent":
            guard let id = object["id"] as? String else { break }
            let reason = object["reason"] as? String ?? "wants to send data"
            let body = object["body"] as? String ?? ""
            // The daemon's asking thread is blocked on this answer, and the
            // dialog is modal — so reply on the next runloop turn rather than
            // from inside the line handler, which is itself draining the pipe.
            Task { @MainActor in
                let approved = ConsentDialog.ask(reason: reason, body: body)
                self.send(["type": "consent_result", "id": id, "ok": approved])
            }

        default:
            break
        }
    }

    private func recordError(_ text: String) {
        lastError = String((lastError + text).suffix(2000))
    }

    private func daemonExited(code: Int32) {
        process = nil
        stdinPipe = nil
        guard state != .stopped else { return }
        // Exiting on its own is always a fault: a healthy daemon only stops
        // when asked, and the menu bar must say so rather than staying green.
        state = .failed("MACman stopped unexpectedly (exit \(code)).")
    }

    // MARK: - Locating the runtime

    struct Runtime {
        let interpreter: URL
        let workingDirectory: URL
        /// Where the Swift helpers live. `nil` lets the daemon fall back to
        /// its own repository-relative default, which is right in development.
        let helpers: URL?
    }

    /// Find the Python that runs the daemon.
    ///
    /// Two cases on purpose. A shipped bundle carries its own interpreter,
    /// because macOS ships Python 3.9 and the daemon needs 3.11 for `tomllib`
    /// — and depending on a Homebrew Python means the app breaks the day the
    /// user upgrades it. During development it falls back to the repository's
    /// virtualenv, so the app can be run without assembling a bundle first.
    static func pythonExecutable() -> Runtime? {
        let resources = Bundle.main.resourceURL

        if let resources {
            let embedded = resources.appendingPathComponent("python/bin/python3")
            let payload = resources.appendingPathComponent("daemon")
            if FileManager.default.isExecutableFile(atPath: embedded.path) {
                return Runtime(interpreter: embedded,
                               workingDirectory: payload,
                               helpers: resources.appendingPathComponent("helpers"))
            }
        }

        // Development fallback: MACMAN_REPO, else the path this was built from.
        let repo = ProcessInfo.processInfo.environment["MACMAN_REPO"]
            .map { URL(fileURLWithPath: $0) }
            ?? URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()   // MACman
                .deletingLastPathComponent()   // Sources
                .deletingLastPathComponent()   // app
                .deletingLastPathComponent()   // repo root

        let venv = repo.appendingPathComponent(".venv/bin/python3")
        if FileManager.default.isExecutableFile(atPath: venv.path) {
            return Runtime(interpreter: venv, workingDirectory: repo, helpers: nil)
        }
        return nil
    }
}
