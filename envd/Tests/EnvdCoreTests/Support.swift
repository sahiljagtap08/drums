// Helpers that need Foundation. Test files must not import Foundation and
// Testing together: under the Command Line Tools the Testing/Foundation
// overlay module is missing, so that import pair fails to compile.
import Foundation
@testable import EnvdCore

func rawBytes(_ s: String) -> Data {
    Data(s.utf8)
}

func tempLockPath(_ name: String) -> String {
    NSTemporaryDirectory() + name
}

/// A pixel region. Tests cannot import CoreGraphics next to Testing, so the
/// CGRect is built here.
func region(x: Double, y: Double, width: Double, height: Double) -> CGRect {
    CGRect(x: x, y: y, width: width, height: height)
}

/// A width x height RGBA frame with every byte set to `value`.
func blankFrame(width: Int, height: Int, value: UInt8) -> Data {
    Data(repeating: value, count: 4 * width * height)
}

/// A frame where every pixel has red 200 and the other bytes 0.
func redFrame(width: Int, height: Int) -> Data {
    var d = blankFrame(width: width, height: height, value: 0)
    for i in stride(from: 0, to: d.count, by: 4) { d[i] = 200 }
    return d
}

/// A copy of `data` with one byte changed.
func withByte(_ data: Data, at index: Int, value: UInt8) -> Data {
    var d = data
    d[index] = value
    return d
}

/// Counts calls and remembers the specs it was given. Used as the "perform"
/// stand-in in route tests so no real input is posted.
final class Recorder: @unchecked Sendable {
    private let lock = NSLock()
    private var specs: [ActionSpec] = []

    func record(_ spec: ActionSpec) {
        lock.lock(); defer { lock.unlock() }
        specs.append(spec)
    }

    var count: Int {
        lock.lock(); defer { lock.unlock() }
        return specs.count
    }

    var seqs: [Int] {
        lock.lock(); defer { lock.unlock() }
        return specs.map(\.seq)
    }
}

/// A POST /action request. `headers` should use lower case names.
func actionRequest(_ json: String, headers: [String: String] = ["host": "127.0.0.1:7701"]) -> HTTPRequest {
    HTTPRequest(method: "POST", path: "/action", headers: headers, body: rawBytes(json))
}

func bodyText(_ res: HTTPResponse) -> String {
    String(decoding: res.body, as: UTF8.self)
}

extension ActionSpec {
    /// A throwaway click spec, for tests that only count calls.
    static func probe(seq: Int) -> ActionSpec {
        ActionSpec(gen: 1, seq: seq, type: "click", x: 0, y: 0)
    }
}
