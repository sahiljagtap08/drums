import Foundation

// POST /action and GET /log, without any real input code. The input layer is
// passed in as `perform`, so tests can use a stand-in.
public struct ActionHandler: Sendable {
    public typealias Perform = @Sendable (ActionSpec) throws -> Void

    let generation: Generation
    let log: ActionLog
    let perform: Perform

    public init(generation: Generation, log: ActionLog, perform: @escaping Perform) {
        self.generation = generation
        self.log = log
        self.perform = perform
    }

    /// What happened inside the generation gate.
    private struct Outcome: Sendable {
        var appliedAt: Double
        var latencyMs: Int
        var failure: String?
    }

    /// The steps, in order: local-only check (403), decode (400), then the
    /// generation gate. The gate checks the generation and performs the action
    /// in one step (see `Generation.applyIfCurrent`), so a reset cannot slip in
    /// between. A stale action gets 409 and nothing is done or logged.
    public func handle(_ req: HTTPRequest, port: UInt16) async -> HTTPResponse {
        if let denied = LocalGuard.check(req, port: port) { return denied }

        let spec: ActionSpec
        do {
            spec = try ActionSpec.decode(req.body)
        } catch {
            return Self.errorJSON(400, "bad_request", detail: "\(error)")
        }

        let perform = self.perform
        let gated = await generation.applyIfCurrent(spec.gen) { () -> Outcome in
            let start = Date()
            var failure: String?
            do { try perform(spec) } catch { failure = "\(error)" }
            let end = Date()
            return Outcome(appliedAt: end.timeIntervalSince1970,
                           latencyMs: Int(end.timeIntervalSince(start) * 1000),
                           failure: failure)
        }

        switch gated {
        case .stale(let current):
            return HTTPResponse.staleGen(current: current)
        case .applied(let outcome):
            await log.append(ActionLogEntry(
                ts: outcome.appliedAt, kind: "action", gen: spec.gen, seq: spec.seq,
                type: spec.type, status: outcome.failure == nil ? "ok" : "error",
                latency_ms: outcome.latencyMs))
            if let failure = outcome.failure {
                return Self.errorJSON(500, "action_failed", detail: failure)
            }
            return HTTPResponse.json("{\"applied_at\":\(outcome.appliedAt),\"seq\":\(spec.seq)}")
        }
    }

    /// GET /log?since=<seconds>. The body is ndjson, oldest entry first.
    public func log(_ req: HTTPRequest) async -> HTTPResponse {
        var since = 0.0
        if let raw = req.query["since"] {
            guard let v = Double(raw), v.isFinite else {
                return Self.errorJSON(400, "bad_request", detail: "since must be a number of seconds")
            }
            since = v
        }
        let entries = await log.since(since)
        return HTTPResponse(status: 200, headers: ["Content-Type": "application/x-ndjson"],
                            body: ActionLog.ndjson(entries))
    }

    static func errorJSON(_ status: Int, _ code: String, detail: String) -> HTTPResponse {
        let body: [String: Any] = ["error": code, "detail": detail]
        let data = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])) ?? Data("{}".utf8)
        return HTTPResponse(status: status, headers: ["Content-Type": "application/json"], body: data)
    }
}

extension HTTPResponse {
    /// The 409 sent for a request made for an old generation.
    public static func staleGen(current: Int) -> HTTPResponse {
        HTTPResponse.json("{\"error\":\"stale_gen\",\"current_gen\":\(current)}", status: 409)
    }
}
