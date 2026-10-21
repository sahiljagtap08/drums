import EnvdCore
import Foundation

func fail(_ message: String, code: Int32) -> Never {
    FileHandle.standardError.write(Data("envd: \(message)\n".utf8))
    exit(code)
}

let config: Config
do {
    config = try Config.parse(Array(CommandLine.arguments.dropFirst()))
} catch {
    fail("\(error)\n\(Config.usage)", code: 2)
}

// Keep the lock for the whole life of the process.
let lock: LockFile
do {
    lock = try LockFile.acquire(path: config.lockPath)
} catch LockError.held {
    fail("another envd already holds \(config.lockPath)", code: 3)
} catch {
    fail("cannot open lock file \(config.lockPath): \(error)", code: 4)
}

Routes.env = config.env
Routes.port = config.port

let server: Server
do {
    server = try Server(port: config.port)
} catch {
    fail("cannot create server: \(error)", code: 1)
}
server.start(
    onReady: { print("envd \(Version.string) env=\(config.env) listening on 127.0.0.1:\(config.port)"); fflush(stdout) },
    onFail: { fail("listener failed: \($0)", code: 1) })

Poller.start(log: Routes.actionLog, generation: Routes.generation)

withExtendedLifetime(lock) {
    dispatchMain()
}
