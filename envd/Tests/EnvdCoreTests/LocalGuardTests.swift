import Testing
@testable import EnvdCore

@Test func localGuardAllowsPlainLocalClients() {
    let ok = HTTPRequest(method: "POST", path: "/gen", headers: ["host": "127.0.0.1:7701"])
    #expect(LocalGuard.check(ok, port: 7701) == nil)
    let name = HTTPRequest(method: "POST", path: "/gen", headers: ["host": "LocalHost:7701"])
    #expect(LocalGuard.check(name, port: 7701) == nil)
}

@Test func localGuardRefusesOriginAndBadHost() {
    let origin = HTTPRequest(method: "POST", path: "/gen", headers: ["host": "127.0.0.1:7701", "origin": "http://x"])
    #expect(LocalGuard.check(origin, port: 7701)?.status == 403)
    let host = HTTPRequest(method: "POST", path: "/gen", headers: ["host": "example.com"])
    #expect(LocalGuard.check(host, port: 7701)?.status == 403)
    let none = HTTPRequest(method: "POST", path: "/gen")
    #expect(LocalGuard.check(none, port: 7701)?.status == 403)
}

@Test func forbiddenHasAReasonPhrase() {
    let text = String(decoding: HTTPResponse(status: 403).serialize(), as: UTF8.self)
    #expect(text.hasPrefix("HTTP/1.1 403 Forbidden\r\n"))
}

@Test func localGuardRefusesGetWithOriginOrBadHost() {
    // GET routes carry the same protection as mutating ones (DNS rebinding).
    for path in ["/health", "/screenshot", "/log", "/pb", "/windows", "/axtext"] {
        let origin = HTTPRequest(method: "GET", path: path, headers: ["host": "127.0.0.1:7701", "origin": "http://evil"])
        #expect(LocalGuard.check(origin, port: 7701)?.status == 403)
        let rebound = HTTPRequest(method: "GET", path: path, headers: ["host": "evil.example:7701"])
        #expect(LocalGuard.check(rebound, port: 7701)?.status == 403)
        let ok = HTTPRequest(method: "GET", path: path, headers: ["host": "127.0.0.1:7701"])
        #expect(LocalGuard.check(ok, port: 7701) == nil)
    }
}
