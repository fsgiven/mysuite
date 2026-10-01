// mysuite-vision — text recognition (OCR) and barcode/QR reading with the macOS Vision framework, on device.
//
// Usage:
//   mysuite-vision text <image> [--languages en-US,de-DE] [--fast]
//   mysuite-vision barcodes <image>
// Prints ONE JSON document on stdout. Exit codes: 0 success (even when nothing is found), 1 any failure
// (message on stderr). Requires macOS 13+ for the request revisions used here.

import CoreGraphics
import Foundation
import ImageIO
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func emit(_ object: Any) -> Never {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]),
          let text = String(data: data, encoding: .utf8) else { fail("could not encode result") }
    print(text)
    exit(0)
}

let args = CommandLine.arguments
if args.count == 2 && (args[1] == "--version" || args[1] == "-v") {
    print("mysuite-vision 1.0 (VNRecognizeTextRequest, VNDetectBarcodesRequest)")
    exit(0)
}
guard args.count >= 3, ["text", "barcodes"].contains(args[1]) else {
    fail("usage: mysuite-vision text <image> [--languages en-US,de-DE] [--fast] | mysuite-vision barcodes <image>")
}
let mode = args[1]
let inputPath = args[2]

guard
    let inputURL = CFURLCreateWithFileSystemPath(nil, inputPath as CFString, .cfurlposixPathStyle, false),
    let source = CGImageSourceCreateWithURL(inputURL, nil),
    let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    fail("failed to load input image: \(inputPath)")
}
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

func box(_ r: CGRect) -> [String: Double] {
    // Vision's origin is bottom-left; report top-left pixel coordinates like every image tool does.
    let w = Double(cgImage.width), h = Double(cgImage.height)
    return ["x": Double(r.minX) * w, "y": (1 - Double(r.maxY)) * h, "width": Double(r.width) * w, "height": Double(r.height) * h]
}

if mode == "text" {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = args.contains("--fast") ? .fast : .accurate
    request.usesLanguageCorrection = true
    if let i = args.firstIndex(of: "--languages"), i + 1 < args.count {
        request.recognitionLanguages = args[i + 1].split(separator: ",").map(String.init)
    }
    do { try handler.perform([request]) } catch { fail("Vision request failed: \(error)") }
    var lines: [[String: Any]] = []
    for observation in request.results ?? [] {
        guard let best = observation.topCandidates(1).first else { continue }
        lines.append(["text": best.string, "confidence": Double(best.confidence), "box": box(observation.boundingBox)])
    }
    emit(["width": cgImage.width, "height": cgImage.height, "lines": lines])
} else {
    let request = VNDetectBarcodesRequest()
    do { try handler.perform([request]) } catch { fail("Vision request failed: \(error)") }
    var codes: [[String: Any]] = []
    for observation in request.results ?? [] {
        codes.append([
            "payload": observation.payloadStringValue ?? "",
            "symbology": observation.symbology.rawValue,
            "confidence": Double(observation.confidence),
            "box": box(observation.boundingBox),
        ])
    }
    emit(["width": cgImage.width, "height": cgImage.height, "codes": codes])
}
