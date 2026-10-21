import Testing
@testable import EnvdCore

private func entry(_ ts: Double, _ seq: Int, kind: String = "action") -> ActionLogEntry {
    ActionLogEntry(ts: ts, kind: kind, gen: 1, seq: seq, type: "click", status: "ok", latency_ms: 2)
}

@Test func actionLogSince() async {
    let l = ActionLog()
    await l.append(.init(ts: 1, kind: "action", gen: 1, seq: 1, type: "click", status: "ok", latency_ms: 2))
    await l.append(.init(ts: 5, kind: "action", gen: 1, seq: 2, type: "type", status: "ok", latency_ms: 3))
    #expect((await l.since(2)).map(\.seq) == [2])
    #expect((await l.since(0)).map(\.seq) == [1, 2])
    #expect((await l.since(9)).isEmpty)
}

@Test func actionLogKeepsOnlyTheNewestEntries() async {
    let l = ActionLog(capacity: 3)
    for i in 1...5 { await l.append(entry(Double(i), i)) }
    #expect((await l.since(0)).map(\.seq) == [3, 4, 5])
}

@Test func ndjsonHasOneObjectPerLineWithWireNames() {
    let out = ActionLog.ndjson([entry(1.5, 1), entry(2, 2, kind: "screenshot")])
    let lines = bodyText(HTTPResponse(status: 200, body: out)).split(separator: "\n")
    #expect(lines.count == 2)
    #expect(lines[0] == #"{"gen":1,"kind":"action","latency_ms":2,"seq":1,"status":"ok","ts":1.5,"type":"click"}"#)
    #expect(lines[1].contains(#""kind":"screenshot""#))
    #expect(ActionLog.ndjson([]).isEmpty)
}
