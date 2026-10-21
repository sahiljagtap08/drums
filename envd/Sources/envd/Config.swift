import Foundation

// Command line settings: envd --env NN --port P [--lock PATH]
struct Config {
    let env: Int
    let port: UInt16
    let lockPath: String

    static let usage = "usage: envd --env NN --port P [--lock PATH]"

    static var defaultLockPath: String {
        NSHomeDirectory() + "/.drums/envd.lock"
    }

    enum ParseError: Error, CustomStringConvertible {
        case message(String)
        var description: String {
            switch self { case .message(let m): return m }
        }
    }

    /// `args` is the argument list without the program name.
    static func parse(_ args: [String]) throws -> Config {
        var env: Int?
        var port: UInt16?
        var lock = defaultLockPath
        var i = 0
        func value(for flag: String) throws -> String {
            i += 1
            guard i < args.count else { throw ParseError.message("missing value for \(flag)") }
            return args[i]
        }
        while i < args.count {
            let flag = args[i]
            switch flag {
            case "--env":
                let v = try value(for: flag)
                guard let n = Int(v), (0...99).contains(n) else {
                    throw ParseError.message("--env must be a number from 0 to 99")
                }
                env = n
            case "--port":
                let v = try value(for: flag)
                guard let p = UInt16(v), p > 0 else {
                    throw ParseError.message("--port must be a number from 1 to 65535")
                }
                port = p
            case "--lock":
                lock = try value(for: flag)
            default:
                throw ParseError.message("unknown argument \(flag)")
            }
            i += 1
        }
        guard let env else { throw ParseError.message("--env is required") }
        guard let port else { throw ParseError.message("--port is required") }
        return Config(env: env, port: port, lockPath: lock)
    }
}
