import Foundation
import AppKit
import PDFKit
import CoreText

@main
struct PDFEditorChecks {
    @MainActor static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let source = root.appendingPathComponent("source.pdf")
        var box = CGRect(x: 0, y: 0, width: 320, height: 480)
        let context = CGContext(source as CFURL, mediaBox: &box, nil)!
        for index in 0..<4 {
            context.beginPDFPage(nil)
            context.setFillColor(CGColor(gray: 0.7, alpha: 1))
            context.fill(CGRect(x: 40, y: 100, width: 100 + index * 20, height: 80))
            if index != 1 {
                context.textPosition = CGPoint(x: 40, y: 300)
                if index == 2 { context.setTextDrawingMode(.invisible) }
                let line = CTLineCreateWithAttributedString(NSAttributedString(
                    string: index == 2 ? "OCR searchable layer" : "Page \(index + 1)",
                    attributes: [.font: NSFont.systemFont(ofSize: 18)]
                ))
                CTLineDraw(line, context)
                context.setTextDrawingMode(.fill)
            }
            context.endPDFPage()
        }
        context.closePDF()
        let fixture = PDFDocument(url: source)!
        fixture.page(at: 2)!.rotation = 90
        fixture.page(at: 2)!.setBounds(CGRect(x: 20, y: 30, width: 280, height: 420), for: .cropBox)
        fixture.documentAttributes = [PDFDocumentAttribute.titleAttribute: "Editor check"]
        precondition(fixture.write(to: source))
        let original = try Data(contentsOf: source)
        let model = PDFEditorModel()
        try model.load(source)
        precondition(model.pageCount == 4)
        model.toggle(1); model.toggle(3)
        let output = root.appendingPathComponent("edited.pdf")
        try model.save(to: output)
        let result = PDFDocument(url: output)!
        precondition(result.pageCount == 2)
        precondition(result.page(at: 0)!.string!.contains("Page 1"))
        precondition(result.page(at: 1)!.string!.contains("OCR searchable layer"))
        precondition(result.page(at: 1)!.rotation == 90)
        precondition(result.page(at: 1)!.bounds(for: .cropBox) == fixture.page(at: 2)!.bounds(for: .cropBox))
        precondition(result.documentAttributes?[PDFDocumentAttribute.titleAttribute] as? String == "Editor check")
        for (new, old) in [(0, 0), (1, 2)] {
            let before = fixture.page(at: old)!.thumbnail(of: NSSize(width: 320, height: 480), for: .cropBox).tiffRepresentation!
            let after = result.page(at: new)!.thumbnail(of: NSSize(width: 320, height: 480), for: .cropBox).tiffRepresentation!
            precondition(before == after, "Page appearance changed")
        }
        let unchanged = try Data(contentsOf: source)
        precondition(unchanged == original)
        func expect(_ expected: PDFEditError, _ action: () throws -> Void) {
            do { try action(); fatalError("Expected error: \(expected)") }
            catch { precondition(String(describing: error) == String(describing: expected)) }
        }
        expect(.original) { try model.save(to: source) }
        let alias = root.appendingPathComponent("alias.pdf")
        try? FileManager.default.removeItem(at: alias)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: source)
        expect(.original) { try model.save(to: alias) }
        let hardlink = root.appendingPathComponent("hardlink.pdf")
        try? FileManager.default.removeItem(at: hardlink)
        try FileManager.default.linkItem(at: source, to: hardlink)
        expect(.original) { try model.save(to: hardlink) }
        model.resetSelection()
        precondition(!model.canSave && model.saved == nil)
        expect(.noSelection) { try model.save(to: output) }
        for index in 0..<4 { model.toggle(index) }
        precondition(!model.canSave)
        expect(.allPages) { try model.save(to: output) }
        model.toggle(1)
        try model.save(to: output)
        precondition(PDFDocument(url: output)!.page(at: 0)!.string?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false)
        try model.load(source)
        precondition(model.removed.isEmpty && model.saved == nil && model.currentPage == 0)
        let invalid = root.appendingPathComponent("invalid.pdf")
        try Data("not a PDF".utf8).write(to: invalid)
        expect(.unreadable) { try model.load(invalid) }
        precondition(model.pageCount == 4)
        let encrypted = root.appendingPathComponent("encrypted.pdf")
        precondition(fixture.write(to: encrypted, withOptions: [.userPasswordOption: "secret", .ownerPasswordOption: "owner"]))
        expect(.locked) { try model.load(encrypted) }
        print("PASS: page deletion, order, OCR text, scanned page, rotation, crop, metadata, appearance, original protection, reset, invalid/encrypted input")
    }
}
