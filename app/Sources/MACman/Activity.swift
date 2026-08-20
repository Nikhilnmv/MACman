// What MACman did, and what left the Mac.
//
// The trust centrepiece: everything else in this app is a claim, and this is
// the evidence. Two choices shape it.
//
// **"Nothing left this Mac" is stated, not implied.** Absence of a warning is
// weak reassurance; a row that says so explicitly is the thing worth reading.
// It is the common case, so it is also the quiet one visually — green, small,
// and never competing with the rows that matter.
//
// **A pre-approved send looks different from one you approved.** Standing
// permission buys fewer interruptions, not less visibility, so it is called
// out in the entry rather than blending into normal traffic.

import SwiftUI

struct ActivityEntry: Decodable, Identifiable, Equatable {
    let ts: Double
    let session: String
    let kind: String
    let title: String
    let engine: String
    let detail: String
    let tools: [String]
    let egress: String
    let ok: Bool

    // The log has no ids; a timestamp plus session is unique in practice and
    // stable across refreshes, which is what List needs.
    var id: String { "\(ts)-\(session)-\(title.prefix(24))" }

    var isTask: Bool { kind == "task" }
    var leftTheMac: Bool { !egress.isEmpty }
    var wasPreApproved: Bool { egress.contains("without asking") }
}

struct ActivitySnapshot: Decodable, Equatable {
    var entries: [ActivityEntry] = []
    var tasks_today: Int = 0
    var sent_today: Int = 0
    var audit_path: String = ""
    var note: String = ""
}

struct ActivityTab: View {
    @ObservedObject var daemon: DaemonController

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()

            if daemon.activity.entries.isEmpty {
                VStack(spacing: 6) {
                    Text("Nothing yet").foregroundStyle(.secondary)
                    Text("Tasks you ask MACman to do will appear here.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(daemon.activity.entries) { entry in
                            ActivityRow(entry: entry)
                            Divider()
                        }
                    }
                }
            }

            Divider()
            HStack {
                Text(daemon.activity.note)
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button("Refresh") { daemon.loadActivity() }
                    .controlSize(.small)
            }
            .padding(12)
        }
        .onAppear { daemon.loadActivity() }
    }

    private var header: some View {
        HStack(spacing: 20) {
            stat("\(daemon.activity.tasks_today)", "tasks today")
            // The number that carries the whole privacy claim. Green at zero,
            // and deliberately not hidden when it is zero — "0 sent out" is
            // the sentence someone wants to see.
            stat("\(daemon.activity.sent_today)", "sent out today",
                 tint: daemon.activity.sent_today == 0 ? .green : .orange)
            Spacer()
        }
        .padding(14)
    }

    private func stat(_ value: String, _ label: String,
                      tint: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value).font(.title2).fontWeight(.medium).foregroundStyle(tint)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct ActivityRow: View {
    let entry: ActivityEntry

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(Self.clock.string(from: Date(timeIntervalSince1970: entry.ts)))
                .font(.caption).foregroundStyle(.secondary)
                .frame(width: 44, alignment: .leading)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    if !entry.ok {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange).font(.caption)
                    }
                    Text(entry.title)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if !entry.detail.isEmpty {
                    Text(entry.detail)
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if entry.isTask {
                    HStack(spacing: 6) {
                        if !entry.engine.isEmpty {
                            Tag(text: entry.engine,
                                tint: entry.engine == "local" ? .green : .orange)
                        }
                        ForEach(entry.tools, id: \.self) { tool in
                            Tag(text: tool, tint: .secondary)
                        }
                    }

                    if entry.leftTheMac {
                        Label(entry.egress, systemImage: entry.wasPreApproved
                              ? "paperplane.circle.fill" : "paperplane")
                            .font(.caption)
                            .foregroundStyle(entry.wasPreApproved ? .orange : .primary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        // Said out loud rather than left to inference.
                        Label("Nothing left this Mac", systemImage: "checkmark.shield")
                            .font(.caption2).foregroundStyle(.green)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private static let clock: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter
    }()
}

struct Tag: View {
    let text: String
    var tint: Color = .secondary

    var body: some View {
        Text(text)
            .font(.caption2)
            .padding(.horizontal, 5).padding(.vertical, 1)
            .background(tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 3))
            .foregroundStyle(tint)
    }
}
