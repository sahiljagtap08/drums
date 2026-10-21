import Foundation

// A tiny HTTP/1.1 reader and writer. It only understands Content-Length
// bodies (no chunked encoding), which is all the envd API needs.

public struct HTTPRequest: Sendable {
    public let method: String
    public let path: String
    public let query: [String: String]
    /// Header names are stored in lower case.
    public let headers: [String: String]
    public let body: Data

    public init(method: String, path: String, query: [String: String] = [:],
                headers: [String: String] = [:], body: Data = Data()) {
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
    }

    /// The biggest body the parser accepts. A larger Content-Length is malformed.
    public static let maxBodyBytes = 64 * 1024 * 1024

    /// The result of looking at the bytes read so far.
    public enum ParseResult: Sendable {
        /// More bytes are needed before a full request is there.
        case incomplete
        /// The bytes can never become a valid request.
        case malformed
        /// A full request, and how many bytes it used.
        case complete(HTTPRequest, consumed: Int)
    }

    /// Returns the request and the number of bytes it used, or nil if the data
    /// is not a full valid request yet.
    public static func parse(_ data: Data) -> (HTTPRequest, consumed: Int)? {
        if case let .complete(req, used) = parseResult(data) {
            return (req, used)
        }
        return nil
    }

    /// Same as `parse`, but tells "need more bytes" apart from "bad request".
    public static func parseResult(_ data: Data) -> ParseResult {
        let bytes = [UInt8](data)
        let marker: [UInt8] = [13, 10, 13, 10]  // CR LF CR LF
        guard let end = find(marker, in: bytes) else { return .incomplete }
        let headerBytes = bytes[0..<end]
        guard let headerText = String(bytes: headerBytes, encoding: .utf8) else { return .malformed }

        var lines = headerText.components(separatedBy: "\r\n")
        let requestLine = lines.removeFirst().split(separator: " ", omittingEmptySubsequences: true)
        guard requestLine.count == 3, requestLine[2].hasPrefix("HTTP/1.") else { return .malformed }

        var headers: [String: String] = [:]
        for line in lines where !line.isEmpty {
            guard let colon = line.firstIndex(of: ":") else { return .malformed }
            let name = line[line.startIndex..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            headers[name] = value
        }
        if headers["transfer-encoding"] != nil { return .malformed }  // chunked is not supported

        var length = 0
        if let raw = headers["content-length"] {
            // Refuse huge or negative lengths here so the sums below cannot overflow.
            guard let n = Int(raw), n >= 0, n <= maxBodyBytes else { return .malformed }
            length = n
        }
        let bodyStart = end + marker.count
        let (total, overflow) = bodyStart.addingReportingOverflow(length)
        if overflow { return .malformed }
        guard bytes.count >= total else { return .incomplete }

        let target = String(requestLine[1])
        var path = target
        var query: [String: String] = [:]
        if let q = target.firstIndex(of: "?") {
            path = String(target[target.startIndex..<q])
            query = parseQuery(String(target[target.index(after: q)...]))
        }
        let req = HTTPRequest(
            method: String(requestLine[0]),
            path: path.removingPercentEncoding ?? path,
            query: query,
            headers: headers,
            body: Data(bytes[bodyStart..<total]))
        return .complete(req, consumed: total)
    }

    private static func parseQuery(_ s: String) -> [String: String] {
        var out: [String: String] = [:]
        for pair in s.split(separator: "&", omittingEmptySubsequences: true) {
            let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let key = decode(String(parts[0]))
            let value = parts.count > 1 ? decode(String(parts[1])) : ""
            out[key] = value
        }
        return out
    }

    private static func decode(_ s: String) -> String {
        let spaced = s.replacingOccurrences(of: "+", with: " ")
        return spaced.removingPercentEncoding ?? spaced
    }

    private static func find(_ needle: [UInt8], in hay: [UInt8]) -> Int? {
        guard hay.count >= needle.count else { return nil }
        for i in 0...(hay.count - needle.count) where hay[i] == needle[0] {
            if Array(hay[i..<(i + needle.count)]) == needle { return i }
        }
        return nil
    }
}

public struct HTTPResponse: Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: Data

    public init(status: Int, headers: [String: String] = [:], body: Data = Data()) {
        self.status = status
        self.headers = headers
        self.body = body
    }

    /// A JSON response from raw JSON text.
    public static func json(_ text: String, status: Int = 200) -> HTTPResponse {
        HTTPResponse(status: status,
                     headers: ["Content-Type": "application/json"],
                     body: Data(text.utf8))
    }

    static let reasons: [Int: String] = [
        200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
        409: "Conflict", 413: "Payload Too Large", 500: "Internal Server Error",
    ]

    /// Bytes ready to write to the socket. Content-Length is always set from the
    /// body, and the connection is closed after the response.
    public func serialize() -> Data {
        var h = headers
        for key in h.keys where key.lowercased() == "content-length" || key.lowercased() == "connection" {
            h.removeValue(forKey: key)
        }
        h["Content-Length"] = String(body.count)
        h["Connection"] = "close"
        var text = "HTTP/1.1 \(status) \(Self.reasons[status] ?? "Unknown")\r\n"
        for key in h.keys.sorted() { text += "\(key): \(h[key]!)\r\n" }
        text += "\r\n"
        var out = Data(text.utf8)
        out.append(body)
        return out
    }
}
