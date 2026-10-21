import Testing
@testable import EnvdCore

@Test func blackFrameDetected() {
    #expect(FrameHash.isBlack(rgba: blankFrame(width: 64, height: 64, value: 0), width: 64, height: 64))
    #expect(!FrameHash.isBlack(rgba: redFrame(width: 64, height: 64), width: 64, height: 64))
}

@Test func regionHashIgnoresOutsidePixels() {
    let a = blankFrame(width: 100, height: 100, value: 1)
    let b = withByte(a, at: 0, value: 99)  // pixel (0,0) differs
    let r = region(x: 50, y: 50, width: 20, height: 20)
    #expect(FrameHash.hash(rgba: a, width: 100, height: 100, region: r)
        == FrameHash.hash(rgba: b, width: 100, height: 100, region: r))
    #expect(FrameHash.hash(rgba: a, width: 100, height: 100, region: nil)
        != FrameHash.hash(rgba: b, width: 100, height: 100, region: nil))
}
