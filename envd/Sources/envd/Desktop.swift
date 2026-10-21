import ApplicationServices
import CoreGraphics
import EnvdCore
import Foundation

// Window listing and accessibility text.
enum Desktop {
    /// Windows of this user's processes in this login session. The window
    /// list only holds this session's windows, and the uid check drops
    /// system-owned ones. Titles of other apps' windows need Screen Recording.
    static func windows() -> [WindowEntry] {
        guard let list = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] else { return [] }
        let me = UInt32(getuid())
        var owners: [Int: Bool] = [:]
        return Windows.entries(from: list) { pid in
            if let known = owners[pid] { return known }
            let ours = Processes.info(pid: Int32(pid))?.uid == me
            owners[pid] = ours
            return ours
        }
    }

    enum AXResult {
        case text(String)
        /// Nothing to read. `reason` says why.
        case unavailable(reason: String)
    }

    /// The value of the focused UI element of the app with this pid. Needs
    /// Accessibility. This never shows a permission prompt.
    static func focusedText(pid: Int32) -> AXResult {
        guard AXIsProcessTrusted() else { return .unavailable(reason: "ax_not_trusted") }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 2)
        var focused: CFTypeRef?
        let f = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &focused)
        guard f == .success, let focused, CFGetTypeID(focused) == AXUIElementGetTypeID() else {
            return .unavailable(reason: "no_focused_element")
        }
        let element = focused as! AXUIElement
        var value: CFTypeRef?
        let v = AXUIElementCopyAttributeValue(element, kAXValueAttribute as CFString, &value)
        guard v == .success, let text = value as? String else {
            return .unavailable(reason: "no_text_value")
        }
        return .text(text)
    }
}
