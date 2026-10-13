import SwiftUI
import AppKit
import PDFKit
import UniformTypeIdentifiers

enum PDFEditError: LocalizedError {
    case unreadable, locked, empty, noSelection, allPages, original, serialization

    var errorDescription: String? {
        switch self {
        case .unreadable: return "PDF를 열 수 없습니다. 파일을 확인하세요."
        case .locked: return "암호를 해제한 PDF 사본을 선택하세요."
        case .empty: return "PDF에 페이지가 없습니다."
        case .noSelection: return "삭제할 페이지를 선택하세요."
        case .allPages: return "최소 1쪽은 남겨야 합니다."
        case .original: return "원본과 다른 저장 이름을 선택하세요."
        case .serialization: return "PDF를 저장할 수 없습니다. 다른 저장 위치를 선택하세요."
        }
    }
}

@MainActor
final class PDFEditorModel: ObservableObject {
    @Published private(set) var document: PDFDocument?
    @Published private(set) var source: URL?
    @Published var removed: Set<Int> = []
    @Published var currentPage = 0
    @Published var errorMessage = ""
    @Published var isPresented = false
    @Published private(set) var saved: URL?

    var pageCount: Int { document?.pageCount ?? 0 }
    var canSave: Bool { !removed.isEmpty && removed.count < pageCount }

    func load(_ url: URL) throws {
        guard let pdf = PDFDocument(url: url) else { throw PDFEditError.unreadable }
        guard !pdf.isEncrypted else { throw PDFEditError.locked }
        guard pdf.pageCount > 0 else { throw PDFEditError.empty }
        document = pdf; source = url
        removed = []; currentPage = 0; errorMessage = ""; saved = nil
    }

    func toggle(_ index: Int) {
        guard (0..<pageCount).contains(index) else { return }
        if removed.contains(index) { removed.remove(index) }
        else { removed.insert(index) }
        saved = nil; errorMessage = ""
    }

    func resetSelection() {
        removed = []; saved = nil; errorMessage = ""
    }

    func save(to url: URL) throws {
        guard let document, let source else { throw PDFEditError.unreadable }
        guard !removed.isEmpty else { throw PDFEditError.noSelection }
        guard removed.count < document.pageCount else { throw PDFEditError.allPages }
        let input = source.resolvingSymlinksInPath().standardizedFileURL
        let output = url.resolvingSymlinksInPath().standardizedFileURL
        let inputID = try input.resourceValues(forKeys: [.fileResourceIdentifierKey]).fileResourceIdentifier
        let outputID = try? output.resourceValues(forKeys: [.fileResourceIdentifierKey]).fileResourceIdentifier
        guard input != output,
              !(inputID != nil && outputID != nil && (inputID as? NSObject)?.isEqual(outputID) == true)
        else { throw PDFEditError.original }
        guard let bytes = document.dataRepresentation(), let edited = PDFDocument(data: bytes) else {
            throw PDFEditError.serialization
        }
        for index in removed.sorted(by: >) { edited.removePage(at: index) }
        guard let bytes = edited.dataRepresentation(), let check = PDFDocument(data: bytes),
              check.pageCount == pageCount - removed.count else { throw PDFEditError.serialization }
        try bytes.write(to: url, options: .atomic)
        saved = url; errorMessage = ""
    }

    func chooseInput() -> Bool {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "페이지를 삭제할 PDF를 선택하세요. OCR 없이 편집할 수 있습니다."
        guard panel.runModal() == .OK, let url = panel.url else { return false }
        do { try load(url); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    func chooseOutput() {
        guard canSave, let source else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        panel.canCreateDirectories = true
        panel.directoryURL = source.deletingLastPathComponent()
        panel.nameFieldStringValue = source.deletingPathExtension().lastPathComponent + "_편집.pdf"
        panel.message = "선택한 페이지를 제외한 PDF를 저장합니다. 원본은 유지됩니다."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try save(to: url) }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct PDFPagePreview: NSViewRepresentable {
    let document: PDFDocument
    let pageIndex: Int

    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePage
        return view
    }

    func updateNSView(_ view: PDFView, context: Context) {
        if view.document !== document { view.document = document }
        if let page = document.page(at: pageIndex), view.currentPage !== page { view.go(to: page) }
    }
}

struct PDFEditorView: View {
    @ObservedObject var model: PDFEditorModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("PDF 페이지 편집").font(.headline)
                    Text(model.source?.lastPathComponent ?? "").lineLimit(2)
                    Text("체크한 페이지는 저장할 때 삭제됩니다. 쪽 번호는 원본 PDF 기준입니다.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button("닫기") { dismiss() }
            }
            if let document = model.document {
                HStack(spacing: 16) {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 8) {
                                ForEach(0..<model.pageCount, id: \.self) { index in
                                    HStack(spacing: 8) {
                                        Toggle("\(index + 1)쪽 삭제", isOn: Binding(
                                            get: { model.removed.contains(index) },
                                            set: { _ in model.toggle(index) }
                                        )).toggleStyle(.checkbox)
                                        Spacer()
                                        Button { model.currentPage = index } label: {
                                            Image(systemName: "eye")
                                        }.help("\(index + 1)쪽 미리 보기")
                                    }
                                    .padding(8)
                                    .background(model.currentPage == index ? Color.accentColor.opacity(0.12) : Color.clear)
                                    .id(index)
                                }
                            }
                        }
                        .onChange(of: model.currentPage) { _, index in proxy.scrollTo(index) }
                    }.frame(width: 192)
                    VStack(spacing: 8) {
                        PDFPagePreview(document: document, pageIndex: model.currentPage)
                        HStack(spacing: 12) {
                            Button("이전 쪽") { model.currentPage -= 1 }.disabled(model.currentPage == 0)
                            Text("\(model.currentPage + 1) / \(model.pageCount)쪽").monospacedDigit()
                            Button("다음 쪽") { model.currentPage += 1 }.disabled(model.currentPage + 1 >= model.pageCount)
                            Toggle("현재 쪽 삭제", isOn: Binding(
                                get: { model.removed.contains(model.currentPage) },
                                set: { _ in model.toggle(model.currentPage) }
                            )).toggleStyle(.checkbox)
                        }
                    }
                }
            }
            HStack(spacing: 12) {
                Text("삭제 \(model.removed.count)쪽 · 저장 \(model.pageCount - model.removed.count)쪽")
                Button("선택 모두 취소", action: model.resetSelection)
                    .disabled(model.removed.isEmpty)
                Spacer()
                Button("편집 PDF 저장…", action: model.chooseOutput)
                    .buttonStyle(.borderedProminent).disabled(!model.canSave)
            }
            if model.removed.count == model.pageCount {
                Text("최소 1쪽은 남겨야 합니다.").foregroundStyle(.red)
            }
            if !model.errorMessage.isEmpty { Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled) }
            if let saved = model.saved {
                HStack(spacing: 12) {
                    Text("저장 완료: \(saved.lastPathComponent)").textSelection(.enabled)
                    Button("편집 PDF 열기") { NSWorkspace.shared.open(saved) }
                }
            }
        }
        .padding(24).frame(minWidth: 864, minHeight: 640)
        .preferredColorScheme(.light)
    }
}
