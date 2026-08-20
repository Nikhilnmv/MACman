// The settings window.
//
// Reads and writes nothing itself: every value comes from the daemon and every
// change goes back to it for validation. The daemon owns `config.toml`, its
// permissions and its format, and a second writer is how two processes end up
// disagreeing about what is on disk. So this window is a view, not a source of
// truth — after any change it redraws from what the daemon says is stored,
// rather than from what it hoped it wrote.
//
// It can configure consent and show its history. It can never grant consent —
// that is the dialog's job, for reasons in ConsentDialog.swift.

import SwiftUI

// MARK: - Model

struct PermissionInfo: Decodable, Identifiable, Equatable {
    let key: String
    let name: String
    let granted: Bool
    let because: String
    let unlocks: [String]
    var id: String { key }
}

struct CapabilityInfo: Decodable, Identifiable, Equatable {
    let name: String
    let available: Bool
    let without: String
    let missing: [String]
    var id: String { name }
}

struct PreApprovalInfo: Decodable, Equatable {
    let category: String
    let path: String
    let describe: String
}

struct SettingsSnapshot: Decodable, Equatable {
    var permissions: [PermissionInfo] = []
    var capabilities: [CapabilityInfo] = []
    var allowed_handles: [String] = []
    var wake_phrases: [String] = []
    var session_idle_minutes: Int = 30
    var wake_timeout_seconds: Int = 120
    var attach_screenshot: Bool = true
    var totp_configured: Bool = false
    var cloud_key_configured: Bool = false
    var pre_approvals: [PreApprovalInfo] = []
    var config_path: String = ""
    var audit_path: String = ""
}

// MARK: - Window

struct SettingsWindow: View {
    @ObservedObject var daemon: DaemonController
    @State private var tab = Tab.permissions

    enum Tab: String, CaseIterable, Identifiable {
        case permissions = "Permissions"
        case access = "Who can reach me"
        case engine = "Engine"
        case activity = "Activity"
        var id: String { rawValue }
    }

    var body: some View {
        TabView(selection: $tab) {
            PermissionsTab(daemon: daemon)
                .tabItem { Text(Tab.permissions.rawValue) }
                .tag(Tab.permissions)
            AccessTab(daemon: daemon)
                .tabItem { Text(Tab.access.rawValue) }
                .tag(Tab.access)
            EngineTab(daemon: daemon)
                .tabItem { Text(Tab.engine.rawValue) }
                .tag(Tab.engine)
            ActivityTab(daemon: daemon)
                .tabItem { Text(Tab.activity.rawValue) }
                .tag(Tab.activity)
        }
        .frame(width: 540, height: 460)
        .onAppear { daemon.loadSettings() }
        .overlay(alignment: .bottom) {
            if let note = daemon.settingsNote {
                Text(note.text)
                    .font(.caption)
                    .foregroundStyle(note.ok ? .secondary : Color.red)
                    .padding(8)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 6))
                    .padding(.bottom, 8)
            }
        }
    }
}

// MARK: - Permissions

struct PermissionsTab: View {
    @ObservedObject var daemon: DaemonController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("MACman holds these, not your Terminal.")
                    .font(.callout)
                Text("Each one is optional. Refusing a permission turns off the "
                     + "features that need it, rather than the program.")
                    .font(.caption).foregroundStyle(.secondary)

                ForEach(daemon.settings.permissions) { permission in
                    HStack(alignment: .top, spacing: 10) {
                        // A filled dot reads as state at a glance; the label
                        // beside it is what a screen reader gets.
                        Circle()
                            .fill(permission.granted ? Color.green : Color.secondary.opacity(0.4))
                            .frame(width: 9, height: 9)
                            .padding(.top, 4)
                            .accessibilityLabel(permission.granted ? "granted" : "not granted")

                        VStack(alignment: .leading, spacing: 3) {
                            Text(permission.name).fontWeight(.medium)
                            Text(permission.because)
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            if !permission.granted && !permission.unlocks.isEmpty {
                                Text("Would enable: \(permission.unlocks.joined(separator: ", "))")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button(permission.granted ? "Review…" : "Grant…") {
                            daemon.openPermission(permission.key)
                        }
                        .controlSize(.small)
                    }
                    Divider()
                }

                Text("What works right now")
                    .font(.headline).padding(.top, 4)
                ForEach(daemon.settings.capabilities) { capability in
                    HStack(alignment: .top, spacing: 8) {
                        Text(capability.available ? "✓" : "·")
                            .foregroundStyle(capability.available ? .green : .secondary)
                            .frame(width: 12)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(capability.name)
                                .foregroundStyle(capability.available ? .primary : .secondary)
                            if !capability.available {
                                Text(capability.without)
                                    .font(.caption2).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
            .padding(18)
        }
    }
}

// MARK: - Who can reach me

struct AccessTab: View {
    @ObservedObject var daemon: DaemonController
    @State private var newHandle = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Who can text this Mac")
                    .font(.headline)
                Text("Messages from anyone not listed are dropped in silence, "
                     + "before any model sees them. An empty list means nobody.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                ForEach(daemon.settings.allowed_handles, id: \.self) { handle in
                    HStack {
                        Text(handle).font(.system(.body, design: .monospaced))
                        Spacer()
                        Button("Remove") { daemon.removeHandle(handle) }
                            .controlSize(.small)
                    }
                }

                HStack {
                    TextField("+447700900123 or an Apple ID email", text: $newHandle)
                        .textFieldStyle(.roundedBorder)
                    Button("Add") {
                        daemon.addHandle(newHandle)
                        newHandle = ""
                    }
                    .disabled(newHandle.trimmingCharacters(in: .whitespaces).isEmpty)
                }

                Divider().padding(.vertical, 4)

                Text("Session").font(.headline)
                Text(daemon.settings.totp_configured
                     ? "A login code is set up. Codes come from your authenticator app."
                     : "⚠ No login code configured — run `macman auth provision`.")
                    .font(.caption)
                    .foregroundStyle(daemon.settings.totp_configured ? .secondary : Color.orange)

                Stepper("Session ends after \(daemon.settings.session_idle_minutes) minutes idle",
                        value: Binding(
                            get: { daemon.settings.session_idle_minutes },
                            set: { daemon.setField("session_idle_minutes", $0) }),
                        in: 1...1440)

                Text("Wake phrases: \(daemon.settings.wake_phrases.joined(separator: ", "))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(18)
        }
    }
}

// MARK: - Engine

struct EngineTab: View {
    @ObservedObject var daemon: DaemonController
    @State private var key = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("On-device — the default")
                    .font(.headline)
                Text("Files, folders, system control, Mail, Calendar, Notes and "
                     + "Reminders run on Apple's on-device model. Free, offline, "
                     + "and nothing leaves this Mac.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Divider().padding(.vertical, 4)

                Text("Claude — optional").font(.headline)
                Text("Only for things a small on-device model cannot do: reading "
                     + "code, understanding images. **Every request shows you "
                     + "exactly what would be sent, and asks first.**")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                if daemon.settings.cloud_key_configured {
                    HStack {
                        Text("✓ A Claude key is configured")
                            .foregroundStyle(.green)
                        Spacer()
                        Button("Remove") { daemon.clearCloudKey() }
                            .controlSize(.small)
                    }
                    Text("Stored in your Keychain. It is never displayed again, "
                         + "including here.")
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    // SecureField, so the key is not shoulder-surfable and does
                    // not land in a screenshot of the settings window.
                    SecureField("sk-ant-…", text: $key)
                        .textFieldStyle(.roundedBorder)
                    HStack {
                        Button("Save to Keychain") {
                            daemon.setCloudKey(key)
                            key = ""
                        }
                        .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                        Spacer()
                        Link("Get a key", destination:
                                URL(string: "https://console.anthropic.com")!)
                            .font(.caption)
                    }
                }

                Divider().padding(.vertical, 4)

                Text("Sent without asking").font(.headline)
                if daemon.settings.pre_approvals.isEmpty {
                    Text("Nothing. Every cloud request asks first.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(daemon.settings.pre_approvals.enumerated()),
                            id: \.offset) { index, rule in
                        HStack {
                            Text(rule.describe).font(.caption)
                            Spacer()
                            Button("Revoke") { daemon.removePreApproval(index) }
                                .controlSize(.small)
                        }
                    }
                }
            }
            .padding(18)
        }
    }
}
