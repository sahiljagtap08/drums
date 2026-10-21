import EnvdCore
import Foundation
import Network

// An HTTP server on 127.0.0.1. Each connection handles one request and is then
// closed.
final class Server: @unchecked Sendable {
    static let maxHeaderBytes = 64 * 1024
    static let maxRequestBytes = HTTPRequest.maxBodyBytes + maxHeaderBytes
    static let idleSeconds = 30.0

    private let listener: NWListener
    private let queue = DispatchQueue(label: "envd.server")

    init(port: UInt16) throws {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else {
            throw Config.ParseError.message("bad port \(port)")
        }
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: nwPort)
        params.allowLocalEndpointReuse = true
        listener = try NWListener(using: params)
    }

    /// Starts listening. `onReady` runs once the port is open. `onFail` runs if
    /// the listener cannot start (for example, the port is already in use).
    func start(onReady: @escaping @Sendable () -> Void, onFail: @escaping @Sendable (Error) -> Void) {
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready: onReady()
            case .failed(let error): onFail(error)
            default: break
            }
        }
        listener.newConnectionHandler = { [queue] conn in
            Connection(conn, queue: queue).run()
        }
        listener.start(queue: queue)
    }

    /// One client connection. All callbacks run on the server queue.
    private final class Connection: @unchecked Sendable {
        let conn: NWConnection
        let queue: DispatchQueue
        var buffer = Data()
        var finished = false

        init(_ conn: NWConnection, queue: DispatchQueue) {
            self.conn = conn
            self.queue = queue
        }

        func run() {
            conn.start(queue: queue)
            queue.asyncAfter(deadline: .now() + Server.idleSeconds) { [self] in
                if !finished { finish(with: Routes.error(400, "request_timeout")) }
            }
            receive()
        }

        func receive() {
            conn.receive(minimumIncompleteLength: 1, maximumLength: 256 * 1024) { [self] data, _, isComplete, error in
                if finished { return }
                if let data { buffer.append(data) }
                if error != nil { conn.cancel(); finished = true; return }

                switch HTTPRequest.parseResult(buffer) {
                case .complete(let req, _):
                    finished = true
                    Task {
                        let res = await Routes.handle(req)
                        self.send(res)
                    }
                case .malformed:
                    finish(with: Routes.error(400, "bad_request"))
                case .incomplete:
                    if buffer.count > Server.maxRequestBytes {
                        finish(with: Routes.error(413, "too_large"))
                    } else if buffer.count > Server.maxHeaderBytes, !hasHeaderEnd() {
                        finish(with: Routes.error(400, "headers_too_large"))
                    } else if isComplete {
                        conn.cancel()
                        finished = true
                    } else {
                        receive()
                    }
                }
            }
        }

        func hasHeaderEnd() -> Bool {
            buffer.range(of: Data([13, 10, 13, 10])) != nil
        }

        func finish(with res: HTTPResponse) {
            finished = true
            send(res)
        }

        func send(_ res: HTTPResponse) {
            conn.send(content: res.serialize(), contentContext: .finalMessage, isComplete: true,
                      completion: .contentProcessed { [conn] _ in conn.cancel() })
        }
    }
}
