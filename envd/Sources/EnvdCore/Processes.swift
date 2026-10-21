import Foundation

// The pure parts of launching and killing processes: request decoding, the
// list of processes that must never be killed, and the filter that picks what
// "kill all" is allowed to touch. The parts that talk to the system live in
// the envd target.

public struct SpecError: Error, CustomStringConvertible, Equatable {
    public let description: String
    public init(_ description: String) { self.description = description }
}

/// One process, as read from the system.
public struct ProcInfo: Equatable, Sendable {
    public let pid: Int32
    public let name: String
    public let uid: UInt32

    public init(pid: Int32, name: String, uid: UInt32) {
        self.pid = pid
        self.name = name
        self.uid = uid
    }
}

/// POST /launch body.
public struct LaunchSpec: Equatable, Sendable {
    public let gen: Int
    public let path: String
    public let args: [String]
    public let env: [String: String]

    public static func decode(_ body: Data) throws -> LaunchSpec {
        guard let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            throw SpecError("body must be a JSON object")
        }
        guard let gen = obj["gen"] as? Int else { throw SpecError("gen is required and must be an integer") }
        guard let path = obj["path"] as? String, path.hasPrefix("/") else {
            throw SpecError("path is required and must be an absolute path")
        }
        var args: [String] = []
        if let raw = obj["args"], !(raw is NSNull) {
            guard let list = raw as? [String] else { throw SpecError("args must be a list of strings") }
            args = list
        }
        var env: [String: String] = [:]
        if let raw = obj["env"], !(raw is NSNull) {
            guard let map = raw as? [String: String] else { throw SpecError("env must be an object of strings") }
            env = map
        }
        return LaunchSpec(gen: gen, path: path, args: args, env: env)
    }

    /// The environment for the child: the current one with `env` on top.
    public func mergedEnvironment(over current: [String: String]) -> [String: String] {
        current.merging(env) { _, mine in mine }
    }
}

/// POST /kill body: either one pid or all:true, never both.
public struct KillSpec: Equatable, Sendable {
    public let gen: Int
    public let pid: Int32?
    public let all: Bool

    public static func decode(_ body: Data) throws -> KillSpec {
        guard let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            throw SpecError("body must be a JSON object")
        }
        guard let gen = obj["gen"] as? Int else { throw SpecError("gen is required and must be an integer") }
        var all = false
        if let raw = obj["all"], !(raw is NSNull) {
            guard let n = raw as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else {
                throw SpecError("all must be true or false")
            }
            all = n.boolValue
        }
        var pid: Int32?
        if let raw = obj["pid"], !(raw is NSNull) {
            guard let n = raw as? Int, n > 1, n <= Int(Int32.max) else {
                throw SpecError("pid must be an integer above 1")
            }
            pid = Int32(n)
        }
        if all && pid != nil { throw SpecError("send pid or all, not both") }
        if !all && pid == nil { throw SpecError("send pid, or all:true") }
        return KillSpec(gen: gen, pid: pid, all: all)
    }
}

public enum Processes {
    /// Names that no kill touches. The first group is the list from the plan.
    /// The second is other desktop session helpers owned by the same user
    /// (launchd for the user, the pasteboard daemon and so on).
    public static let systemAllowlist: Set<String> = [
        // From the plan.
        "envd", "loginwindow", "Dock", "Finder", "SystemUIServer", "cfprefsd", "WindowServer",
        // Session and window server helpers.
        "launchd", "pboard", "distnoted", "WindowManager", "ControlCenter", "NotificationCenter",
        "universalaccessd", "ViewBridgeAuxiliary", "usernoted", "lsd",
        // Remote access that runs as the user: Screen Sharing / VNC, and the ssh agent.
        "ScreensharingAgent", "ssh-agent",
    ]

    /// Extra names that "kill all" keeps alive, but a kill of one named pid
    /// may still hit. They are what keeps a remote machine reachable: ssh and
    /// the shells and tmux it runs in, caffeinate (keeps the machine awake)
    /// and Tailscale (the network path to it).
    public static let keepAliveNames: Set<String> = [
        "sshd", "sshd-session", "ssh", "login", "zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh",
        "tmux", "caffeinate", "Tailscale",
    ]

    /// `pid` and every parent above it, up to pid 1. `parentOf` gives a
    /// process's parent, or nil when it cannot be read. The walk stops at pid
    /// 0 or 1, at a repeat (a loop), or when the parent is unknown. Used so
    /// kill-all never signals the terminal, shell, make or harness that
    /// started envd, however deep that chain is.
    public static func ancestorChain(of pid: Int32, parentOf: (Int32) -> Int32?) -> Set<Int32> {
        var chain: Set<Int32> = [pid]
        var current = pid
        while current > 1, let parent = parentOf(current), parent > 0, !chain.contains(parent) {
            chain.insert(parent)
            current = parent
        }
        return chain
    }

    /// The pids "kill all" may signal: owned by `uid`, not on either name list,
    /// and not in `protectedPids` (envd itself and its parent). Pids 0 and 1
    /// are never returned.
    public static func killTargets(_ procs: [ProcInfo], uid: UInt32, protectedPids: Set<Int32>) -> [Int32] {
        procs.filter {
            allowed($0, uid: uid, protectedPids: protectedPids) && !keepAliveNames.contains($0.name)
        }.map(\.pid)
    }

    /// What may be done about one process, for POST /kill with a pid.
    public enum Verdict: Equatable, Sendable {
        case ok
        case notFound
        case notOurs
        case protected
    }

    public static func verdict(for proc: ProcInfo?, uid: UInt32, protectedPids: Set<Int32>) -> Verdict {
        guard let proc else { return .notFound }
        if proc.uid != uid { return .notOurs }
        return allowed(proc, uid: uid, protectedPids: protectedPids) ? .ok : .protected
    }

    private static func allowed(_ p: ProcInfo, uid: UInt32, protectedPids: Set<Int32>) -> Bool {
        p.pid > 1 && p.uid == uid && !protectedPids.contains(p.pid) && !systemAllowlist.contains(p.name)
    }
}
