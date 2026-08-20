// First-run setup.
//
// Ordered so that nothing is asked for before the user knows what MACman
// cannot do. A tool that wants Full Disk Access has to earn the click, and the
// only way to earn it is to be specific about the limits first — vague
// reassurance reads as evasion to exactly the audience this is built for.
//
// Two things this deliberately does not do:
//
// **It does not treat permissions as prerequisites.** Every one is optional and
// declining shows what it disables, not a warning. Setup completes without any
// of them; only the text channel genuinely needs Full Disk Access.
//
// **It does not end on an assertion.** The last step runs a real task and
// counts outbound sockets, so the install proves the central claim on the
// user's own machine instead of asking them to trust a number in a document.

import AppKit
import CoreImage.CIFilterBuiltins
import SwiftUI

struct SetupStatus: Decodable, Equatable {
    var has_handles = false
    var has_code = false
    var full_disk = false
    var complete = false
    var text_channel_ready = false
}

struct SelfTestResult: Decodable, Equatable {
    var ok = false
    var task = ""
    var answer = ""
    var outbound = 0
    var elapsed_ms = 0
    var error = ""
}

struct SetupWindow: View {
    @ObservedObject var daemon: DaemonController
    @State private var step = 0

    private let steps = ["Welcome", "Permissions", "Who can reach me",
                         "Your code", "Engine", "Check it works"]

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()

            ScrollView {
                Group {
                    switch step {
                    case 0: WelcomeStep()
                    case 1: PermissionsTab(daemon: daemon)
                    case 2: AccessTab(daemon: daemon)
                    case 3: CodeStep(daemon: daemon)
                    case 4: EngineTab(daemon: daemon)
                    default: SelfTestStep(daemon: daemon)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            Divider()
            footer
        }
        .frame(width: 560, height: 520)
        .onAppear {
            daemon.loadSettings()
            daemon.loadSetupStatus()
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, name in
                Circle()
                    .fill(index == step ? Color.accentColor
                          : index < step ? Color.accentColor.opacity(0.35)
                          : Color.secondary.opacity(0.25))
                    .frame(width: 7, height: 7)
                if index < steps.count - 1 { Spacer(minLength: 0) }
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 14)
        .overlay(alignment: .bottomLeading) {
            Text(steps[step]).font(.headline).padding(.leading, 18)
                .padding(.bottom, -22)
        }
        .padding(.bottom, 26)
    }

    private var footer: some View {
        HStack {
            if step > 0 {
                Button("Back") { step -= 1 }
            }
            Spacer()
            if step < steps.count - 1 {
                // Never blocked: a user who wants to skip a permission and come
                // back later should not have to fight the wizard to do it.
                Button(step == 0 ? "Set up MACman" : "Next") { step += 1 }
                    .keyboardShortcut(.defaultAction)
            } else {
                Button("Done") {
                    NSApplication.shared.keyWindow?.close()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(14)
    }
}

// MARK: - Steps

struct WelcomeStep: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Text your Mac. It does the thing. It texts you back.")
                .font(.title3)

            Text("Before anything is asked of you, three things MACman "
                 + "cannot do:")
                .font(.callout).foregroundStyle(.secondary)

            limit("It can lock your Mac. It can never unlock it.",
                  "macOS blocks synthetic input at the lock screen, and MACman "
                  + "holds no password. The worst case for a stolen session is "
                  + "a more locked Mac.")
            limit("Your login password is never used, requested, or stored.",
                  "Sessions authenticate with a code from your authenticator "
                  + "app, revocable without touching anything about your Mac.")
            limit("Nothing reaches a cloud model without showing you first.",
                  "One place in the code can send data out. It shows you "
                  + "exactly what would go, and refusing is the default.")

            Text("Everything here is optional. Skip anything, change it later "
                 + "in Settings.")
                .font(.caption).foregroundStyle(.secondary).padding(.top, 4)
        }
        .padding(18)
    }

    private func limit(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label(title, systemImage: "checkmark.shield").fontWeight(.medium)
            Text(detail).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 22)
        }
    }
}

struct CodeStep: View {
    @ObservedObject var daemon: DaemonController
    @State private var code = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("A code, not your password")
                .font(.headline)
            Text("Sessions are authenticated with a six-digit code from an "
                 + "authenticator app. It is independent of your login "
                 + "password and can be revoked on its own.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if daemon.setupStatus.has_code && daemon.provisionURI == nil {
                Label("A login code is already set up.", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
                Text("Creating a new one invalidates the current entry in your "
                     + "authenticator app.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Replace it…") { daemon.provisionCode(force: true) }
            } else if let uri = daemon.provisionURI {
                Text("Scan this with your authenticator app:").font(.callout)
                if let image = Self.qr(from: uri) {
                    Image(nsImage: image)
                        .interpolation(.none)
                        .resizable().frame(width: 170, height: 170)
                        .padding(6)
                        .background(.white, in: RoundedRectangle(cornerRadius: 6))
                }
                // The QR *is* the secret. Saying so is more useful than
                // pretending otherwise, and it explains why it is shown once.
                Text("This code is the secret itself — don't photograph it or "
                     + "share the screen while it's visible.")
                    .font(.caption2).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)

                Divider().padding(.vertical, 4)

                Text("Now type a code from the app, so we know it works:")
                    .font(.callout)
                HStack {
                    TextField("123456", text: $code)
                        .textFieldStyle(.roundedBorder).frame(width: 110)
                        .font(.system(.body, design: .monospaced))
                    Button("Verify") { daemon.verifyCode(code); code = "" }
                        .disabled(code.count < 6)
                    if let verified = daemon.codeVerified {
                        Label(verified ? "That worked" : "Not a valid code",
                              systemImage: verified ? "checkmark.circle.fill"
                                                    : "xmark.circle.fill")
                            .foregroundStyle(verified ? .green : .red)
                            .font(.caption)
                    }
                }
                Text("Setup isn't finished until one real code works — "
                     + "otherwise you'd find out while standing somewhere "
                     + "unable to reach your Mac.")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Button("Create a login code") { daemon.provisionCode(force: false) }
                if let note = daemon.provisionNote {
                    Text(note).font(.caption).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(18)
    }

    /// Rendered locally with CoreImage — the URI never leaves this Mac, and
    /// no QR service is involved.
    @MainActor
    static func qr(from string: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(string.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
        let context = CIContext()
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent)
        else { return nil }
        return NSImage(cgImage: cgImage,
                       size: NSSize(width: scaled.extent.width,
                                    height: scaled.extent.height))
    }
}

struct SelfTestStep: View {
    @ObservedObject var daemon: DaemonController

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Prove it, on this Mac")
                .font(.headline)
            Text("This runs one real task on the on-device model and counts "
                 + "every outbound network connection while it does. It is the "
                 + "central claim, checked on your machine rather than "
                 + "asserted in a document.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Button(daemon.selfTestRunning ? "Running…" : "Run the check") {
                daemon.runSelfTest()
            }
            .disabled(daemon.selfTestRunning)

            if let result = daemon.selfTest {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Asked: \(result.task)")
                        .font(.caption).foregroundStyle(.secondary)

                    if result.ok {
                        Label(result.answer, systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .fixedSize(horizontal: false, vertical: true)
                        Label(result.outbound == 0
                              ? "0 network connections — nothing left this Mac"
                              : "\(result.outbound) network connection(s) — "
                                + "this should be zero",
                              systemImage: result.outbound == 0
                                ? "lock.shield.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(result.outbound == 0 ? .green : .red)
                        Text("Answered in \(result.elapsed_ms) ms")
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Label(result.error.isEmpty ? "The check failed" : result.error,
                              systemImage: "xmark.circle.fill")
                            .foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(12)
                .background(.quaternary.opacity(0.4),
                            in: RoundedRectangle(cornerRadius: 8))
            }

            if daemon.setupStatus.complete {
                Divider().padding(.vertical, 4)
                Label("MACman is set up. Text your Mac from your phone.",
                      systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green)
                if !daemon.setupStatus.text_channel_ready {
                    Text("Full Disk Access is still off, so incoming texts "
                         + "can't be read yet. Everything else works.")
                        .font(.caption).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(18)
    }
}
