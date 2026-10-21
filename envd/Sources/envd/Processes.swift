import Darwin
import EnvdCore
import Foundation

// Starting and stopping processes. The rules about what may be killed are in
// EnvdCore (`Processes.systemAllowlist` and friends). This file does the
// system calls.
extension Processes {
    // MARK: - Launch

    /// The children envd started, so they stay alive in memory until they
    /// exit. Foundation reaps a child when it ends.
    private final class Children: @unchecked Sendable {
        private let lock = NSLock()
        private var running: [Int32: Process] = [:]

        func add(_ p: Process) {
            lock.lock(); defer { lock.unlock() }
            running[p.processIdentifier] = p
        }

        func remove(_ pid: Int32) {
            lock.lock(); defer { lock.unlock() }
            running.removeValue(forKey: pid)
        }
    }

    private static let children = Children()

    enum LaunchResult: Sendable {
        case started(pid: Int32)
        case failed(String)
    }

    /// Starts the program. Nothing is waited for: the child runs on its own.
    /// Its output goes nowhere and it has no input. Call this inside the
    /// generation gate; it does not block.
    static func launch(_ spec: LaunchSpec) -> LaunchResult {
        guard FileManager.default.isExecutableFile(atPath: spec.path) else {
            return .failed("not an executable file: \(spec.path)")
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: spec.path)
        p.arguments = spec.args
        p.environment = spec.mergedEnvironment(over: ProcessInfo.processInfo.environment)
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { done in children.remove(done.processIdentifier) }
        do {
            try p.run()
        } catch {
            return .failed("\(error)")
        }
        children.add(p)
        return .started(pid: p.processIdentifier)
    }

    // MARK: - Reading the process table

    /// One process, or nil if it does not exist. A zombie (finished but not
    /// yet cleaned up) counts as gone.
    static func info(pid: Int32) -> ProcInfo? {
        var bsd = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &bsd, size) == size else { return nil }
        if bsd.pbi_status == UInt32(SZOMB) { return nil }
        var buf = [CChar](repeating: 0, count: 256)
        let n = proc_name(pid, &buf, UInt32(buf.count))
        let name = n > 0
            ? text(of: buf)
            : withUnsafeBytes(of: bsd.pbi_comm) { raw in text(of: Array(raw.bindMemory(to: CChar.self))) }
        return ProcInfo(pid: pid, name: name, uid: bsd.pbi_uid)
    }

    /// The text of a C char buffer, up to the first zero byte.
    private static func text(of buf: [CChar]) -> String {
        String(decoding: buf.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    static func all() -> [ProcInfo] {
        let count = proc_listallpids(nil, 0)
        guard count > 0 else { return [] }
        var pids = [Int32](repeating: 0, count: Int(count) + 64)
        let got = proc_listallpids(&pids, Int32(pids.count * MemoryLayout<Int32>.size))
        guard got > 0 else { return [] }
        return pids.prefix(Int(got)).compactMap { $0 > 0 ? info(pid: $0) : nil }
    }

    /// envd itself and its parent (often the shell or tmux that started it).
    static var protectedPids: Set<Int32> {
        [getpid(), getppid()]
    }

    // MARK: - Kill

    enum KillResult: Sendable {
        /// SIGTERM was sent to these processes.
        case signalled([ProcInfo])
        case refused(String)
    }

    /// Sends SIGTERM. Call this inside the generation gate; it does not block.
    /// Follow it with `escalate` outside the gate.
    static func terminate(_ spec: KillSpec) -> KillResult {
        let uid = UInt32(getuid())
        if let pid = spec.pid {
            let target = info(pid: pid)
            switch verdict(for: target, uid: uid, protectedPids: protectedPids) {
            case .ok: break
            case .notFound: return .signalled([])
            case .notOurs: return .refused("process \(pid) belongs to another user")
            case .protected: return .refused("process \(pid) is protected")
            }
            kill(pid, SIGTERM)
            return .signalled([target!])
        }
        let procs = all()
        let wanted = Set(killTargets(procs, uid: uid, protectedPids: protectedPids))
        let targets = procs.filter { wanted.contains($0.pid) }
        for t in targets { kill(t.pid, SIGTERM) }
        return .signalled(targets)
    }

    /// Waits up to 2 seconds for the processes to exit, then sends SIGKILL to
    /// any that are still the same process. The identity check (same name and
    /// owner) keeps a recycled pid from being hit.
    static func escalate(_ targets: [ProcInfo]) async {
        let deadline = Date().addingTimeInterval(2)
        var pending = targets
        while !pending.isEmpty, Date() < deadline {
            pending = stillRunning(pending)
            if pending.isEmpty { return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        for t in stillRunning(pending) { kill(t.pid, SIGKILL) }
    }

    private static func stillRunning(_ list: [ProcInfo]) -> [ProcInfo] {
        list.filter { info(pid: $0.pid) == $0 }
    }
}
