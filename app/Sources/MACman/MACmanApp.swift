// MACman's menu bar app.
//
// Three surfaces, each with a different job:
//
// * **Menu bar** — state at a glance, and the controls that stop things.
// * **Settings** — permissions, allowlist, engine, and the activity log. It can
//   configure consent and show its history; it can never grant consent.
// * **Setup** — first run, ordered so nothing is asked for before the user
//   knows what MACman cannot do.
//
// Consent lives in none of them. It is a native dialog, because a browser
// extension can read and click a window but not an NSAlert — see
// ConsentDialog.swift.
//
// The one number that earns its place in the menu bar is `sentOut`. It is
// almost always zero, and a zero visible without opening anything is a better
// privacy claim than any sentence in a settings pane.

import SwiftUI

/// Owns the daemon across the app's whole life.
///
/// `MenuBarExtra` provides no launch hook — its content is only built when the
/// menu is opened, so starting the daemon from a view meant MACman did nothing
/// at all until someone clicked the icon. The app delegate is the only place
/// that reliably runs at launch and at termination.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let daemon = DaemonController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Writing to a pipe whose reader has exited raises SIGPIPE, and the
        // default action for SIGPIPE is to kill the process. The daemon dying
        // must never take the app down with it — the menu bar is the only
        // place the user can find out that something went wrong.
        signal(SIGPIPE, SIG_IGN)
        daemon.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        // An orphaned daemon would keep running under permissions granted to
        // this app, with no icon left to stop it from.
        daemon.stop()
    }
}

@main
struct MACmanApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuContent(daemon: delegate.daemon)
        } label: {
            MenuBarLabel(daemon: delegate.daemon)
        }
        .menuBarExtraStyle(.menu)

        Window("MACman Settings", id: SettingsWindowID) {
            SettingsWindow(daemon: delegate.daemon)
        }
        .windowResizability(.contentSize)

        Window("Set up MACman", id: SetupWindowID) {
            SetupWindow(daemon: delegate.daemon)
        }
        .windowResizability(.contentSize)
    }
}

let SettingsWindowID = "macman-settings"
let SetupWindowID = "macman-setup"

/// Separate view so the icon re-renders when the daemon's state changes;
/// observation does not reach into a `label:` closure otherwise.
struct MenuBarLabel: View {
    @ObservedObject var daemon: DaemonController

    var body: some View {
        Image(systemName: daemon.state == .running && !daemon.status.listening
              ? "desktopcomputer.trianglebadge.exclamationmark"
              : daemon.state.symbolName)
    }
}

extension DaemonController.State {
    var symbolName: String {
        switch self {
        case .running:  return "desktopcomputer"
        case .starting: return "desktopcomputer.trianglebadge.exclamationmark"
        case .stopped:  return "desktopcomputer.slash"
        case .failed:   return "exclamationmark.triangle.fill"
        }
    }

    var summary: String {
        switch self {
        case .running:  return "Running"
        case .starting: return "Starting…"
        case .stopped:  return "Stopped"
        case .failed(let why): return why
        }
    }
}

struct MenuContent: View {
    @ObservedObject var daemon: DaemonController
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(headline)

        if case .failed(let why) = daemon.state {
            Text(why)
        } else if daemon.state == .running {
            // The reason it is not listening matters more than the fact, so it
            // is shown in full rather than truncated to a tidy phrase.
            if !daemon.status.listening && !daemon.status.listenDetail.isEmpty {
                Text(daemon.status.listenDetail)
            }
            Text("\(daemon.status.tasksToday) tasks today · "
                 + "\(daemon.status.sentOut) sent out")

            // A model without tools answers from memory instead of looking
            // things up, which looks like MACman being vague rather than
            // broken. Surfaced, not hidden behind a green dot.
            if !daemon.status.tools {
                Text("⚠ On-device model cannot use tools")
            }
            // No separate Full Disk Access warning: when that is what stops
            // MACman listening, `listenDetail` above already says so, and two
            // lines about one problem read like two problems.
        }

        Divider()

        Button("Set up MACman…") {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: SetupWindowID)
            daemon.loadSetupStatus()
        }

        Button("Settings…") {
            // LSUIElement apps have no Dock icon, so a new window can open
            // behind whatever is in front. Activate before opening.
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: SettingsWindowID)
            daemon.loadSettings()
        }
        .keyboardShortcut(",")

        if daemon.state == .running || daemon.state == .starting {
            if daemon.status.listening {
                Button("Stop listening") { daemon.stopListening() }
            } else {
                Button("Start listening") { daemon.startListening() }
            }
            Button("Refresh") { daemon.refresh() }
            Button("Show me a consent request…") { daemon.testConsent() }
        } else {
            Button("Start MACman") { daemon.start() }
        }

        Divider()
        Button("Quit MACman") {
            daemon.stop()
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q")
    }

    /// What the icon and first line claim.
    ///
    /// "Running" used to mean "the bridge process is alive", which was true
    /// while MACman answered no messages at all. It now describes the product:
    /// listening, or not, and why.
    private var headline: String {
        guard daemon.state == .running else { return daemon.state.summary }
        return daemon.status.listening
            ? "Listening for texts — \(daemon.status.engine)"
            : "Not listening"
    }
}
