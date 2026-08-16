// Tool support for Apple's on-device model.
//
// Compiled only with `-DMACMAN_TOOLS`, because `@Generable` needs the
// `FoundationModelsMacros` plugin, which ships with full Xcode and **not** with
// Command Line Tools. Without the flag the helper still builds and still does
// text generation; it just can't take actions.
//
// ## Why these are narrow and typed, not a shell
//
// Given a raw `bash` tool this model wrote `ls -l ~/Downloads | grep -v . |
// grep Pdf | wc -l`, then `ls -1 Downloads | grep 'PDF'` (exit 1), then
// `df -h /Users/me/Downloads` — and reported confident file counts from all
// three. It is good at picking a tool and filling in typed fields, and bad at
// authoring shell syntax. So it gets fields, and Python builds the command.
//
// ## Why they don't do anything themselves
//
// MACman's security model — the deny/confirm gate, credential-path blocks, the
// audit log, lock-state tiers — lives in Python. A Swift tool that ran a
// command directly would bypass all of it, making the on-device engine the one
// place where protected paths quietly work.
//
// So each tool is a **proxy**: it writes a request to stdout and blocks until
// Python writes back a result.
//
//     swift  → {"type":"tool_request","name":"count_files","arguments":{...}}
//     python → {"type":"tool_result","content":"..."}

#if MACMAN_TOOLS

import Foundation
import FoundationModels

/// Line-delimited JSON channel to the Python side.
///
/// Serialised because the session may call tools concurrently, and two
/// interleaved requests on one pipe would deadlock both.
@available(macOS 26.0, *)
enum ToolChannel {
    private static let lock = NSLock()

    static func request(_ name: String, _ arguments: [String: Any]) -> String {
        lock.lock()
        defer { lock.unlock() }

        let payload: [String: Any] = [
            "type": "tool_request", "name": name, "arguments": arguments,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let line = String(data: data, encoding: .utf8) else {
            return "Tool request could not be encoded."
        }

        print(line)
        fflush(stdout)

        // A closed pipe means Python went away mid-task; report it as a tool
        // failure so the model can say so rather than hanging.
        guard let reply = readLine(strippingNewline: true),
              let replyData = reply.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: replyData)
                  as? [String: Any],
              let content = parsed["content"] as? String else {
            return "MACman did not return a usable tool result."
        }
        return content
    }
}

// MARK: - Files and folders

@available(macOS 26.0, *)
struct CountFilesTool: Tool {
    typealias Output = String
    let name = "count_files"
    let description = """
        Count the files in a folder on this Mac, optionally only those with a \
        given extension. Use this for any question of the form "how many \
        files...". Returns an exact number.
        """

    @Generable
    struct Arguments {
        @Guide(description: "Folder to count in, e.g. Downloads or ~/Documents")
        var folder: String
        @Guide(description: "Extension without the dot, e.g. pdf. Leave empty to count all files.")
        var ext: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("count_files",
                            ["folder": arguments.folder, "extension": arguments.ext ?? ""])
    }
}

@available(macOS 26.0, *)
struct ListFolderTool: Tool {
    typealias Output = String
    let name = "list_folder"
    let description = """
        List what is inside a folder on this Mac. Use this whenever asked what \
        a folder contains. Never guess at the contents of a folder.
        """

    @Generable
    struct Arguments {
        @Guide(description: "Folder to list, e.g. Downloads, ~/Documents, /tmp")
        var folder: String
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("list_folder", ["folder": arguments.folder])
    }
}

@available(macOS 26.0, *)
struct FindFilesTool: Tool {
    typealias Output = String
    let name = "find_files"
    let description = """
        Find files whose name contains some text, searching subfolders too.
        """

    @Generable
    struct Arguments {
        @Guide(description: "Folder to search under, e.g. ~/Documents")
        var folder: String
        @Guide(description: "Text to look for in file names, case-insensitive")
        var nameContains: String
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("find_files",
                            ["folder": arguments.folder,
                             "name_contains": arguments.nameContains])
    }
}

@available(macOS 26.0, *)
struct ReadFileTool: Tool {
    typealias Output = String
    let name = "read_file"
    let description = "Read the beginning of a text file on this Mac."

    @Generable
    struct Arguments {
        @Guide(description: "File to read, e.g. ~/Desktop/notes.txt")
        var path: String
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("read_file", ["path": arguments.path ?? ""])
    }
}

// MARK: - System and apps

@available(macOS 26.0, *)
struct SystemInfoTool: Tool {
    typealias Output = String
    let name = "system_info"
    let description = """
        Look up a fact or current status of this Mac: macos_version, hostname, \
        disk_free, battery, date, uptime, or wifi (whether Wi-Fi is connected \
        and to which network).
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: macos_version, hostname, disk_free, battery, date, uptime, wifi")
        var fact: String
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("system_info", ["fact": arguments.fact])
    }
}

@available(macOS 26.0, *)
struct OpenAppTool: Tool {
    typealias Output = String
    let name = "open_app"
    let description = "Open an application on this Mac."

    @Generable
    struct Arguments {
        @Guide(description: "Application name, e.g. Safari, Notes, Pages")
        var name: String
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("open_app", ["name": arguments.name ?? ""])
    }
}

// MARK: - Level 1 primitives

@available(macOS 26.0, *)
struct SystemControlTool: Tool {
    typealias Output = String
    let name = "system_control"
    let description = """
        Control this Mac's settings and power: locking, sleeping, restarting, \
        shutting down, volume, brightness, Wi-Fi, internet connection, and \
        Bluetooth. Actions: lock, sleep, display_off, restart, shutdown, mute, \
        unmute, volume, brightness, wifi_on, wifi_off, wifi_status, wifi_list, \
        wifi_join, bluetooth_on, bluetooth_off, bluetooth_status. \
        volume and brightness take a value 0-100.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: lock, sleep, display_off, restart, shutdown, mute, unmute, volume, brightness, wifi_on, wifi_off, wifi_status, wifi_list, wifi_join, bluetooth_on, bluetooth_off, bluetooth_status")
        var action: String
        @Guide(description: "Percentage 0-100, only for volume or brightness. Omit otherwise.")
        var value: Int?
        @Guide(description: "Wi-Fi network name, only for wifi_join. Omit otherwise.")
        var name: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("system_control",
                            ["action": arguments.action,
                             "value": arguments.value ?? 0,
                             "name": arguments.name ?? ""])
    }
}

@available(macOS 26.0, *)
struct FileOperationTool: Tool {
    typealias Output = String
    let name = "file_operation"
    let description = """
        Move, copy, rename, trash, compress a file or folder, or create a \
        folder. Use this for any request that changes files on disk.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: move, copy, rename, trash, compress, make_folder")
        var action: String
        @Guide(description: "File or folder to act on, e.g. ~/Downloads/report.pdf")
        var source: String
        @Guide(description: "Destination folder, new name, or archive path. Omit when unused.")
        var destination: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("file_operation",
                            ["action": arguments.action, "source": arguments.source,
                             "destination": arguments.destination ?? ""])
    }
}

// MARK: - Level 2 primitives (application automation)

@available(macOS 26.0, *)
struct MediaControlTool: Tool {
    typealias Output = String
    let name = "media_control"
    let description = """
        Control music playback in Spotify or Apple Music: play, pause, next, \
        previous, now_playing, search.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: play, pause, next, previous, now_playing, search")
        var action: String
        @Guide(description: "Spotify or Music. Empty picks whichever is playing.")
        var app: String?
        @Guide(description: "Search text, only for search. Omit otherwise.")
        var query: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("media_control",
                            ["action": arguments.action, "app": arguments.app ?? "",
                             "query": arguments.query ?? ""])
    }
}

@available(macOS 26.0, *)
struct BrowserControlTool: Tool {
    typealias Output = String
    let name = "browser_control"
    let description = """
        Open pages, search the web, or read what is on screen in a browser: \
        open, search, current_url, page_text, new_tab, list_tabs.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: open, search, current_url, page_text, new_tab, list_tabs")
        var action: String
        @Guide(description: "URL for open/new_tab, or words for search. Empty otherwise.")
        var target: String?
        @Guide(description: "Safari, Chrome, Arc or Firefox. Omit to use whichever is open.")
        var browser: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("browser_control",
                            ["action": arguments.action, "target": arguments.target ?? "",
                             "browser": arguments.browser ?? ""])
    }
}

@available(macOS 26.0, *)
struct DocumentControlTool: Tool {
    typealias Output = String
    let name = "document_control"
    let description = """
        Pages, Numbers and Keynote DOCUMENTS: open one, read its text, export \
        it as PDF, close it, or list which documents are currently open. USE \
        THIS whenever the request mentions a document, spreadsheet, \
        presentation, Pages, Numbers or Keynote. Actions: open, read, \
        export_pdf, close, list_open.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: open, read, export_pdf, close, list_open")
        var action: String
        @Guide(description: "Document path, e.g. ~/Documents/resume.pages")
        var path: String?
        @Guide(description: "Pages, Numbers or Keynote. Omit to infer from the file extension.")
        var app: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("document_control",
                            ["action": arguments.action, "path": arguments.path ?? "",
                             "app": arguments.app ?? ""])
    }
}

@available(macOS 26.0, *)
struct RunShortcutTool: Tool {
    typealias Output = String
    let name = "run_shortcut"
    let description = """
        List or run a macOS Shortcut. Shortcuts reach apps that have no other \
        automation, and cover anything the owner has built themselves.
        """

    @Generable
    struct Arguments {
        @Guide(description: "list or run")
        var action: String
        @Guide(description: "Shortcut name, required for run")
        var name: String?
        @Guide(description: "Optional file to pass as input. Omit if unused.")
        var inputPath: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("run_shortcut",
                            ["action": arguments.action, "name": arguments.name ?? "",
                             "input_path": arguments.inputPath ?? ""])
    }
}

// MARK: - Mail, Calendar, Notes, Reminders

@available(macOS 26.0, *)
struct MailControlTool: Tool {
    typealias Output = String
    let name = "mail_control"
    let description = """
        Email in Mail: unread_count to see how many unread messages, \
        list_recent to see recent senders and subjects, draft to compose a \
        message. Drafting leaves the message open and UNSENT for the owner \
        to send themselves.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: unread_count, list_recent, draft")
        var action: String
        @Guide(description: "Recipient address, only for draft. Omit otherwise.")
        var to: String?
        @Guide(description: "Subject line, only for draft. Omit otherwise.")
        var subject: String?
        @Guide(description: "Message text, only for draft. Omit otherwise.")
        var body: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("mail_control",
                            ["action": arguments.action, "to": arguments.to ?? "",
                             "subject": arguments.subject ?? "",
                             "body": arguments.body ?? ""])
    }
}

@available(macOS 26.0, *)
struct CalendarControlTool: Tool {
    typealias Output = String
    let name = "calendar_control"
    let description = """
        The owner's calendar and schedule: today for what is on today, \
        upcoming for the next week, create_event to add something. Use this \
        for anything about appointments, meetings, events or schedule.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: today, upcoming, create_event")
        var action: String
        @Guide(description: "Event title, only for create_event. Omit otherwise.")
        var title: String?
        @Guide(description: "Start time in ISO format like 2026-08-20T15:00, only for create_event.")
        var when: String?
        @Guide(description: "Event length in minutes. Omit for 60.")
        var durationMinutes: Int?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("calendar_control",
                            ["action": arguments.action,
                             "title": arguments.title ?? "",
                             "when": arguments.when ?? "",
                             "duration_minutes": arguments.durationMinutes ?? 60])
    }
}

@available(macOS 26.0, *)
struct NotesControlTool: Tool {
    typealias Output = String
    let name = "notes_control"
    let description = """
        The Notes app: count how many notes, list their titles, read one, or \
        create a new note. Use this for anything about notes or jotting \
        something down.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: count, list, read, create")
        var action: String
        @Guide(description: "Note title to read, or the title for a new note. Omit for count and list.")
        var title: String?
        @Guide(description: "Note text, only for create. Omit otherwise.")
        var body: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("notes_control",
                            ["action": arguments.action,
                             "title": arguments.title ?? "",
                             "body": arguments.body ?? ""])
    }
}

@available(macOS 26.0, *)
struct RemindersControlTool: Tool {
    typealias Output = String
    let name = "reminders_control"
    let description = """
        The Reminders app and to-do list: list what is outstanding, create a \
        new reminder, or complete one. Use this for anything about reminders, \
        tasks or things to do.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: list, create, complete")
        var action: String
        @Guide(description: "The reminder text to add, or the one to complete. Omit for list.")
        var title: String?
        @Guide(description: "Optional due time in ISO format like 2026-08-20T09:00.")
        var when: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("reminders_control",
                            ["action": arguments.action,
                             "title": arguments.title ?? "",
                             "when": arguments.when ?? ""])
    }
}

// MARK: - Level 3 (developer tools)

@available(macOS 26.0, *)
struct VSCodeControlTool: Tool {
    typealias Output = String
    let name = "vscode_control"
    let description = """
        Open a project or file in VS Code: open_project, open_file, \
        new_window. Use this for anything about opening code, a repository, \
        a project folder or a source file in the editor.
        """

    @Generable
    struct Arguments {
        @Guide(description: "One of: open_project, open_file, new_window")
        var action: String
        @Guide(description: "Folder or file to open, e.g. ~/code/nimoriz")
        var path: String
        @Guide(description: "Line number to jump to, only for open_file. Omit otherwise.")
        var line: Int?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("vscode_control",
                            ["action": arguments.action, "path": arguments.path,
                             "line": arguments.line ?? 0])
    }
}

@available(macOS 26.0, *)
struct ClaudeCodeTool: Tool {
    typealias Output = String
    let name = "claude_code"
    let description = """
        Hand a coding task to Claude Code, which does the work itself. USE \
        THIS for anything needing real code reasoning: fixing a bug, \
        explaining an error, writing or refactoring code, running or \
        repairing tests. Claude edits files in the project and uses the \
        owner's Claude account, so it always asks first.
        """

    @Generable
    struct Arguments {
        @Guide(description: "What Claude should do, in plain language")
        var task: String
        @Guide(description: "Project folder, e.g. ~/code/nimoriz. Omit for the home folder.")
        var project: String?
    }

    func call(arguments: Arguments) async throws -> String {
        ToolChannel.request("claude_code",
                            ["task": arguments.task,
                             "project": arguments.project ?? ""])
    }
}

@available(macOS 26.0, *)
func macmanTools() -> [any Tool] {
    [
        CountFilesTool(), ListFolderTool(), FindFilesTool(), ReadFileTool(),
        SystemInfoTool(), OpenAppTool(),
        SystemControlTool(), FileOperationTool(),
        MediaControlTool(), BrowserControlTool(), DocumentControlTool(),
        RunShortcutTool(),
        MailControlTool(), CalendarControlTool(), NotesControlTool(),
        RemindersControlTool(),
        VSCodeControlTool(), ClaudeCodeTool(),
    ]
}

#endif
