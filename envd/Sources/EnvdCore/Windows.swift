import Foundation

// Turns the raw window list from CGWindowListCopyWindowInfo into the shape
// GET /windows returns. The raw keys are plain strings, so this can be tested
// with made-up data.
public struct WindowEntry: Equatable, Sendable {
    public let id: Int
    public let pid: Int
    public let app: String
    public let title: String
    public let x: Double
    public let y: Double
    public let w: Double
    public let h: Double
    public let layer: Int
}

public enum Windows {
    /// Keeps the windows whose owner pid passes `owned`. Order is kept (the
    /// system lists windows front to back). A window with no id or no pid is
    /// dropped. A missing title or app name becomes "". Titles of other
    /// apps' windows are empty unless Screen Recording is granted.
    public static func entries(from raw: [[String: Any]], owned: (Int) -> Bool) -> [WindowEntry] {
        var out: [WindowEntry] = []
        for w in raw {
            guard let id = int(w["kCGWindowNumber"]), let pid = int(w["kCGWindowOwnerPID"]), owned(pid) else { continue }
            let b = w["kCGWindowBounds"] as? [String: Any] ?? [:]
            out.append(WindowEntry(
                id: id, pid: pid,
                app: w["kCGWindowOwnerName"] as? String ?? "",
                title: w["kCGWindowName"] as? String ?? "",
                x: num(b["X"]) ?? 0, y: num(b["Y"]) ?? 0,
                w: num(b["Width"]) ?? 0, h: num(b["Height"]) ?? 0,
                layer: int(w["kCGWindowLayer"]) ?? 0))
        }
        return out
    }

    /// The JSON array for the response body.
    public static func json(_ entries: [WindowEntry]) -> [[String: Any]] {
        entries.map { e in
            ["id": e.id, "pid": e.pid, "app": e.app, "title": e.title, "layer": e.layer,
             "bounds": ["x": e.x, "y": e.y, "w": e.w, "h": e.h]]
        }
    }

    private static func num(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let n = v as? NSNumber { return n.doubleValue }
        return nil
    }

    private static func int(_ v: Any?) -> Int? {
        if let i = v as? Int { return i }
        if let n = v as? NSNumber { return n.intValue }
        return nil
    }
}
