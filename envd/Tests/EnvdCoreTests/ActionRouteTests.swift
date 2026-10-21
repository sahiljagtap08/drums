import Testing
@testable import EnvdCore

// These tests drive ActionHandler directly (the same code POST /action runs)
// with a recording stand-in for the real input layer. Starting the real
// Network.framework server inside a test is not needed for the guarantees.

private let port: UInt16 = 7701
private let goodClick = #"{"gen":7,"seq":11,"type":"click","x":5,"y":6}"#

private func makeHandler(gen: Int = 7) async -> (ActionHandler, Recorder, ActionLog, Generation) {
    let g = Generation(initial: gen)
    let log = ActionLog()
    let rec = Recorder()
    let h = ActionHandler(generation: g, log: log) { spec in rec.record(spec) }
    return (h, rec, log, g)
}

@Test func currentGenIsAppliedAndLogged() async {
    let (h, rec, log, _) = await makeHandler()
    let res = await h.handle(actionRequest(goodClick), port: port)
    #expect(res.status == 200)
    let body = bodyText(res)
    #expect(body.contains(#""seq":11"#))
    #expect(body.contains(#""applied_at":"#))
    #expect(rec.seqs == [11])
    let entries = await log.since(0)
    #expect(entries.map(\.seq) == [11])
    #expect(entries.first?.kind == "action")
    #expect(entries.first?.status == "ok")
    #expect(entries.first?.gen == 7)
}

@Test func staleGenGets409AndIsNotApplied() async {
    // The server is at gen 8; the action was made for gen 7.
    let (h, rec, log, g) = await makeHandler()
    await g.set(8)
    let res = await h.handle(actionRequest(goodClick), port: port)
    #expect(res.status == 409)
    #expect(bodyText(res) == #"{"error":"stale_gen","current_gen":8}"#)
    #expect(rec.count == 0)
    #expect((await log.since(0)).isEmpty)
}

@Test func resetThenStaleActionHasNoEffect() async {
    // The same flow a client sees: reset to gen 9 and then an old action arrives.
    let (h, rec, log, g) = await makeHandler(gen: 7)
    let first = await h.handle(actionRequest(goodClick), port: port)
    #expect(first.status == 200)
    await g.set(9)
    let late = await h.handle(actionRequest(#"{"gen":7,"seq":12,"type":"type","text":"x"}"#), port: port)
    #expect(late.status == 409)
    #expect(bodyText(late) == #"{"error":"stale_gen","current_gen":9}"#)
    #expect(rec.seqs == [11])
    #expect((await log.since(0)).map(\.seq) == [11])
}

@Test func originHeaderIsRejectedWith403() async {
    let (h, rec, log, _) = await makeHandler()
    let req = actionRequest(goodClick, headers: ["host": "127.0.0.1:7701", "origin": "https://evil.example"])
    let res = await h.handle(req, port: port)
    #expect(res.status == 403)
    #expect(rec.count == 0)
    #expect((await log.since(0)).isEmpty)
    // Even an Origin of "null" (sandboxed pages) is refused.
    let nul = await h.handle(actionRequest(goodClick, headers: ["host": "127.0.0.1:7701", "origin": "null"]), port: port)
    #expect(nul.status == 403)
}

@Test func wrongOrMissingHostIsRejectedWith403() async {
    let (h, rec, _, _) = await makeHandler()
    let bad: [[String: String]] = [
        ["host": "evil.example:7701"],
        ["host": "127.0.0.1:9999"],
        ["host": "127.0.0.1"],
        ["host": "attacker.localhost:7701"],
        [:],
    ]
    for headers in bad {
        let res = await h.handle(actionRequest(goodClick, headers: headers), port: port)
        #expect(res.status == 403, "headers \(headers)")
    }
    #expect(rec.count == 0)
}

@Test func localhostHostIsAccepted() async {
    let (h, rec, _, _) = await makeHandler()
    let res = await h.handle(actionRequest(goodClick, headers: ["host": "localhost:7701"]), port: port)
    #expect(res.status == 200)
    #expect(rec.count == 1)
}

@Test func badBodyGets400AndIsNotApplied() async {
    let (h, rec, log, _) = await makeHandler()
    let res = await h.handle(actionRequest(#"{"gen":7,"seq":1,"type":"click"}"#), port: port)
    #expect(res.status == 400)
    #expect(rec.count == 0)
    #expect((await log.since(0)).isEmpty)
}

@Test func failedPerformIsLoggedAndReports500() async {
    struct Boom: Error {}
    let g = Generation(initial: 7)
    let log = ActionLog()
    let h = ActionHandler(generation: g, log: log) { _ in throw Boom() }
    let res = await h.handle(actionRequest(goodClick), port: port)
    #expect(res.status == 500)
    #expect(bodyText(res).contains("action_failed"))
    #expect((await log.since(0)).first?.status == "error")
}

@Test func logRouteReturnsNdjsonAndChecksSince() async {
    let (h, _, _, _) = await makeHandler()
    _ = await h.handle(actionRequest(goodClick), port: port)
    let all = await h.log(HTTPRequest(method: "GET", path: "/log", query: ["since": "0"]))
    #expect(all.status == 200)
    #expect(all.headers["Content-Type"] == "application/x-ndjson")
    #expect(bodyText(all).split(separator: "\n").count == 1)
    let none = await h.log(HTTPRequest(method: "GET", path: "/log", query: ["since": "99999999999"]))
    #expect(none.body.isEmpty)
    let bad = await h.log(HTTPRequest(method: "GET", path: "/log", query: ["since": "soon"]))
    #expect(bad.status == 400)
}

// The atomic gate. A reset must never let an old-gen action take effect after
// the reset has finished. Many old-gen actions race one reset; once the reset
// returns, the applied count must stop growing.
@Test func resetAndActionsAreSerialised() async {
    for _ in 0..<20 {
        let g = Generation(initial: 1)
        let applied = Recorder()
        // Each task returns nil, except the reset task, which returns how many
        // actions had applied when the reset came back.
        var seenAtReset: Int?
        await withTaskGroup(of: Int?.self) { group in
            for i in 0..<200 {
                group.addTask {
                    _ = await g.applyIfCurrent(1) { applied.record(ActionSpec.probe(seq: i)) }
                    return nil
                }
            }
            group.addTask {
                await g.set(2)
                return applied.count
            }
            for await result in group where result != nil { seenAtReset = result }
        }
        #expect(seenAtReset == applied.count, "an old-gen action applied after the reset")
    }
}

@Test func applyIfCurrentReportsCurrentGenWhenStale() async {
    let g = Generation(initial: 5)
    let ran = Recorder()
    let r = await g.applyIfCurrent(4) { ran.record(ActionSpec.probe(seq: 1)); return 1 }
    guard case .stale(let current) = r else {
        Issue.record("expected stale")
        return
    }
    #expect(current == 5)
    #expect(ran.count == 0)
    let ok = await g.applyIfCurrent(5) { 42 }
    guard case .applied(let v) = ok else {
        Issue.record("expected applied")
        return
    }
    #expect(v == 42)
}
