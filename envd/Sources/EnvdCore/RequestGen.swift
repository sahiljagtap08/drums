import Foundation

public enum RequestGen {
    /// The gen a request was made for: the `gen` query parameter, or else the
    /// integer `gen` field of a JSON body. Nil when there is none.
    public static func supplied(_ req: HTTPRequest) -> Int? {
        if let q = req.query["gen"], let n = Int(q) { return n }
        guard let obj = try? JSONSerialization.jsonObject(with: req.body) as? [String: Any] else { return nil }
        return obj["gen"] as? Int
    }
}
