import SwiftUI
import AppKit
import CoreText
import UniformTypeIdentifiers

struct SavedJob: Identifiable {
    let id: String
    let directory: URL
    let name: String
    let source: URL
    let status: String
    let scope: String
    let count: Int
    let processed: Int
    let sampleReady: Bool
    let approved: Bool
    let sample: URL?
    let output: URL?
    let report: URL?
    let updated: Double
}

@MainActor
final class OCRModel: ObservableObject {
    @Published var source: URL?
    @Published var destination: URL?
    @Published var job: URL?
    @Published var sample: URL?
    @Published var result: URL?
    @Published var report: URL?
    @Published var jobs: [SavedJob] = []
    @Published var running = false
    @Published var stopping = false
    @Published var sampleReady = false
    @Published var approved = false
    @Published var done = 0
    @Published var target = 0
    @Published var pageCount = 0
    @Published var status = "ready"
    @Published var scope = "sample"
    @Published var message = "PDF를 선택해 샘플부터 확인하세요."
    @Published var errorMessage = ""
    @Published var reviews = 0
    @Published var failed = 0
    var process: Process?
    var overwriteAllowed = false
    var receivedFinal = false
    var quitting = false
    let jobRoot: URL

    init() {
        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "--job-root"), index+1 < args.count {
            jobRoot = URL(fileURLWithPath: args[index+1])
        } else {
            jobRoot = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("OCR Helper/jobs", isDirectory: true)
        }
        refreshJobs()
        if let index = args.firstIndex(of: "--input"), index+1 < args.count {
            select(URL(fileURLWithPath: args[index+1]))
        }
    }

    func chooseInput() {
        guard !running else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "변환할 PDF를 선택하세요. 원본은 유지됩니다."
        if panel.runModal() == .OK, let url = panel.url { select(url) }
    }

    func select(_ url: URL) {
        guard !running, url.isFileURL, url.pathExtension.lowercased() == "pdf" else { return }
        source = url
        let base = url.deletingPathExtension().lastPathComponent + "_OCR"
        var candidate = url.deletingLastPathComponent().appendingPathComponent(base+".pdf")
        var suffix = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = url.deletingLastPathComponent().appendingPathComponent(base+" \(suffix).pdf")
            suffix += 1
        }
        destination = candidate
        job = nil; sample = nil; result = nil; report = nil
        sampleReady = false; approved = false; overwriteAllowed = false
        pageCount = 0; done = 0; target = 0; reviews = 0; failed = 0
        status = "ready"; scope = "sample"; errorMessage = ""
        message = "먼저 대표 페이지의 샘플을 변환하세요."
    }

    func chooseDestination() {
        guard !running else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        panel.canCreateDirectories = true
        panel.message = "변환한 PDF를 저장할 위치를 선택하세요."
        panel.nameFieldStringValue = destination?.lastPathComponent ?? "변환_OCR.pdf"
        panel.directoryURL = destination?.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            if url.standardizedFileURL == source?.standardizedFileURL {
                errorMessage = "원본과 다른 저장 이름을 선택하세요."
                return
            }
            destination = url
            overwriteAllowed = FileManager.default.fileExists(atPath: url.path)
            errorMessage = ""
        }
    }

    func refreshJobs() {
        let directories = (try? FileManager.default.contentsOfDirectory(at: jobRoot, includingPropertiesForKeys: nil)) ?? []
        jobs = directories.compactMap { dir in
            guard let bytes = try? Data(contentsOf: dir.appendingPathComponent("job.json")),
                  let value = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  let id = value["id"] as? String, let name = value["source_name"] as? String,
                  let sourcePath = value["source_path"] as? String else { return nil }
            let pages = value["pages"] as? [String: [String: Any]] ?? [:]
            let count = pages.values.filter { ["complete","no_text","existing_text","failed"].contains($0["status"] as? String ?? "") }.count
            let export = value["export"] as? [String: Any]
            func url(_ path: String?) -> URL? { path.map { URL(fileURLWithPath: $0) } }
            return SavedJob(id: id, directory: dir, name: name, source: URL(fileURLWithPath: sourcePath),
                status: value["status"] as? String ?? "ready", scope: value["scope"] as? String ?? "sample",
                count: value["page_count"] as? Int ?? 0, processed: count,
                sampleReady: value["sample_ready"] as? Bool ?? false,
                approved: value["sample_approved"] as? Bool ?? false,
                sample: url(value["sample_path"] as? String), output: url(export?["path"] as? String ?? value["destination"] as? String),
                report: url(value["report_path"] as? String), updated: value["updated_at"] as? Double ?? 0)
        }.sorted { $0.updated > $1.updated }
    }

    func load(_ saved: SavedJob) {
        guard !running else { return }
        source = saved.source; job = saved.directory
        destination = saved.output ?? saved.source.deletingPathExtension().appendingPathExtension("ocr.pdf")
        sample = saved.sample; result = saved.status.hasPrefix("complete") ? saved.output : nil; report = saved.report
        sampleReady = saved.sampleReady; approved = saved.approved
        status = saved.status; scope = saved.scope; pageCount = saved.count
        done = saved.processed; target = saved.count; overwriteAllowed = false; errorMessage = ""
        message = "저장된 작업을 불러왔습니다. 완료한 페이지는 다시 처리하지 않습니다."
    }

    func start(_ requestedScope: String) {
        guard !running, source != nil else { return }
        guard requestedScope != "full" || (sampleReady && approved && destination != nil) else { return }
        guard let resources = Bundle.main.resourceURL else { return }
        let worker = resources.appendingPathComponent("engine/ocr-worker")
        guard FileManager.default.isExecutableFile(atPath: worker.path) else {
            errorMessage = "앱의 실행 파일이 없습니다. 완성된 앱을 다시 사용하세요."
            return
        }
        var args = ["--scope", requestedScope, "--job-root", jobRoot.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        if let job { args += ["--job", job.path] } else if let source { args += ["--input", source.path] }
        if requestedScope == "full", let destination {
            args += ["--output", destination.path, "--sample-approved"]
            if overwriteAllowed { args += ["--overwrite"] }
            if status == "complete_with_errors" { args += ["--retry-failed"] }
        }
        let task = Process(); task.executableURL = worker; task.arguments = args
        let stdout = Pipe(), stderr = Pipe()
        task.standardOutput = stdout; task.standardError = stderr
        running = true; stopping = false; receivedFinal = false
        scope = requestedScope; errorMessage = ""; done = 0; target = 0
        message = "작업을 준비하고 있습니다."
        process = task
        let readers = DispatchGroup()
        readers.enter()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var pending = Data()
            while true {
                let chunk = stdout.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let end = pending.firstIndex(of: 10) {
                    let line = pending.prefix(upTo: end)
                    pending.removeSubrange(...end)
                    if let event = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] {
                        DispatchQueue.main.async { self?.receive(event) }
                    }
                }
            }
            readers.leave()
        }
        readers.enter()
        DispatchQueue.global(qos: .utility).async {
            while !stderr.fileHandleForReading.availableData.isEmpty { }
            readers.leave()
        }
        task.terminationHandler = { [weak self] process in
            readers.notify(queue: .main) {
                Task { @MainActor in
                guard let self else { return }
                self.running = false; self.stopping = false; self.process = nil
                if !self.receivedFinal {
                    self.status = "error"
                    self.errorMessage = "처리가 중단됐습니다. 작업을 다시 불러오면 완료한 페이지부터 이어갈 수 있습니다. (종료 코드 \(process.terminationStatus))"
                }
                self.refreshJobs()
                if self.quitting { NSApp.reply(toApplicationShouldTerminate: true) }
                }
            }
        }
        do { try task.run() }
        catch {
            stdout.fileHandleForWriting.closeFile(); stderr.fileHandleForWriting.closeFile()
            running = false; process = nil; errorMessage = "실행하지 못했습니다: \(error.localizedDescription)"
        }
    }

    func receive(_ event: [String: Any]) {
        if let value = event["done"] as? Int { done = value }
        if let value = event["target"] as? Int { target = value }
        if let value = event["message"] as? String { message = value }
        if let path = event["job_dir"] as? String { job = URL(fileURLWithPath: path) }
        switch event["event"] as? String {
        case "job":
            pageCount = event["page_count"] as? Int ?? pageCount
        case "finished":
            receivedFinal = true
            status = event["status"] as? String ?? "error"
            reviews = event["review_count"] as? Int ?? reviews
            failed = (event["failed_pages"] as? [Int])?.count ?? 0
            if let path = event["report_path"] as? String { report = URL(fileURLWithPath: path) }
            if status == "sample_ready" {
                sampleReady = true
                if let path = event["path"] as? String { sample = URL(fileURLWithPath: path) }
                message = "샘플을 열어 검색과 원본 모습을 확인하세요."
            } else if status.hasPrefix("complete") {
                if let path = event["path"] as? String { result = URL(fileURLWithPath: path) }
                message = failed == 0 ? "PDF 저장을 완료했습니다." : "PDF를 저장했습니다. 미처리 \(failed)쪽은 원본으로 포함했습니다."
            } else if status == "paused" { message = "중단했습니다. 완료한 페이지는 저장돼 있습니다." }
        case "error":
            receivedFinal = true; status = "error"
            errorMessage = event["message"] as? String ?? "처리 중 문제가 발생했습니다."
        default: break
        }
    }

    func pause() {
        guard running, let process, process.isRunning else { return }
        stopping = true; message = "안전하게 중단하는 중입니다. 완료한 페이지는 유지됩니다."
        process.terminate()
    }

    func open(_ url: URL?) { if let url { NSWorkspace.shared.open(url) } }
    var fullButtonTitle: String {
        if status == "complete_with_errors" { return "미처리 쪽 다시 시도" }
        if ["paused","running","error"].contains(status) && scope == "full" { return "전체 변환 이어하기" }
        return "전체 변환 시작"
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var model: OCRModel?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let model = Self.model, model.running {
            model.quitting = true; model.pause(); return .terminateLater
        }
        return .terminateNow
    }
}

// Apple-inspired surfaces. All authored spacing uses a 4-point grid.
private enum OCRDesign {
    static let smallGap: CGFloat = 4
    static let itemGap: CGFloat = 12
    static let cardPadding: CGFloat = 16
    static let sectionGap: CGFloat = 24
    static let cardRadius: CGFloat = 16
    static let accent = Color(red: 0, green: 102.0 / 255, blue: 204.0 / 255)
    static let ink = Color(red: 29.0 / 255, green: 29.0 / 255, blue: 31.0 / 255)
    static let canvas = Color(red: 245.0 / 255, green: 245.0 / 255, blue: 247.0 / 255)
    static let surface = Color.white
    static let hairline = Color(red: 224.0 / 255, green: 224.0 / 255, blue: 224.0 / 255)

    static let largeTitle = font("Bold", style: .largeTitle)
    static let headline = font("SemiBold", style: .headline)
    static let body = font("Regular", style: .body)
    static let bodyMedium = font("Medium", style: .body)
    static let callout = font("Regular", style: .callout)
    static let calloutMedium = font("Medium", style: .callout)
    static let caption = font("Regular", style: .caption1)

    private static func font(_ weight: String, style: NSFont.TextStyle) -> Font {
        .custom("Pretendard-" + weight, size: NSFont.preferredFont(forTextStyle: style).pointSize)
    }

    static func registerFonts() {
        guard let directory = Bundle.main.resourceURL?.appendingPathComponent("Fonts"),
              let files = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else { return }
        for file in files where file.pathExtension == "otf" {
            CTFontManagerRegisterFontsForURL(file as CFURL, .process, nil)
        }
    }
}

private struct OCRCardStyle: GroupBoxStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(OCRDesign.cardPadding)
            .background(OCRDesign.surface, in: RoundedRectangle(cornerRadius: OCRDesign.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: OCRDesign.cardRadius).strokeBorder(OCRDesign.hairline, lineWidth: 1))
    }
}

struct ContentView: View {
    @ObservedObject var model: OCRModel
    var body: some View {
        HStack(alignment: .top, spacing: OCRDesign.sectionGap) {
            VStack(alignment: .leading, spacing: OCRDesign.sectionGap) {
                HStack(spacing: OCRDesign.itemGap) {
                    Image(systemName: "doc.text.viewfinder").font(.system(size: 36)).foregroundStyle(OCRDesign.accent)
                    VStack(alignment: .leading, spacing: OCRDesign.smallGap) {
                        Text("OCR 도우미").font(OCRDesign.largeTitle)
                        Text("원본 모습을 유지한 검색 가능한 PDF").foregroundStyle(.secondary)
                    }
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: OCRDesign.itemGap) {
                        HStack(spacing: OCRDesign.itemGap) {
                            Text("1. 파일 선택").font(OCRDesign.headline)
                            Spacer()
                            Button("PDF 선택…", action: model.chooseInput).disabled(model.running)
                        }
                        Text(model.source?.lastPathComponent ?? "선택한 파일 없음").font(OCRDesign.bodyMedium).lineLimit(2)
                        if model.pageCount > 0 { Text("전체 \(model.pageCount)쪽").font(OCRDesign.caption).foregroundStyle(.secondary) }
                        Divider()
                        HStack(spacing: OCRDesign.itemGap) {
                            VStack(alignment: .leading, spacing: OCRDesign.smallGap) {
                                Text("저장 위치").font(OCRDesign.caption).foregroundStyle(.secondary)
                                Text(model.destination?.path ?? "파일을 선택하면 자동으로 정합니다.").lineLimit(2).truncationMode(.middle).font(OCRDesign.caption)
                            }
                            Spacer()
                            Button("변경…", action: model.chooseDestination).disabled(model.running || model.source == nil)
                        }
                    }
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: OCRDesign.itemGap) {
                        Text("2. 샘플 확인").font(OCRDesign.headline)
                        Text("대표 페이지 최대 5쪽을 먼저 변환합니다.").foregroundStyle(.secondary)
                        HStack(spacing: OCRDesign.itemGap) {
                            Button(model.status == "paused" && model.scope == "sample" ? "샘플 이어하기" : "샘플 변환") { model.start("sample") }
                                .disabled(model.running || model.source == nil)
                            Button("샘플 PDF 열기") { model.open(model.sample) }.disabled(model.sample == nil)
                        }
                        Toggle("샘플을 확인했습니다", isOn: $model.approved).disabled(!model.sampleReady || model.running)
                    }
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: OCRDesign.itemGap) {
                        Text("3. 전체 변환").font(OCRDesign.headline)
                        HStack(spacing: OCRDesign.itemGap) {
                            Button(model.fullButtonTitle) { model.start("full") }
                                .buttonStyle(.borderedProminent)
                                .disabled(model.running || !model.sampleReady || !model.approved)
                            if model.running {
                                Button(model.stopping ? "중단 중…" : "중단") { model.pause() }.disabled(model.stopping)
                            }
                        }
                        if model.running {
                            if model.target > 0 { ProgressView(value: Double(model.done), total: Double(model.target)) }
                            else { ProgressView().controlSize(.small) }
                        }
                        Text(model.message).font(OCRDesign.callout).textSelection(.enabled)
                        if model.target > 0 { Text("\(model.done) / \(model.target)쪽 처리").font(OCRDesign.caption).monospacedDigit().foregroundStyle(.secondary) }
                        if !model.errorMessage.isEmpty { Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled) }
                        HStack(spacing: OCRDesign.itemGap) {
                            Button("완성 PDF 열기") { model.open(model.result) }.disabled(model.result == nil)
                            Button("검토 보고서") { model.open(model.report) }.disabled(model.report == nil)
                        }
                        if model.reviews > 0 { Text("확인 항목 \(model.reviews)개 · 표·수식은 원본과 함께 확인하세요.").font(OCRDesign.caption).foregroundStyle(.secondary) }
                    }
                }
                Text("이 Mac에서 처리합니다. 완료한 페이지는 저장돼 중단 후 이어갈 수 있습니다.")
                    .font(OCRDesign.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity)
            VStack(alignment: .leading, spacing: OCRDesign.itemGap) {
                HStack(spacing: OCRDesign.itemGap) { Text("최근 작업").font(OCRDesign.headline); Spacer(); Button { model.refreshJobs() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.borderless) }
                if model.jobs.isEmpty { Text("저장된 작업이 없습니다.").foregroundStyle(.secondary).font(OCRDesign.callout) }
                ScrollView {
                    VStack(spacing: OCRDesign.itemGap) {
                        ForEach(model.jobs.prefix(12)) { saved in
                            Button { model.load(saved) } label: {
                                VStack(alignment: .leading, spacing: OCRDesign.smallGap) {
                                    Text(saved.name).font(OCRDesign.calloutMedium).lineLimit(2)
                                    Text("\(saved.processed) / \(saved.count)쪽 기록").font(OCRDesign.caption).foregroundStyle(.secondary)
                                    Text(saved.status.hasPrefix("complete") ? "완료" : (saved.status == "sample_ready" ? "샘플 준비됨" : "불러와서 이어하기"))
                                        .font(OCRDesign.caption).foregroundStyle(OCRDesign.accent)
                                }.frame(maxWidth: .infinity, alignment: .leading).padding(OCRDesign.itemGap)
                            }.buttonStyle(.plain).background(OCRDesign.surface, in: RoundedRectangle(cornerRadius: OCRDesign.cardRadius))
                                .overlay(RoundedRectangle(cornerRadius: OCRDesign.cardRadius).strokeBorder(OCRDesign.hairline, lineWidth: 1)).disabled(model.running)
                        }
                    }
                }
            }.frame(width: 224)
        }.padding(OCRDesign.sectionGap).frame(minWidth: 864, minHeight: 712)
        .background(OCRDesign.canvas)
        .foregroundStyle(OCRDesign.ink)
        .font(OCRDesign.body)
        .tint(OCRDesign.accent)
        .groupBoxStyle(OCRCardStyle())
        .preferredColorScheme(.light)
        .onAppear { AppDelegate.model = model }
        .onOpenURL { model.select($0) }
    }
}

@main
struct OCRApplication: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = OCRModel()
    init() {
        OCRDesign.registerFonts()
    }

    var body: some Scene {
        WindowGroup { ContentView(model: model) }
            .defaultSize(width: 900, height: 784)
            .commands {
                CommandGroup(replacing: .newItem) { Button("PDF 열기…", action: model.chooseInput).keyboardShortcut("o") }
            }
    }
}
