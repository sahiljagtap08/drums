import AppKit
import EnvdCore
import Foundation

// Every 2 seconds, writes a sample of the pasteboard hash and the front app
// to the action log. The harness reads these through GET /log to see what
// the clipboard and the front app were doing during a run.
enum Poller {
    static let intervalSeconds: UInt64 = 2

    static func start(log: ActionLog, generation: Generation) {
        Task.detached {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: intervalSeconds * 1_000_000_000)
                let hash = Pasteboard.read().hash
                let app = NSWorkspace.shared.frontmostApplication
                let front = app?.bundleIdentifier ?? app?.localizedName
                let gen = await generation.current()
                await log.append(.pasteboardSample(ts: Date().timeIntervalSince1970, gen: gen,
                                                   hash: hash, frontmost: front))
            }
        }
    }
}
