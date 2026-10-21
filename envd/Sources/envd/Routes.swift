import AppKit
import CoreGraphics
import EnvdCore
import Foundation

// All routes live here. Later tasks add more cases to `handle`.
enum Routes {
    /// The environment number. It is set once in main before the server starts
    /// and is never changed after that, so reading it from any thread is safe.
    nonisolated(unsafe) static var env: Int = 0

    static let generation = Generation(initial: 0)
    static let screenshotRecord = ScreenshotRecord()

    static func handle(_ req: HTTPRequest) async -> HTTPResponse {
        switch (req.method, req.path) {
        case ("GET", "/health"):
            return await health()
        case ("POST", "/gen"):
            return await setGeneration(req)
        case ("GET", "/screenshot"):
            return await screenshot(req)
        case (_, "/health"), (_, "/gen"), (_, "/screenshot"):
            return error(405, "method_not_allowed")
        default:
            return error(404, "not_found")
        }
    }

    // MARK: - Routes

    static func health() async -> HTTPResponse {
        let gen = await generation.current()
        let screen = await mainScreen()
        let body: [String: Any] = [
            "env": env,
            "uid": Int(getuid()),
            "gen": gen,
            "on_console": onConsole(),
            "screen": ["w": screen.w, "h": screen.h, "scale": screen.scale],
            "version": Version.string,
        ]
        return jsonResponse(body)
    }

    static func setGeneration(_ req: HTTPRequest) async -> HTTPResponse {
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
            let parts = raw.split(separator: ",", omittingEmptySubsequences: false).map { Double($0) }
            guard parts.count == 4, let x = parts[0], let y = parts[1], let w = parts[2], let h = parts[3],
                  x >= 0, y >= 0, w > 0, h > 0 else {
                return error(400, "bad_request", detail: "region must be x,y,w,h with w and h above 0")
            }
            region = CGRect(x: x, y: y, width: w, height: h)
        }
        let key = region.map { "\($0.origin.x),\($0.origin.y),\($0.width),\($0.height)" } ?? "full"

        let gen = await generation.current()
        let started = Date()
        let shot: (png: Data, rgba: Data, w: Int, h: Int)
        do {
            shot = try await Screenshot.capture(region: region)
        } catch {
            return Self.error(500, "capture_failed", detail: "\(error)")
        }
        let elapsedMs = Int(Date().timeIntervalSince(started) * 1000)
        await screenshotRecord.noteLatency(ms: elapsedMs)

        // The capture is already cut to the region, so hash all of it.
        let hash = FrameHash.hash(rgba: shot.rgba, width: shot.w, height: shot.h, region: nil)
        let stale = await screenshotRecord.noteHash(hash, for: key)
        let black = FrameHash.isBlack(rgba: shot.rgba, width: shot.w, height: shot.h)

        let stamp = ISO8601DateFormatter()
        stamp.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return HTTPResponse(status: 200, headers: [
            "Content-Type": "image/png",
            "X-Drums-Gen": String(gen),
            "X-Drums-Captured-At": stamp.string(from: started),
            "X-Drums-Stale": stale ? "true" : "false",
            "X-Drums-Black": black ? "true" : "false",
        ], body: shot.png)
    }

    // MARK: - Generation guard

    /// Call this first in every mutating route. It returns nil when the
    /// request's gen is current. Otherwise it returns the response to send and
    /// the route must do nothing. The gen is read from the `gen` query
    /// parameter, or from the `gen` field of a JSON body.
    static func requireGen(_ req: HTTPRequest) async -> HTTPResponse? {
        var supplied: Int?
        if let q = req.query["gen"] { supplied = Int(q) }
        if supplied == nil { supplied = intField("gen", in: req.body) }
        guard let supplied else {
            return error(400, "bad_request", detail: "gen is required")
        }
        if await generation.check(supplied) { return nil }
        let current = await generation.current()
        return HTTPResponse.json("{\"error\":\"stale_gen\",\"current_gen\":\(current)}", status: 409)
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
