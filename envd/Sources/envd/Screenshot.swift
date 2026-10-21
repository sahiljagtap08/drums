import CoreGraphics
import EnvdCore
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

// Takes a picture of the main display. The picture is returned as PNG bytes
// and as raw RGBA bytes (4 bytes per pixel, no row padding).
enum Screenshot {
    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    /// Captures the main display. `region` is in pixels of the captured image
    /// (the origin is the top left). With `captureResolution = .nominal` one
    /// pixel is one point. When `region` is nil the whole display is returned.
    ///
    /// Without Screen Recording permission the system hands back a black image
    /// and no error. That is not hidden here: the caller checks for black.
    static func capture(region: CGRect?) async throws -> (png: Data, rgba: Data, w: Int, h: Int) {
        let full: CGImage
        do {
            full = try await captureWithScreenCaptureKit()
        } catch let sckError {
            // ScreenCaptureKit failed. Try the command line tool instead. If
            // that fails too, report both errors.
            do {
                full = try await captureWithScreencapture()
            } catch {
                throw Failure(description: "ScreenCaptureKit: \(sckError); screencapture: \(error)")
            }
        }
        let image = try crop(full, to: region)
        guard let png = encodePNG(image) else { throw Failure(description: "PNG encode failed") }
        guard let rgba = rgbaBytes(image) else { throw Failure(description: "pixel read failed") }
        return (png, rgba, image.width, image.height)
    }

    // MARK: - Capture paths

    private static func captureWithScreenCaptureKit() async throws -> CGImage {
        let content = try await SCShareableContent.current
        let mainID = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == mainID }) ?? content.displays.first else {
            throw Failure(description: "no display found")
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.captureResolution = .nominal
        config.showsCursor = false
        config.width = display.width
        config.height = display.height
        return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    }

    private static func captureWithScreencapture() async throws -> CGImage {
        let path = NSTemporaryDirectory() + "envd-shot-\(UUID().uuidString).png"
        defer { try? FileManager.default.removeItem(atPath: path) }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = ["-x", "-t", "png", path]
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            process.terminationHandler = { p in
                if p.terminationStatus == 0 {
                    cont.resume()
                } else {
                    cont.resume(throwing: Failure(description: "screencapture exited with \(p.terminationStatus)"))
                }
            }
            do { try process.run() } catch { cont.resume(throwing: error) }
        }
        guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw Failure(description: "cannot read screencapture output")
        }
        return image
    }

    // MARK: - Image helpers

    /// Cuts `region` out of the image. The region is cut to the image edges.
    /// An empty result is an error.
    private static func crop(_ image: CGImage, to region: CGRect?) throws -> CGImage {
        guard let region else { return image }
        let bounds = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        let r = region.integral.intersection(bounds)
        guard !r.isNull, r.width >= 1, r.height >= 1, let cut = image.cropping(to: r) else {
            throw Failure(description: "region is outside the screen")
        }
        return cut
    }

    private static func encodePNG(_ image: CGImage) -> Data? {
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else {
            return nil
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }

    private static func rgbaBytes(_ image: CGImage) -> Data? {
        let w = image.width
        let h = image.height
        var data = Data(count: w * h * 4)
        let ok = data.withUnsafeMutableBytes { (buf: UnsafeMutableRawBufferPointer) -> Bool in
            guard let ctx = CGContext(
                data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        return ok ? data : nil
    }
}

/// What the screenshot route remembers between calls: the last hash for each
/// region, to spot a stale frame. Capture times go to the action log.
actor ScreenshotRecord {
    private var lastHash: [String: String] = [:]

    /// Stores `hash` for `key` and says whether it equals the previous one.
    /// The first call for a key is never stale.
    func noteHash(_ hash: String, for key: String) -> Bool {
        let stale = lastHash[key] == hash
        lastHash[key] = hash
        return stale
    }
}
