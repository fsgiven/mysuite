// mysuite-cutout — isolates the foreground subject(s) of an image and writes
// a transparent-background PNG, using the macOS Vision framework's
// VNGenerateForegroundInstanceMaskRequest (the same on-device tech behind
// Preview/Photos/Safari's "Copy Subject"). Requires macOS 14+.
//
// Usage: mysuite-cutout <input-image> <output.png>
// Exit codes: 0 success, 1 any failure (message on stderr) — matches how
// mysuite's Python side treats every other shelled-out tool.

import CoreGraphics
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
if args.count == 2 && (args[1] == "--version" || args[1] == "-v") {
    print("mysuite-cutout 1.0 (Vision VNGenerateForegroundInstanceMaskRequest)")
    exit(0)
}
guard args.count == 3 else {
    fail("usage: mysuite-cutout <input-image> <output.png>")
}
let inputPath = args[1]
let outputPath = args[2]

guard
    let inputURL = CFURLCreateWithFileSystemPath(nil, inputPath as CFString, .cfurlposixPathStyle, false),
    let source = CGImageSourceCreateWithURL(inputURL, nil),
    let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    fail("failed to load input image: \(inputPath)")
}
// Carried forward into the output PNG below — otherwise EXIF/IPTC/etc. would
// be silently dropped, since generateMaskedImage() only returns pixel data,
// not the source's metadata dictionary.
let sourceMetadata = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)

let requestHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
let request = VNGenerateForegroundInstanceMaskRequest()

do {
    try requestHandler.perform([request])
} catch {
    fail("Vision request failed: \(error)")
}

guard let result = request.results?.first else {
    fail("no foreground subject found in image")
}

do {
    let maskedPixelBuffer = try result.generateMaskedImage(
        ofInstances: result.allInstances,
        from: requestHandler,
        croppedToInstancesExtent: false
    )
    let ciImage = CIImage(cvPixelBuffer: maskedPixelBuffer)
    let context = CIContext()
    guard let outputCGImage = context.createCGImage(ciImage, from: ciImage.extent) else {
        fail("failed to render masked image")
    }
    guard
        let outputURL = CFURLCreateWithFileSystemPath(nil, outputPath as CFString, .cfurlposixPathStyle, false),
        let destination = CGImageDestinationCreateWithURL(outputURL, UTType.png.identifier as CFString, 1, nil)
    else {
        fail("failed to open output path: \(outputPath)")
    }
    CGImageDestinationAddImage(destination, outputCGImage, sourceMetadata)
    guard CGImageDestinationFinalize(destination) else {
        fail("failed to write output PNG: \(outputPath)")
    }
} catch {
    fail("mask generation failed: \(error)")
}
