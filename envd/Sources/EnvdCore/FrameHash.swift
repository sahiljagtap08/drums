import CoreGraphics
import Foundation

// Pure helpers for judging a captured frame. The frame is raw RGBA bytes,
// 4 bytes per pixel, rows from the top, with no padding between rows.
public enum FrameHash {
    private static let fnvOffset: UInt64 = 0xcbf2_9ce4_8422_2325
    private static let fnvPrime: UInt64 = 0x0000_0100_0000_01b3

    /// FNV-1a 64 over the bytes of `region` (or the whole frame when `region`
    /// is nil), as 16 lower-case hex digits. Only pixels inside the region are
    /// read, so changes outside it do not change the hash. The region is
    /// rounded to whole pixels and cut to the frame edges.
    public static func hash(rgba: Data, width: Int, height: Int, region: CGRect?) -> String {
        var h = fnvOffset
        let box = pixelBox(region, width: width, height: height)
        if rgba.count >= width * height * 4, box.x1 > box.x0, box.y1 > box.y0 {
            rgba.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                for y in box.y0..<box.y1 {
                    var i = (y * width + box.x0) * 4
                    let end = (y * width + box.x1) * 4
                    while i < end {
                        h = (h ^ UInt64(raw[i])) &* fnvPrime
                        i += 1
                    }
                }
            }
        }
        let hex = String(h, radix: 16)
        return String(repeating: "0", count: 16 - hex.count) + hex
    }

    /// True when at least 99.5% of the sampled pixels have red, green and blue
    /// all below 8. Alpha is ignored. A frame with no readable pixels also
    /// counts as black, because nothing visible was captured.
    public static func isBlack(rgba: Data, width: Int, height: Int) -> Bool {
        let total = width * height
        guard width > 0, height > 0, rgba.count >= total * 4 else { return true }
        // Look at about 10,000 pixels, spread evenly over the frame.
        let step = max(1, total / 10_000)
        var sampled = 0
        var dark = 0
        rgba.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            var p = 0
            while p < total {
                let i = p * 4
                sampled += 1
                if raw[i] < 8, raw[i + 1] < 8, raw[i + 2] < 8 { dark += 1 }
                p += step
            }
        }
        return Double(dark) >= Double(sampled) * 0.995
    }

    /// The biggest region number the route accepts. No real screen is wider.
    public static let maxRegionValue = 100_000.0

    /// Reads `x,y,w,h` from the region query parameter. Returns nil when it is
    /// not four numbers, when any number is not finite (inf, nan) or is above
    /// `maxRegionValue`, when x or y is negative, or when w or h is not above 0.
    public static func parseRegion(_ raw: String) -> CGRect? {
        let parts = raw.split(separator: ",", omittingEmptySubsequences: false).map { Double($0) }
        guard parts.count == 4, let x = parts[0], let y = parts[1], let w = parts[2], let h = parts[3] else {
            return nil
        }
        for v in [x, y, w, h] where !v.isFinite || v > maxRegionValue { return nil }
        guard x >= 0, y >= 0, w > 0, h > 0 else { return nil }
        return CGRect(x: x, y: y, width: w, height: h)
    }

    /// The region as whole pixel edges, cut to the frame. Edges are clamped as
    /// Doubles before turning into Int, and a non-finite edge gives an empty
    /// box, so this can never trap.
    private static func pixelBox(_ region: CGRect?, width: Int, height: Int) -> (x0: Int, y0: Int, x1: Int, y1: Int) {
        guard let r = region else { return (0, 0, width, height) }
        let values = [r.minX, r.minY, r.maxX, r.maxY]
        if values.contains(where: { !$0.isFinite }) { return (0, 0, 0, 0) }
        func edge(_ v: Double, _ limit: Int) -> Int { Int(min(max(v, 0), Double(limit))) }
        return (edge(r.minX.rounded(.down), width), edge(r.minY.rounded(.down), height),
                edge(r.maxX.rounded(.up), width), edge(r.maxY.rounded(.up), height))
    }
}
