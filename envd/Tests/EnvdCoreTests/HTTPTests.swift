import Testing
@testable import EnvdCore

@Test func parsesRequestWithQueryAndBody() throws {
    let raw = rawBytes("POST /action?x=1 HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}")
    let (req, used) = try #require(HTTPRequest.parse(raw))
    #expect(req.method == "POST")
    #expect(req.path == "/action")
    #expect(req.query["x"] == "1")
    #expect(used == raw.count)
}

@Test func incompleteBodyNeedsMoreBytes() {
    let raw = rawBytes("POST /a HTTP/1.1\r\nContent-Length: 5\r\n\r\nab")
    #expect(HTTPRequest.parse(raw) == nil)
    guard case .incomplete = HTTPRequest.parseResult(raw) else {
        Issue.record("expected incomplete")
        return
    }
}

@Test func badRequestLineIsMalformed() {
    guard case .malformed = HTTPRequest.parseResult(rawBytes("NONSENSE\r\n\r\n")) else {
        Issue.record("expected malformed")
        return
    }
}

@Test func serializeSetsContentLength() {
    let res = HTTPResponse.json("{}")
    let text = String(decoding: res.serialize(), as: UTF8.self)
    #expect(text.hasPrefix("HTTP/1.1 200 OK\r\n"))
    #expect(text.contains("Content-Length: 2\r\n"))
    #expect(text.hasSuffix("\r\n\r\n{}"))
}
