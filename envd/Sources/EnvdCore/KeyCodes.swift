// Virtual key codes for the US keyboard layout. Only `key` actions use this
// table. `type` actions never do: they send the characters themselves.
public enum KeyCodes {
    public static let table: [String: UInt16] = {
        var t: [String: UInt16] = [
            "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
            "left": 123, "right": 124, "down": 125, "up": 126,
            // Letters.
            "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
            "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
            "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
            "n": 45, "m": 46,
            // The number row.
            "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
        ]
        let fkeys: [UInt16] = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111]
        for (i, code) in fkeys.enumerated() { t["f\(i + 1)"] = code }
        return t
    }()

    /// The key code for a name such as "return" or "a", or nil if unknown.
    public static func code(for name: String) -> UInt16? {
        table[name.lowercased()]
    }
}
