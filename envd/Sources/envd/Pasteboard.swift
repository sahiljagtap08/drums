import AppKit
import EnvdCore
import Foundation

// The general pasteboard (the clipboard). Reading and writing it needs no
// special permission.
enum Pasteboard {
    struct Snapshot {
        let text: String?
        let hash: String
        let changeCount: Int
    }

    static func read() -> Snapshot {
        let pb = NSPasteboard.general
        let text = pb.string(forType: .string)
        return Snapshot(text: text, hash: PasteboardHash.hash(of: text), changeCount: pb.changeCount)
    }

    /// Replaces the contents with one string and returns what is there now.
    static func set(_ text: String) -> Snapshot {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        return read()
    }

    static func clear() {
        NSPasteboard.general.clearContents()
    }
}
