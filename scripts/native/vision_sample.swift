import Foundation
import Vision
import CoreGraphics
import ImageIO

// Read-only OCR: writes recognized text and measured character boxes to JSON.
let args = CommandLine.arguments
 guard args.count == 3 || args.count == 4 else { fatalError("usage: vision_sample image.png output.json [languages]") }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = args.count == 4 ? args[3].split(separator: ",").map(String.init) : ["ko-KR", "en-US"]
request.usesLanguageCorrection = false
let handler = VNImageRequestHandler(url: URL(fileURLWithPath: args[1]), options: [:])
try handler.perform([request])
func box(_ r: CGRect) -> [Double] { [r.minX, r.minY, r.width, r.height] }
var lines: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let s = candidate.string
    var chars: [[String: Any]] = []
    for i in s.indices {
        let end = s.index(after: i)
        if let b = try candidate.boundingBox(for: i..<end) {
            chars.append(["text": String(s[i]), "box": box(b.boundingBox)])
        }
    }
    lines.append(["text": s, "confidence": candidate.confidence,
                  "box": box(observation.boundingBox), "chars": chars])
}
let data = try JSONSerialization.data(withJSONObject: ["engine": "Apple Vision", "lines": lines], options: [.prettyPrinted, .sortedKeys])
try data.write(to: URL(fileURLWithPath: args[2]), options: .atomic)
print("\(args[1]): \(lines.count) lines")
