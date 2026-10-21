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
