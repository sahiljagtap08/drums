import Testing
@testable import EnvdCore

@Test func healthHasThePermissionBooleans() {
    let body = Health.payload(env: 3, uid: 501, gen: 2, onConsole: true, screenW: 1920, screenH: 1080,
                              scale: 1, version: "x", axTrusted: false, screenRecording: true)
    let parsed = viaJSON(body)
    #expect(parsed["ax_trusted"] as? Bool == false)
    #expect(parsed["screen_recording"] as? Bool == true)
    // The old keys are all still there.
    for key in ["env", "uid", "gen", "on_console", "screen", "version"] {
        #expect(parsed[key] != nil, "\(key) missing")
    }
}

@Test func pasteboardHashIsSha256OrEmpty() {
    #expect(PasteboardHash.hash(of: nil) == "empty")
    #expect(PasteboardHash.hash(of: "abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    #expect(PasteboardHash.hash(of: "") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
}

@Test func windowsKeepOnlyOwnedPidsAndMapFields() {
    let raw = [
        rawWindow(id: 1, pid: 10, app: "TextEdit", title: "note.txt", x: 5, y: 6, w: 700, h: 500),
        rawWindow(id: 2, pid: 99, app: "Other", title: "x"),
        rawWindow(id: 3, pid: 10, app: "TextEdit"),
    ]
    let got = Windows.entries(from: raw, owned: { $0 == 10 })
    #expect(got.map(\.id) == [1, 3])
    #expect(got[0] == WindowEntry(id: 1, pid: 10, app: "TextEdit", title: "note.txt",
                                  x: 5, y: 6, w: 700, h: 500, layer: 0))
    #expect(got[1].title == "")

    let json = Windows.json(got)
    let bounds = json[0]["bounds"] as? [String: Double]
    #expect(bounds == ["x": 5, "y": 6, "w": 700, "h": 500])
    #expect(json[0]["pid"] as? Int == 10)
}

@Test func windowsDropEntriesWithNoIdOrPid() {
    let got = Windows.entries(from: [["kCGWindowOwnerPID": 10], ["kCGWindowNumber": 4]], owned: { _ in true })
    #expect(got.isEmpty)
}

@Test func requestGenComesFromQueryOrBody() {
    let q = HTTPRequest(method: "DELETE", path: "/pb", query: ["gen": "5"])
    #expect(RequestGen.supplied(q) == 5)
    let b = HTTPRequest(method: "POST", path: "/pb", body: bytes(#"{"gen":6,"text":"x"}"#))
    #expect(RequestGen.supplied(b) == 6)
    #expect(RequestGen.supplied(HTTPRequest(method: "GET", path: "/pb")) == nil)
}

@Test func pasteboardSampleEntryShape() {
    let e = ActionLogEntry.pasteboardSample(ts: 10, gen: 4, hash: "empty", frontmost: "com.apple.TextEdit")
    #expect(e.kind == "pb" && e.gen == 4 && e.hash == "empty" && e.frontmost == "com.apple.TextEdit")
    let line = bodyText(HTTPResponse(status: 200, body: ActionLog.ndjson([e])))
    #expect(line.contains(#""hash":"empty""#))
    #expect(line.contains(#""frontmost":"com.apple.TextEdit""#))
    // Other entries do not grow the new keys.
    let plain = ActionLogEntry(ts: 1, kind: "action", gen: 1, seq: 1, type: "click", status: "ok", latency_ms: 1)
    #expect(!bodyText(HTTPResponse(status: 200, body: ActionLog.ndjson([plain]))).contains("hash"))
}
