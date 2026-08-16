# What MACman can do

Everything shipped and working today, with the phrasing that gets it done.

For architecture and the free/paid boundary see [CAPABILITY.md](CAPABILITY.md);
for what's planned see [ROADMAP.md](ROADMAP.md).

**Status:** ✅ verified against ground truth · 🔓 needs a one-time macOS
approval · ⚠️ works, but MACman often picks the wrong tool for it ·
🔵 needs a Claude key · ⬜ planned

Measured reliability for all of this: [RELIABILITY.md](RELIABILITY.md).

**Everything marked ✅ runs entirely on your Mac** — Apple's on-device model,
no key, no network, no cost.

---

## Files and folders

| Ask | Status |
|---|---|
| *"How many PDFs are in my Downloads?"* | ✅ |
| *"What's in my Documents folder?"* | ✅ |
| *"Find files with 'invoice' in the name"* | ✅ |
| *"Read my notes file on the Desktop"* | ✅ |
| *"Create a folder called Research in Documents"* | ✅ |
| *"Move the report from Downloads to Documents"* | ✅ |
| *"Copy that file to my Desktop"* | ✅ |
| *"Rename it to final.pdf"* | ✅ |
| *"Compress the Research folder"* | ✅ |
| *"Move those files to the Trash"* | ✅ asks first |

Deleting goes to the **Trash**, not `rm` — recoverable. It always asks before
acting, and a refusal means nothing is touched.

Folders like `~/.ssh`, `~/.aws` and your Keychain are **refused outright**, and
that refusal can't be overridden by confirming.

## System control

| Ask | Status |
|---|---|
| *"Lock my Mac"* | ✅ |
| *"Put the Mac to sleep"* / *"Turn the display off"* | ✅ |
| *"Set the volume to 40"* · *"Mute"* · *"Unmute"* | ✅ |
| *"What macOS version is this?"* | ✅ |
| *"What's the hostname?"* · *"How much disk space is free?"* | ✅ |
| *"What's the battery at?"* · *"What's the date?"* · *"How long has it been up?"* | ✅ |
| *"Set brightness to 60"* | 🔓 Accessibility |
| *"Restart"* / *"Shut down"* | ✅ asks first |

MACman can **lock your Mac but never unlock it** — macOS blocks that outright,
and MACman doesn't hold your password. The worst case for a lost session is a
*more* locked Mac.

## Network

| Ask | Status |
|---|---|
| *"Is Wi-Fi connected?"* · *"What networks do I have saved?"* | ✅ |
| *"Turn Wi-Fi on"* | ✅ |
| *"Turn Wi-Fi off"* | ✅ asks first |
| *"Join my home network"* | ✅ saved networks only |
| Bluetooth on / off / status | 🔓 needs Bluetooth permission for your terminal |

Wi-Fi and Bluetooth live under the same system control as volume and lock — that merge took Wi-Fi from 0/6 to 5/6 on tool selection.

**No password is ever accepted.** Only networks already saved on this Mac can
be joined — MACman doesn't handle credentials.

Turning Wi-Fi off asks first, since it may be the connection carrying your
session.

## Apps

| Ask | Status |
|---|---|
| *"Open Safari"* / *"Open Pages"* / any app | ✅ |
| *"How many notes do I have?"* · *"Make a note called Shopping"* | ✅ |
| *"How many unread emails?"* · *"Draft an email to Sam about lunch"* | ✅ draft only, never sends |
| *"What reminders do I have?"* · *"Remind me to call the bank"* | ✅ |
| *"What's on my calendar today?"* · *"Add a meeting at 10am"* | 🔓 approve Calendar once |
| *"Export my resume as a PDF"* | ✅ |
| *"Any documents open in Pages?"* | ✅ |
| *"What are my reminders?"* | 🔓 approve Reminders once |

The 🔓 ones show a macOS approval dialog the first time. Approve it and they
work from then on. MACman times out after 3 seconds and tells you which app is
waiting, so an unanswered prompt never stalls a session.

## Developer tools

| Ask | Status |
|---|---|
| *"Open my MACMan project in VS Code"* | ✅ |
| *"Open app.py at line 42"* | ✅ |
| *"Fix the failing test in my project"* | ⏸ needs a working `claude` CLI |
| *"Ask Claude to explain this error"* | ⏸ needs a working `claude` CLI |

MACman doesn't try to *be* a coding assistant — it hands the task to **Claude
Code**, which has its own tools and does the work. Because that edits files and
uses your Claude account, it always asks first, naming the project and the task.

Neither Claude nor VS Code is scriptable via AppleScript, so both are driven by
their **command-line tools** rather than by clicking their windows — the same
result without the 50% accessibility coin flip.

## Getting to it

| Ask | Status |
|---|---|
| Text your Apple ID from another Apple device | ✅ |
| Wake phrase, customisable (*"MACman wake up"*) | ✅ |
| 6-digit code from your authenticator | ✅ |
| *"end session"* — also *stop*, *cancel*, *log out* | ✅ |
| Screenshot attached to replies | ✅ |
| Automatic 30-minute idle timeout | ✅ |

---

## Under the hood

Eighteen primitives. Each takes typed fields rather than a command string —
measured **8/8** correct that way against **1/5** when the model wrote shell
itself. Tool selection across all eighteen measures **99%**
([RELIABILITY.md](RELIABILITY.md)).

| Tool | Actions |
|---|---|
| `count_files` | folder, extension |
| `list_folder` | folder, limit |
| `find_files` | folder, name_contains |
| `read_file` | path, max_lines |
| `system_info` | macos_version · hostname · disk_free · battery · date · uptime |
| `app_info` | notes_count · unread_mail · todays_events · reminders |
| `open_app` | name |
| `system_control` | lock · sleep · display_off · restart · shutdown · mute · unmute · volume · brightness · wifi_on/off/status/list/join · bluetooth_on/off/status |
| `file_operation` | move · copy · rename · trash · compress · make_folder |
| `media_control` | play · pause · next · previous · now_playing · search |
| `browser_control` | open · search · current_url · page_text · new_tab · list_tabs |
| `document_control` | open · read · export_pdf · close · list_open |
| `run_shortcut` | list · run |
| `mail_control` | unread_count · list_recent · draft *(never sends)* |
| `calendar_control` | today · upcoming · create_event |
| `notes_control` | count · list · read · create |
| `reminders_control` | list · create · complete |
| `vscode_control` | open_project · open_file · new_window |
| `claude_code` | hand a coding task to Claude Code |

Every call passes the same checks regardless of which model asked: capability
tier, guard verdict, path validation, audit log.

---

## Coming next

Level 2 — application automation, mostly free. See [ROADMAP.md](ROADMAP.md).

| Planned | Mechanism |
|---|---|
| **Fix the two tool-name collisions** — Wi-Fi and "documents" | descriptions ⬜ |
| Open a specific chat, file or project | URL schemes ⬜ |
| Editing document contents, not just export | AppleScript ⬜ |

Already shipped this round: Pages/Numbers/Keynote open-read-export, browser
control, Music and Spotify playback, and running your own Shortcuts.

Level 3 — orchestrating Claude, Codex, VS Code. Level 4 — the Claude fallback
for vision and novel apps.

---

## Not building yet, and why

### AirDrop — deferred

*"AirDrop that PDF to my iPhone"* is a natural thing to want, and it's on the
vision list. It's deferred because **macOS exposes no scriptable way to send a
file over AirDrop.** There is no command-line tool, no AppleScript dictionary
entry, and no reliable Shortcuts action for choosing a recipient and sending.

The only route is UI automation — open the share sheet, find the device tile,
click it. MACman's Accessibility navigation measured **50% correct**, and this
is the worst possible place for a coin flip: a wrong click sends the **wrong
file** to the **wrong person**, and there's no undo once it's gone.

Half-working here is worse than not shipping it, so it waits until one of:

1. **The Claude tier lands** — better UI reasoning may clear the bar, with a
   confirmation step naming the file and recipient before sending.
2. **A scriptable path appears** — a Shortcuts action or CLI. Worth
   re-checking each macOS release.

Until then MACman will say it can't, rather than guessing.

### Also deferred

| | Why |
|---|---|
| **Bluetooth device connect/disconnect** | Needs `blueutil`; power on/off already works once installed |
| **Changing settings inside System Settings panes** | Accessibility navigation, 50% |
| **WhatsApp file attachments** | Opening a chat is scriptable; attaching a file is UI-only |
| **Anything visual** — *"which photo shows the product?"* | Apple's model is text-only. Verified: it accepts no image input at all. Needs Claude |
| **FaceTime voice control** | Planned for v3 |
