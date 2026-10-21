import CryptoKit
import Foundation

// The pasteboard hash. It is the SHA-256 of the text's UTF-8 bytes as lower
// case hex, or "empty" when the pasteboard holds no string at all.
public enum PasteboardHash {
    public static let empty = "empty"

    public static func hash(of text: String?) -> String {
        guard let text else { return empty }
        return SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
