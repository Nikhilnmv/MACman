// The native dialog shown before anything leaves this Mac.
//
// Native, and not a page served from localhost, for a specific reason: a
// browser extension with host permissions can read any page you open —
// including one your own machine served — and can click its buttons. Showing
// status that way is fine. Granting permission for data to leave is not.
//
// ## Deliberate choices in how this reads
//
// **Refusing is the default button.** The safe answer should be the one a
// reflexive Return press produces. Approving requires choosing it.
//
// **The disclosure is shown in full**, not summarised behind "Details…". A
// consent dialog whose whole point is disclosure should not hide the
// disclosure. It is scrollable rather than truncated, because a payload list
// that runs off the bottom is exactly the part someone needs to see.
//
// **No "don't ask again" checkbox.** Standing permission is a considered
// decision, so it lives in Settings with a scope and an expiry — not on a
// dialog someone is trying to dismiss.

import AppKit

enum ConsentDialog {

    /// Show the disclosure and return the owner's answer.
    ///
    /// - Parameters:
    ///   - reason: Short phrase, e.g. "sends data to Anthropic's API".
    ///   - body: The full rendered disclosure.
    @MainActor
    static func ask(reason: String, body: String) -> Bool {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "MACman \(reason)"
        alert.informativeText = ""

        // NSAlert truncates long informative text; a scrollable accessory view
        // keeps a long payload list readable instead of clipping the part that
        // matters most.
        alert.accessoryView = Self.scrollableText(body)

        // Order matters: the first button is the default. Refusal goes first
        // so Return refuses.
        alert.addButton(withTitle: "Don't send")
        alert.addButton(withTitle: "Send")

        // Escape should also refuse, which it does by mapping to the second
        // button only if that button is the cancel one — so mark it explicitly.
        alert.buttons.first?.keyEquivalent = "\r"
        alert.buttons.last?.keyEquivalent = ""

        // The daemon may be running with no window focused; without this the
        // dialog can appear behind whatever the user is looking at.
        NSApp.activate(ignoringOtherApps: true)

        return alert.runModal() == .alertSecondButtonReturn
    }

    @MainActor
    private static func scrollableText(_ body: String) -> NSView {
        let width: CGFloat = 420
        let maxHeight: CGFloat = 260

        let text = NSTextView()
        text.string = body
        text.isEditable = false
        text.isSelectable = true          // so the payload can be copied
        text.drawsBackground = false
        text.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        text.textContainerInset = NSSize(width: 4, height: 4)

        text.frame = NSRect(x: 0, y: 0, width: width, height: 0)
        text.sizeToFit()
        let height = min(max(text.frame.height, 60), maxHeight)

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0,
                                                width: width, height: height))
        scroll.documentView = text
        scroll.hasVerticalScroller = height >= maxHeight
        scroll.drawsBackground = false
        return scroll
    }
}
