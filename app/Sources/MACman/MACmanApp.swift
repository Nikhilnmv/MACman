// MACman's menu bar app.
//
// Phase C is deliberately narrow: prove the permission model. The app starts
// the daemon as a child, shows real status, and lets the user stop it. No
// settings, no consent dialog, no setup wizard — those come once the thing
// they depend on is known to work.
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
    }
}

let SettingsWindowID = "macman-settings"

/// Separate view so the icon re-renders when the daemon's state changes;
/// observation does not reach into a `label:` closure otherwise.
struct MenuBarLabel: View {
    @ObservedObject var daemon: DaemonController

    var body: some View {
        Image(systemName: daemon.state.symbolName)
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
            Text("\(daemon.status.tasksToday) tasks today · "
                 + "\(daemon.status.sentOut) sent out")

            // A model without tools answers from memory instead of looking
            // things up, which looks like MACman being vague rather than
            // broken. Surfaced, not hidden behind a green dot.
            if !daemon.status.tools {
                Text("⚠ On-device model cannot use tools")
            }
            if !daemon.status.fullDiskAccess {
                Text("⚠ No Full Disk Access — cannot receive texts")
            }
        }

        Divider()

        Button("Settings…") {
            // LSUIElement apps have no Dock icon, so a new window can open
            // behind whatever is in front. Activate before opening.
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: SettingsWindowID)
            daemon.loadSettings()
        }
        .keyboardShortcut(",")

        if daemon.state == .running || daemon.state == .starting {
            Button("Stop MACman") { daemon.stop() }
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

    private var headline: String {
        guard daemon.state == .running else { return daemon.state.summary }
        return "Running — \(daemon.status.engine)"
    }
}
