import Testing
@testable import EnvdCore

@Test func decodeRejectsClickWithoutCoordinates() {
    #expect(throws: (any Error).self) {
        try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":1,"type":"click"}"#))
    }
}

@Test func decodeTypeWithUnicode() throws {
    let a = try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":2,"type":"type","text":"D01-G1-R0001-00ff<>"}"#))
    #expect(a.text == "D01-G1-R0001-00ff<>")
    let b = try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":3,"type":"type","text":"café 世界 😀"}"#))
    #expect(b.text == "café 世界 😀")
}

@Test func decodeClickAndDoubleClick() throws {
    let c = try ActionSpec.decode(rawBytes(#"{"gen":3,"seq":4,"type":"click","x":10.5,"y":20}"#))
    #expect(c.x == 10.5 && c.y == 20 && c.gen == 3 && c.seq == 4)
    let d = try ActionSpec.decode(rawBytes(#"{"gen":3,"seq":5,"type":"dblclick","x":1,"y":2,"button":"right"}"#))
    #expect(d.type == "dblclick" && d.button == "right")
    #expect(throws: (any Error).self) {
        try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":1,"type":"dblclick","x":1}"#))
    }
}

@Test func decodeRejectsMissingFieldsPerType() {
    let bad = [
        #"{"gen":1,"seq":1,"type":"type"}"#,
        #"{"gen":1,"seq":1,"type":"type","text":""}"#,
        #"{"gen":1,"seq":1,"type":"key"}"#,
        #"{"gen":1,"seq":1,"type":"scroll"}"#,
        #"{"gen":1,"seq":1}"#,
        #"{"seq":1,"type":"click","x":1,"y":1}"#,
        #"{"gen":1,"type":"click","x":1,"y":1}"#,
        #"{"gen":1,"seq":1,"type":"wiggle"}"#,
        #"not json"#,
    ]
    for json in bad {
        #expect(throws: (any Error).self, "should reject \(json)") {
            try ActionSpec.decode(rawBytes(json))
        }
    }
}

@Test func decodeScrollNeedsDxOrDy() throws {
    let a = try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":1,"type":"scroll","dy":-120}"#))
    #expect(a.dy == -120 && a.dx == nil)
    let b = try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":1,"type":"scroll","dx":5}"#))
    #expect(b.dx == 5)
}

@Test func decodeKeyChecksNamesAndModifiers() throws {
    let a = try ActionSpec.decode(rawBytes(#"{"gen":1,"seq":1,"type":"key","key":"a","modifiers":["cmd","shift"]}"#))
    #expect(a.key == "a" && a.modifiers == ["cmd", "shift"])
    let bad = [
        #"{"gen":1,"seq":1,"type":"key","key":"banana"}"#,
        #"{"gen":1,"seq":1,"type":"key","key":"a","modifiers":["hyper"]}"#,
        #"{"gen":1,"seq":1,"type":"click","x":1,"y":1,"button":"middle"}"#,
    ]
    for json in bad {
        #expect(throws: (any Error).self, "should reject \(json)") {
            try ActionSpec.decode(rawBytes(json))
        }
    }
}

@Test func keyTableCoversRequiredNames() {
    var names = ["return", "tab", "escape", "space", "delete", "left", "right", "up", "down"]
    names += "abcdefghijklmnopqrstuvwxyz0123456789".map { String($0) }
    names += (1...12).map { "f\($0)" }
    for n in names { #expect(KeyCodes.code(for: n) != nil, "missing key \(n)") }
    // Every key has its own code.
    let codes = names.compactMap { KeyCodes.code(for: $0) }
    #expect(Set(codes).count == codes.count)
    #expect(KeyCodes.code(for: "return") == 36)
    #expect(KeyCodes.code(for: "a") == 0)
}
