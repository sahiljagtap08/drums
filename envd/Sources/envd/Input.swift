import CoreGraphics
import EnvdCore
import Foundation

// Posts mouse and keyboard events to the current login session. The events go
// to the session event tap, so they reach whatever app is in front. macOS only
// delivers them if this process has Accessibility permission. Without it the
// post call still succeeds and the event is silently dropped.
enum Input {
    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    /// Do one action. `screenScale` is the number of screenshot pixels per
    /// screen point. Click and scroll positions are given in screenshot pixels
    /// and are divided by it to get points. The screenshots envd takes use one
    /// pixel per point, so the route passes 1.
    static func perform(_ a: ActionSpec, screenScale: CGFloat) throws {
        guard let source = CGEventSource(stateID: .hidSystemState) else {
            throw Failure(description: "cannot create event source")
        }
        let flags = eventFlags(a.modifiers)
        switch a.type {
        case "click":
            try click(a, source: source, scale: screenScale, flags: flags, count: 1)
        case "dblclick":
            try click(a, source: source, scale: screenScale, flags: flags, count: 2)
        case "type":
            try type(a.text ?? "", source: source)
        case "key":
            try key(a, source: source, flags: flags)
        case "scroll":
            try scroll(a, source: source, scale: screenScale)
        default:
            throw Failure(description: "unknown action type \(a.type)")
        }
    }

    // MARK: - Mouse

    private static func point(_ a: ActionSpec, scale: CGFloat) -> CGPoint {
        let s = scale > 0 ? scale : 1
        return CGPoint(x: CGFloat(a.x ?? 0) / s, y: CGFloat(a.y ?? 0) / s)
    }

    private static func click(_ a: ActionSpec, source: CGEventSource, scale: CGFloat,
                              flags: CGEventFlags, count: Int) throws {
        let p = point(a, scale: scale)
        let right = a.button == "right"
        let button: CGMouseButton = right ? .right : .left
        let downType: CGEventType = right ? .rightMouseDown : .leftMouseDown
        let upType: CGEventType = right ? .rightMouseUp : .leftMouseUp

        try post(mouse: .mouseMoved, at: p, button: button, source: source, flags: flags, clickState: 0)
        for n in 1...count {
            try post(mouse: downType, at: p, button: button, source: source, flags: flags, clickState: n)
            try post(mouse: upType, at: p, button: button, source: source, flags: flags, clickState: n)
        }
    }

    private static func post(mouse type: CGEventType, at p: CGPoint, button: CGMouseButton,
                             source: CGEventSource, flags: CGEventFlags, clickState: Int) throws {
        guard let e = CGEvent(mouseEventSource: source, mouseType: type, mouseCursorPosition: p, mouseButton: button) else {
            throw Failure(description: "cannot create mouse event")
        }
        if clickState > 0 { e.setIntegerValueField(.mouseEventClickState, value: Int64(clickState)) }
        e.flags = flags
        e.post(tap: .cgSessionEventTap)
    }

    /// Scrolls with wheel deltas in pixels. The numbers go straight to the
    /// system, so they follow its convention: a positive dy scrolls up. If x
    /// and y are given the pointer is moved there first.
    private static func scroll(_ a: ActionSpec, source: CGEventSource, scale: CGFloat) throws {
        if a.x != nil, a.y != nil {
            try post(mouse: .mouseMoved, at: point(a, scale: scale), button: .left,
                     source: source, flags: [], clickState: 0)
        }
        func wheel(_ v: Double?) -> Int32 { Int32(max(-10_000, min(10_000, (v ?? 0).rounded()))) }
        guard let e = CGEvent(scrollWheelEvent2Source: source, units: .pixel, wheelCount: 2,
                              wheel1: wheel(a.dy), wheel2: wheel(a.dx), wheel3: 0) else {
            throw Failure(description: "cannot create scroll event")
        }
        e.post(tap: .cgSessionEventTap)
    }

    // MARK: - Keyboard

    /// Types text one character at a time. Each character is sent as its own
    /// Unicode string on key code 0 with no modifier flags, so the text comes
    /// out the same on any keyboard layout and no key table is involved.
    private static func type(_ text: String, source: CGEventSource) throws {
        for character in text {
            let units = Array(String(character).utf16)
            for down in [true, false] {
                guard let e = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: down) else {
                    throw Failure(description: "cannot create key event")
                }
                units.withUnsafeBufferPointer { buf in
                    e.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
                }
                e.flags = []
                e.post(tap: .cgSessionEventTap)
            }
            usleep(1000)  // a short gap so the app keeps up and the order is kept
        }
    }

    private static func key(_ a: ActionSpec, source: CGEventSource, flags: CGEventFlags) throws {
        guard let name = a.key, let code = KeyCodes.code(for: name) else {
            throw Failure(description: "unknown key \(a.key ?? "")")
        }
        for down in [true, false] {
            guard let e = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(code), keyDown: down) else {
                throw Failure(description: "cannot create key event")
            }
            e.flags = flags
            e.post(tap: .cgSessionEventTap)
        }
    }

    private static func eventFlags(_ names: [String]?) -> CGEventFlags {
        var flags: CGEventFlags = []
        for name in names ?? [] {
            switch name {
            case "cmd": flags.insert(.maskCommand)
            case "shift": flags.insert(.maskShift)
            case "alt": flags.insert(.maskAlternate)
            case "ctrl": flags.insert(.maskControl)
            default: break
            }
        }
        return flags
    }
}
