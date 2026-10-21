import Foundation

// The body of GET /health. `ax_trusted` and `screen_recording` let a harness
// see missing permissions before a run: without Accessibility macOS silently
// drops every posted input event, and without Screen Recording screenshots are
// black or fail.
public enum Health {
    public static func payload(env: Int, uid: Int, gen: Int, onConsole: Bool,
                               screenW: Int, screenH: Int, scale: Double,
                               version: String, axTrusted: Bool, screenRecording: Bool) -> [String: Any] {
        [
            "env": env,
            "uid": uid,
            "gen": gen,
            "on_console": onConsole,
            "screen": ["w": screenW, "h": screenH, "scale": scale],
            "version": version,
            "ax_trusted": axTrusted,
            "screen_recording": screenRecording,
        ]
    }
}
