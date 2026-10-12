import Foundation
import Vision

func coordinates(_ region: NormalizedRegion) -> [Double] {
    let r = region.normalizedPath.boundingBox
    return [r.minX, r.minY, r.width, r.height]
}

@main
struct OCRDocument {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 3 else { fatalError("usage: vision_document image.png output.json") }
        var request = RecognizeDocumentsRequest()
        request.textRecognitionOptions.recognitionLanguages = [Locale.Language(identifier: "ko-KR"), Locale.Language(identifier: "en-US")]
        request.textRecognitionOptions.useLanguageCorrection = false
        let results = try await request.perform(on: URL(fileURLWithPath: args[1]))
        let documents: [[String: Any]] = results.map { result in
            let tables = result.document.tables.map { table in
                table.rows.map { row in
                    row.map { cell -> [String: Any] in
                        ["text": cell.content.text.transcript,
                         "box": coordinates(cell.content.boundingRegion),
                         "row_range": [cell.rowRange.lowerBound, cell.rowRange.upperBound],
                         "column_range": [cell.columnRange.lowerBound, cell.columnRange.upperBound]]
                    }
                }
            }
            return ["text": result.document.text.transcript, "tables": tables]
        }
        try JSONSerialization.data(withJSONObject: documents, options: [.prettyPrinted, .sortedKeys])
            .write(to: URL(fileURLWithPath:args[2]), options:.atomic)
        print("\(results.count) documents; \(results.reduce(0) {$0 + $1.document.tables.count}) tables")
    }
}
