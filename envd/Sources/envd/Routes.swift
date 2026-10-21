import AppKit
import ApplicationServices
import CoreGraphics
import EnvdCore
import Foundation

// All routes live here. Later tasks add more cases to `handle`.
enum Routes {
    /// The environment number. It is set once in main before the server starts
    /// and is never changed after that, so reading it from any thread is safe.
    nonisolated(unsafe) static var env: Int = 0
    /// The port envd listens on. Set once in main before the server starts. The
    /// Host header of every request must name this port.
    nonisolated(unsafe) static var port: UInt16 = 0

    static let generation = Generation(initial: 0)
    static let screenshotRecord = ScreenshotRecord()
    static let actionLog = ActionLog()

    /// Screenshots use one pixel per screen point, so action positions need no
    /// scaling.
    static let actionScale: CGFloat = 1

    static let actions = ActionHandler(generation: generation, log: actionLog) { spec in
        try Input.perform(spec, screenScale: actionScale)
    }

    /// Every path that exists. A known path with the wrong method gets 405.
    static let knownPaths: Set<String> = [
        "/health", "/gen", "/screenshot", "/action", "/log",
        "/pb", "/launch", "/kill", "/windows", "/axtext",
    ]

    static func handle(_ req: HTTPRequest) async -> HTTPResponse {
        // The local-only guard runs first, for every route including GET and
        // unknown paths. A web page in Chrome on this machine could otherwise
        // read screenshots or state from here (DNS rebinding). Doing it once
        // here means a new route cannot forget it.
        if let denied = LocalGuard.check(req, port: port) { return denied }

        switch (req.method, req.path) {
        case ("GET", "/health"):
            return await health()
        case ("POST", "/gen"):
            return await setGeneration(req)
        case ("GET", "/screenshot"):
            return await screenshot(req)
        case ("POST", "/action"):
            return await actions.handle(req, port: port)
        case ("GET", "/log"):
            return await actions.log(req)
        case ("GET", "/pb"):
            return pasteboardGet()
        case ("POST", "/pb"):
            return await pasteboardSet(req)
        case ("DELETE", "/pb"):
            return await pasteboardClear(req)
        case ("POST", "/launch"):
            return await launch(req)
        case ("POST", "/kill"):
            return await kill(req)
        case ("GET", "/windows"):
            return windows()
        case ("GET", "/axtext"):
            return axText(req)
        case (_, let path) where knownPaths.contains(path):
            return error(405, "method_not_allowed")
        default:
            return error(404, "not_found")
        }
    }

    // MARK: - Routes

    static func health() async -> HTTPResponse {
        let gen = await generation.current()
        let screen = await mainScreen()
        // Both permission checks only look; neither one shows a prompt.
        let body = Health.payload(
            env: env, uid: Int(getuid()), gen: gen, onConsole: onConsole(),
            screenW: screen.w, screenH: screen.h, scale: screen.scale,
            version: Version.string,
            axTrusted: AXIsProcessTrusted(),
            screenRecording: CGPreflightScreenCaptureAccess())
        return jsonResponse(body)
    }

    static func setGeneration(_ req: HTTPRequest) async -> HTTPResponse {
        if let denied = LocalGuard.check(req, port: port) { return denied }
        guard let g = intField("gen", in: req.body) else {
            return error(400, "bad_request", detail: "body must be JSON with an integer gen")
        }
        await generation.set(g)
        return jsonResponse(["gen": g])
    }

    /// GET /screenshot, with an optional `region=x,y,w,h`. Returns a PNG. The
    /// headers say which gen it was taken in, when, whether the picture is the
    /// same as the last one for this region, and whether it is all black.
    static func screenshot(_ req: HTTPRequest) async -> HTTPResponse {
        var region: CGRect?
        if let raw = req.query["region"] {
            guard let parsed = FrameHash.parseRegion(raw) else {
                return error(400, "bad_request",
                             detail: "region must be x,y,w,h: finite numbers, x and y at least 0, w and h above 0")
            }
            region = parsed
        }
        let key = region.map { "\($0.origin.x),\($0.origin.y),\($0.width),\($0.height)" } ?? "full"

        let gen = await generation.current()
        let started = Date()
        let shot: (png: Data, rgba: Data, w: Int, h: Int)
        do {
            shot = try await Screenshot.capture(region: region)
        } catch {
            await actionLog.append(ActionLogEntry(
                ts: Date().timeIntervalSince1970, kind: "screenshot", gen: gen, seq: 0,
                type: "screenshot", status: "error", latency_ms: Int(Date().timeIntervalSince(started) * 1000)))
            return Self.error(500, "capture_failed", detail: "\(error)")
        }
        let capturedAt = Date()
        let elapsedMs = Int(capturedAt.timeIntervalSince(started) * 1000)

        // The capture is already cut to the region, so hash all of it.
        let hash = FrameHash.hash(rgba: shot.rgba, width: shot.w, height: shot.h, region: nil)
        let stale = await screenshotRecord.noteHash(hash, for: key)
        let black = FrameHash.isBlack(rgba: shot.rgba, width: shot.w, height: shot.h)
        await actionLog.append(ActionLogEntry(
            ts: capturedAt.timeIntervalSince1970, kind: "screenshot", gen: gen, seq: 0,
            type: "screenshot", status: black ? "black" : (stale ? "stale" : "ok"), latency_ms: elapsedMs))

        let stamp = ISO8601DateFormatter()
        stamp.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return HTTPResponse(status: 200, headers: [
            "Content-Type": "image/png",
            "X-Drums-Gen": String(gen),
            "X-Drums-Captured-At": stamp.string(from: capturedAt),
            "X-Drums-Stale": stale ? "true" : "false",
            "X-Drums-Black": black ? "true" : "false",
        ], body: shot.png)
    }

    // MARK: - Pasteboard

    static func pasteboardGet() -> HTTPResponse {
        let snap = Pasteboard.read()
        return jsonResponse(["text": snap.text ?? NSNull(), "hash": snap.hash, "changecount": snap.changeCount])
    }

    /// POST /pb {gen, text}
    static func pasteboardSet(_ req: HTTPRequest) async -> HTTPResponse {
        guard let gen = RequestGen.supplied(req) else { return error(400, "bad_request", detail: "gen is required") }
        guard let obj = try? JSONSerialization.jsonObject(with: req.body) as? [String: Any],
              let text = obj["text"] as? String else {
            return error(400, "bad_request", detail: "body must be JSON with a string text")
        }
        switch await generation.applyIfCurrent(gen, { Pasteboard.set(text) }) {
        case .stale(let current): return HTTPResponse.staleGen(current: current)
        case .applied(let snap): return jsonResponse(["hash": snap.hash, "changecount": snap.changeCount])
        }
    }

    /// DELETE /pb?gen=
    static func pasteboardClear(_ req: HTTPRequest) async -> HTTPResponse {
        guard let gen = RequestGen.supplied(req) else { return error(400, "bad_request", detail: "gen is required") }
        switch await generation.applyIfCurrent(gen, { Pasteboard.clear() }) {
        case .stale(let current): return HTTPResponse.staleGen(current: current)
        case .applied: return jsonResponse(["hash": PasteboardHash.empty])
        }
    }

    // MARK: - Processes, windows, accessibility text

    /// POST /launch {gen, path, args, env}
    static func launch(_ req: HTTPRequest) async -> HTTPResponse {
        let spec: LaunchSpec
        do { spec = try LaunchSpec.decode(req.body) } catch {
            return Self.error(400, "bad_request", detail: "\(error)")
        }
        switch await generation.applyIfCurrent(spec.gen, { Processes.launch(spec) }) {
        case .stale(let current):
            return HTTPResponse.staleGen(current: current)
        case .applied(.started(let pid)):
            return jsonResponse(["pid": Int(pid)])
        case .applied(.failed(let why)):
            return error(500, "launch_failed", detail: why)
        }
    }

    /// POST /kill {gen, pid} or {gen, all:true}. SIGTERM goes out inside the
    /// generation gate. The wait and the SIGKILL come after, outside it, so a
    /// slow process never holds up a reset.
    static func kill(_ req: HTTPRequest) async -> HTTPResponse {
        let spec: KillSpec
        do { spec = try KillSpec.decode(req.body) } catch {
            return Self.error(400, "bad_request", detail: "\(error)")
        }
        switch await generation.applyIfCurrent(spec.gen, { Processes.terminate(spec) }) {
        case .stale(let current):
            return HTTPResponse.staleGen(current: current)
        case .applied(.refused(let why)):
            return error(403, "protected_process", detail: why)
        case .applied(.signalled(let targets)):
            await Processes.escalate(targets)
            return jsonResponse(["killed": targets.map { Int($0.pid) }])
        }
    }

    /// GET /windows
    static func windows() -> HTTPResponse {
        let body = (try? JSONSerialization.data(withJSONObject: Windows.json(Desktop.windows()), options: [.sortedKeys]))
            ?? Data("[]".utf8)
        return HTTPResponse(status: 200, headers: ["Content-Type": "application/json"], body: body)
    }

    /// GET /axtext?pid=
    static func axText(_ req: HTTPRequest) -> HTTPResponse {
        guard let raw = req.query["pid"], let pid = Int32(raw), pid > 0 else {
            return error(400, "bad_request", detail: "pid must be a process id")
        }
        switch Desktop.focusedText(pid: pid) {
        case .text(let t): return jsonResponse(["text": t])
        case .unavailable(let reason): return jsonResponse(["text": NSNull(), "reason": reason])
        }
    }

    // MARK: - Generation guard

    /// A check-only guard. It returns nil when the request's gen is current.
    /// Otherwise it returns the response to send and the route must do nothing.
    /// The gen is read from the `gen` query parameter, or from the `gen` field
    /// of a JSON body.
    ///
    /// Warning: this is check-then-act. A reset can land after the check and
    /// before the route's effect. A route whose effect must never happen for a
    /// stale gen (like POST /action) must use `Generation.applyIfCurrent`
    /// instead, which does both in one step.
    static func requireGen(_ req: HTTPRequest) async -> HTTPResponse? {
        var supplied: Int?
        if let q = req.query["gen"] { supplied = Int(q) }
        if supplied == nil { supplied = intField("gen", in: req.body) }
        guard let supplied else {
            return error(400, "bad_request", detail: "gen is required")
        }
        if await generation.check(supplied) { return nil }
        let current = await generation.current()
        return HTTPResponse.staleGen(current: current)
    }

    // MARK: - Helpers

    static func error(_ status: Int, _ code: String, detail: String? = nil) -> HTTPResponse {
        var body: [String: Any] = ["error": code]
        if let detail { body["detail"] = detail }
        return jsonResponse(body, status: status)
    }

    static func jsonResponse(_ object: [String: Any], status: Int = 200) -> HTTPResponse {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return HTTPResponse(status: status, headers: ["Content-Type": "application/json"], body: data)
    }

    static func intField(_ name: String, in body: Data) -> Int? {
        guard let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else { return nil }
        if let n = obj[name] as? Int { return n }
        return nil
    }

    /// True when this login session owns the physical console (screen).
    static func onConsole() -> Bool {
        guard let dict = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
        return (dict["kCGSSessionOnConsoleKey"] as? Bool) ?? false
    }

    struct ScreenInfo: Sendable {
        let w: Int
        let h: Int
        let scale: Double
    }

    @MainActor
    static func mainScreenOnMain() -> ScreenInfo {
        if let screen = NSScreen.main {
            return ScreenInfo(w: Int(screen.frame.width), h: Int(screen.frame.height),
                              scale: Double(screen.backingScaleFactor))
        }
        // No screen object (headless or no window server). Try the display
        // list; if that is empty too, report zeros so the caller can tell.
        let bounds = CGDisplayBounds(CGMainDisplayID())
        return ScreenInfo(w: Int(bounds.width), h: Int(bounds.height), scale: 1.0)
    }

    static func mainScreen() async -> ScreenInfo {
        await mainScreenOnMain()
    }
}
