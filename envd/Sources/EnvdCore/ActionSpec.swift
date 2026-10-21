import Foundation

// One input action sent to POST /action.
public struct ActionSpec: Codable, Sendable, Equatable {
    public var gen: Int
    public var seq: Int
    /// click, dblclick, type, key or scroll.
    public var type: String
    public var x: Double?
    public var y: Double?
    /// left (the default) or right. Used by click and dblclick.
    public var button: String?
    public var text: String?
    public var key: String?
    /// Any of cmd, shift, alt, ctrl.
    public var modifiers: [String]?
    public var dx: Double?
    public var dy: Double?

    public init(gen: Int, seq: Int, type: String, x: Double? = nil, y: Double? = nil,
                button: String? = nil, text: String? = nil, key: String? = nil,
                modifiers: [String]? = nil, dx: Double? = nil, dy: Double? = nil) {
        self.gen = gen
        self.seq = seq
        self.type = type
        self.x = x
        self.y = y
        self.button = button
        self.text = text
        self.key = key
        self.modifiers = modifiers
        self.dx = dx
        self.dy = dy
    }

    public static let types: Set<String> = ["click", "dblclick", "type", "key", "scroll"]
    public static let buttons: Set<String> = ["left", "right"]
    public static let modifierNames: Set<String> = ["cmd", "shift", "alt", "ctrl"]
    /// The longest text one `type` action may carry. Typing blocks the
    /// generation gate while it runs, so the length is kept small.
    public static let maxTextCount = 2000

    public struct DecodeError: Error, CustomStringConvertible, Equatable {
        public let description: String
    }

    /// Reads and checks an action. Each type has its own required fields.
    public static func decode(_ json: Data) throws -> ActionSpec {
        let spec: ActionSpec
        do {
            spec = try JSONDecoder().decode(ActionSpec.self, from: json)
        } catch {
            throw DecodeError(description: "invalid action JSON: \(error)")
        }
        try spec.validate()
        return spec
    }

    private func validate() throws {
        func fail(_ message: String) -> DecodeError { DecodeError(description: message) }
        func finite(_ v: Double?) -> Bool { v.map { $0.isFinite } ?? true }

        guard Self.types.contains(type) else { throw fail("unknown type \(type)") }
        guard finite(x), finite(y), finite(dx), finite(dy) else { throw fail("numbers must be finite") }
        if let button, !Self.buttons.contains(button) { throw fail("button must be left or right") }
        if let modifiers {
            for m in modifiers where !Self.modifierNames.contains(m) {
                throw fail("unknown modifier \(m)")
            }
        }
        switch type {
        case "click", "dblclick":
            guard let x, let y, x >= 0, y >= 0 else { throw fail("\(type) needs x and y, both at least 0") }
        case "type":
            guard let text, !text.isEmpty else { throw fail("type needs non-empty text") }
            guard text.count <= Self.maxTextCount else {
                throw fail("text is longer than \(Self.maxTextCount) characters")
            }
        case "key":
            guard let key else { throw fail("key needs key") }
            guard KeyCodes.code(for: key) != nil else { throw fail("unknown key \(key)") }
        case "scroll":
            guard dx != nil || dy != nil else { throw fail("scroll needs dx or dy") }
        default:
            break
        }
    }
}
