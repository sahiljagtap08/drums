import Testing
@testable import EnvdCore

@Test func allowlistHasThePlanNames() {
    for name in ["envd", "loginwindow", "Dock", "Finder", "SystemUIServer", "cfprefsd", "WindowServer"] {
        #expect(Processes.systemAllowlist.contains(name), "\(name) must be protected")
    }
}

@Test func killAllKeepsAllowlistedOtherUsersAndProtectedPids() {
    let me: UInt32 = 501
    let procs = [
        ProcInfo(pid: 100, name: "TextEdit", uid: me),
        ProcInfo(pid: 101, name: "Google Chrome", uid: me),
        ProcInfo(pid: 102, name: "Dock", uid: me),
        ProcInfo(pid: 103, name: "Finder", uid: me),
        ProcInfo(pid: 104, name: "loginwindow", uid: me),
        ProcInfo(pid: 105, name: "envd", uid: me),
        ProcInfo(pid: 106, name: "cfprefsd", uid: me),
        ProcInfo(pid: 107, name: "SystemUIServer", uid: me),
        ProcInfo(pid: 200, name: "TextEdit", uid: 502),      // another user
        ProcInfo(pid: 300, name: "notenvd", uid: me),         // protected by pid below
        ProcInfo(pid: 400, name: "sshd-session", uid: me),    // keeps the machine reachable
        ProcInfo(pid: 401, name: "zsh", uid: me),
        ProcInfo(pid: 402, name: "Tailscale", uid: me),
        ProcInfo(pid: 1, name: "launchd", uid: me),
        ProcInfo(pid: 0, name: "kernel_task", uid: me),
    ]
    let targets = Processes.killTargets(procs, uid: me, protectedPids: [300])
    #expect(targets == [100, 101])
}

@Test func namedPidMayBeAShellButNotASystemProcess() {
    let me: UInt32 = 501
    // A shell the harness launched can be killed by its pid...
    #expect(Processes.verdict(for: ProcInfo(pid: 9, name: "sh", uid: me), uid: me, protectedPids: []) == .ok)
    // ...but "kill all" leaves shells and ssh alone.
    #expect(Processes.keepAliveNames.isSuperset(of: ["sshd", "zsh", "tmux", "caffeinate", "Tailscale"]))
}

@Test func killPidVerdicts() {
    let me: UInt32 = 501
    #expect(Processes.verdict(for: nil, uid: me, protectedPids: []) == .notFound)
    #expect(Processes.verdict(for: ProcInfo(pid: 9, name: "TextEdit", uid: 0), uid: me, protectedPids: []) == .notOurs)
    #expect(Processes.verdict(for: ProcInfo(pid: 9, name: "Dock", uid: me), uid: me, protectedPids: []) == .protected)
    #expect(Processes.verdict(for: ProcInfo(pid: 9, name: "x", uid: me), uid: me, protectedPids: [9]) == .protected)
    #expect(Processes.verdict(for: ProcInfo(pid: 9, name: "TextEdit", uid: me), uid: me, protectedPids: []) == .ok)
}

@Test func launchSpecDecodesAndMergesEnv() throws {
    let spec = try LaunchSpec.decode(bytes(#"{"gen":3,"path":"/bin/echo","args":["a","b"],"env":{"A":"new","B":"2"}}"#))
    #expect(spec.gen == 3)
    #expect(spec.path == "/bin/echo")
    #expect(spec.args == ["a", "b"])
    #expect(spec.mergedEnvironment(over: ["A": "old", "C": "3"]) == ["A": "new", "B": "2", "C": "3"])
    let bare = try LaunchSpec.decode(bytes(#"{"gen":1,"path":"/bin/echo"}"#))
    #expect(bare.args.isEmpty && bare.env.isEmpty)
}

@Test func launchSpecRejectsBadBodies() {
    #expect(throws: SpecError.self) { try LaunchSpec.decode(bytes("nope")) }
    #expect(throws: SpecError.self) { try LaunchSpec.decode(bytes(#"{"path":"/bin/echo"}"#)) }
    #expect(throws: SpecError.self) { try LaunchSpec.decode(bytes(#"{"gen":1,"path":"echo"}"#)) }
    #expect(throws: SpecError.self) { try LaunchSpec.decode(bytes(#"{"gen":1,"path":"/bin/echo","args":[1]}"#)) }
    #expect(throws: SpecError.self) { try LaunchSpec.decode(bytes(#"{"gen":1,"path":"/bin/echo","env":{"A":1}}"#)) }
}

@Test func killSpecNeedsExactlyOneTarget() throws {
    #expect(try KillSpec.decode(bytes(#"{"gen":1,"pid":42}"#)) == KillSpec(gen: 1, pid: 42, all: false))
    #expect(try KillSpec.decode(bytes(#"{"gen":1,"all":true}"#)) == KillSpec(gen: 1, pid: nil, all: true))
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1}"#)) }
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1,"all":false}"#)) }
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1,"pid":42,"all":true}"#)) }
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1,"all":1}"#)) }
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1,"pid":1}"#)) }
    #expect(throws: SpecError.self) { try KillSpec.decode(bytes(#"{"gen":1,"pid":-5}"#)) }
}
