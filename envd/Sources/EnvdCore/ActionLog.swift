import Foundation

// One line of the action log. The field names are the wire names, so
// `latency_ms` keeps its underscore.
public struct ActionLogEntry: Codable, Sendable, Equatable {
    /// Seconds since 1970, with fractions.
    public var ts: Double
    /// "action", "screenshot" or "pb".
    public var kind: String
    public var gen: Int
    /// The action's seq. Entries that have none (screenshots) use 0.
    public var seq: Int
    public var type: String
    public var status: String
    public var latency_ms: Int
    /// Only on "pb" entries: the pasteboard hash (see PasteboardHash).
    public var hash: String?
    /// Only on "pb" entries: the bundle id (or name) of the front app.
    public var frontmost: String?

    public init(ts: Double, kind: String, gen: Int, seq: Int, type: String, status: String, latency_ms: Int,
                hash: String? = nil, frontmost: String? = nil) {
        self.ts = ts
        self.kind = kind
        self.gen = gen
        self.seq = seq
        self.type = type
        self.status = status
        self.latency_ms = latency_ms
        self.hash = hash
        self.frontmost = frontmost
    }

    /// The entry the poller writes every 2 seconds.
    public static func pasteboardSample(ts: Double, gen: Int, hash: String, frontmost: String?) -> ActionLogEntry {
        ActionLogEntry(ts: ts, kind: "pb", gen: gen, seq: 0, type: "pb", status: "ok", latency_ms: 0,
                       hash: hash, frontmost: frontmost)
    }
}

// A bounded in-memory log. When it is full the oldest entries are dropped.
public actor ActionLog {
    private var entries: [ActionLogEntry] = []
    private let capacity: Int

    public init(capacity: Int = 5000) {
        self.capacity = max(1, capacity)
    }

    public func append(_ e: ActionLogEntry) {
        entries.append(e)
        if entries.count > capacity { entries.removeFirst(entries.count - capacity) }
    }

    /// Entries with `ts` at or after the given time, oldest first. "At or
    /// after" means a client that polls with the last time it saw may get that
    /// entry again, but it can never miss one that shares the same time.
    public func since(_ ts: Double) -> [ActionLogEntry] {
        entries.filter { $0.ts >= ts }
    }

    /// Newline-delimited JSON: one entry per line, keys sorted.
    public static func ndjson(_ entries: [ActionLogEntry]) -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        var out = Data()
        for e in entries {
            if let line = try? encoder.encode(e) {
                out.append(line)
                out.append(10)
            }
        }
        return out
    }
}
