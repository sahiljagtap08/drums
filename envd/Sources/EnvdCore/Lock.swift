import Foundation

public enum LockError: Error, Equatable {
    /// Another process already holds the lock.
    case held
    /// The lock file could not be opened. The value is errno.
    case openFailed(Int32)
}

// A lock file that keeps a second envd from running. The lock lives as long as
// the file descriptor is open, and the OS drops it if the process dies.
public final class LockFile: @unchecked Sendable {
    private let fd: Int32
    public let path: String

    private init(fd: Int32, path: String) {
        self.fd = fd
        self.path = path
    }

    public static func acquire(path: String) throws -> LockFile {
        let dir = (path as NSString).deletingLastPathComponent
        if !dir.isEmpty {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        }
        let fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0o600)
        if fd < 0 { throw LockError.openFailed(errno) }
        if flock(fd, LOCK_EX | LOCK_NB) != 0 {
            let err = errno
            close(fd)
            if err == EWOULDBLOCK { throw LockError.held }
            throw LockError.openFailed(err)
        }
        // Write our pid for people who look at the file. This is best effort.
        _ = ftruncate(fd, 0)
        let pid = Data("\(getpid())\n".utf8)
        _ = pid.withUnsafeBytes { write(fd, $0.baseAddress, pid.count) }
        return LockFile(fd: fd, path: path)
    }

    deinit {
        flock(fd, LOCK_UN)
        close(fd)
    }
}
