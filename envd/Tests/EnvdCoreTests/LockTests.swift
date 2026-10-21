import Testing
@testable import EnvdCore

@Test func secondInstanceExitsOnLock() throws {
    let p = tempLockPath("envd-test.lock")
    let a = try LockFile.acquire(path: p)
    #expect(throws: (any Error).self) { try LockFile.acquire(path: p) }
    _ = a
}

@Test func lockIsReleasedWhenDropped() throws {
    let p = tempLockPath("envd-test-release.lock")
    var a: LockFile? = try LockFile.acquire(path: p)
    #expect(throws: LockError.held) { try LockFile.acquire(path: p) }
    a = nil
    _ = try LockFile.acquire(path: p)
    _ = a
}
