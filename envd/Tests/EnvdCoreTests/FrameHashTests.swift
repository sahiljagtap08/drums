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

@Test func regionHashSeesChangeInsideRegion() {
    let a = blankFrame(width: 100, height: 100, value: 1)
    let inside = withByte(a, at: (60 * 100 + 60) * 4, value: 99)  // pixel (60,60)
    let r = region(x: 50, y: 50, width: 20, height: 20)
    #expect(FrameHash.hash(rgba: a, width: 100, height: 100, region: r)
        != FrameHash.hash(rgba: inside, width: 100, height: 100, region: r))
}

@Test func blackBoundaryIsSevenVersusEight() {
    #expect(FrameHash.isBlack(rgba: blankFrame(width: 64, height: 64, value: 7), width: 64, height: 64))
    #expect(!FrameHash.isBlack(rgba: blankFrame(width: 64, height: 64, value: 8), width: 64, height: 64))
}

@Test func nonFiniteOrHugeRegionIsRejected() {
    #expect(FrameHash.parseRegion("0,0,400,300") != nil)
    for bad in ["inf,0,10,10", "0,0,inf,10", "nan,0,10,10", "0,0,10,nan", "-inf,0,10,10",
                "0,0,1e300,10", "0,0,10,1e9", "-1,0,10,10", "0,0,0,10", "1,2,3", "a,b,c,d"] {
        #expect(FrameHash.parseRegion(bad) == nil, "should reject \(bad)")
    }
}

@Test func hashDoesNotTrapOnBadRegions() {
    let a = blankFrame(width: 10, height: 10, value: 1)
    let empty = FrameHash.hash(rgba: a, width: 10, height: 10, region: region(x: 0, y: 0, width: 0, height: 0))
    let bad = [region(x: .infinity, y: 0, width: 5, height: 5),
               region(x: 0, y: .nan, width: 5, height: 5),
               region(x: 0, y: 0, width: .infinity, height: 5),
               region(x: 0, y: 0, width: 1e300, height: 1e300),
               region(x: -1e300, y: -1e300, width: 5, height: 5)]
    for r in bad { _ = FrameHash.hash(rgba: a, width: 10, height: 10, region: r) }
    // A region far larger than the frame is cut to the frame, so it equals the full hash.
    let huge = FrameHash.hash(rgba: a, width: 10, height: 10, region: region(x: 0, y: 0, width: 1e300, height: 1e300))
    #expect(huge == FrameHash.hash(rgba: a, width: 10, height: 10, region: nil))
    #expect(FrameHash.hash(rgba: a, width: 10, height: 10, region: region(x: .infinity, y: 0, width: 5, height: 5)) == empty)
}
