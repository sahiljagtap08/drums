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

// The deterministic gate for atomicity. The work closure blocks for 50 ms. The
// test waits until the work has started, then calls set(2). Because the apply
// is in flight, set(2) must wait for it. If check and apply could be split
// apart, set(2) would return while the work was still running, and the
// finish time would be later than the time set returned.
@Test func resetCannotInterleaveWithAnInFlightApply() async {
    let g = Generation(initial: 1)
    let work = SlowWork()

    let inFlight = Task { await g.applyIfCurrent(1) { work.run(blockMs: 50) } }
    while !work.started { await Task.yield() }

    await g.set(2)
    let setReturnedAt = uptimeNow()

    let finishedAt = work.finishedAt
    #expect(finishedAt != nil, "set() returned while the apply was still running")
    if let finishedAt {
        #expect(finishedAt <= setReturnedAt, "set() returned before the in-flight apply finished")
    }

    guard case .applied = await inFlight.value else {
        Issue.record("the in-flight apply should have run, it started before the reset")
        return
    }
    #expect(work.runCount == 1)

    // After the reset, the same old gen is refused and its work never runs.
    let later = SlowWork()
    let r = await g.applyIfCurrent(1) { later.run(blockMs: 0) }
    guard case .stale(let current) = r else {
        Issue.record("expected stale after the reset")
        return
    }
    #expect(current == 2)
    #expect(later.runCount == 0)
}

// The gap test. The first test above proves a reset cannot run during the
// work. This one proves a reset cannot run between the gen check and the work.
// Action A holds the actor for 300 ms. Action B (gen 1) is queued behind it,
// and 100 ms later the reset set(2) is queued behind B. So when A finishes,
// both B and the reset are waiting. If B's check and its work were separate
// steps (for example, an await between them), the reset would run in the gap
// and B's work would run after the reset. Here B must run fully first.
@Test func resetCannotSlipBetweenCheckAndWork() async {
    let g = Generation(initial: 1)
    let a = SlowWork()
    let b = SlowWork()

    let taskA = Task { await g.applyIfCurrent(1) { a.run(blockMs: 300) } }
    while !a.started { await Task.yield() }

    let taskB = Task { await g.applyIfCurrent(1) { b.run(blockMs: 10) } }
    try? await Task.sleep(for: .milliseconds(100))  // let B settle in the queue
    let taskC = Task { () -> Double in
        await g.set(2)
        return uptimeNow()
    }

    let setReturnedAt = await taskC.value
    _ = await taskA.value
    let resultB = await taskB.value

    guard case .applied = resultB else {
        Issue.record("B was queued before the reset, so it must have been applied")
        return
    }
    let finishedAt = b.finishedAt
    #expect(finishedAt != nil)
    if let finishedAt {
        #expect(finishedAt <= setReturnedAt, "B's work ran after the reset: check and work were split")
    }
}
