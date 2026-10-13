# OCR 도우미

스캔 PDF를 검색 가능한 PDF로 만드는 Mac 앱입니다. OCR 결과를 AI용 텍스트와 이미지로 저장할 수 있습니다.
일반 PDF와 OCR 결과 PDF에서 선택한 페이지를 삭제하고 새 파일로 저장할 수 있습니다.
앱 사용 방법은 [사용법.txt](사용법.txt), 작업 상태와 이전 검증 기록은 [HANDOFF.md](HANDOFF.md)를 확인하세요.

## 폴더 구조

```text
OCR/
├── mac_app/              SwiftUI 화면
├── ocr_app/              처리 엔진, PDF 출력, AI용 저장
├── scripts/
│   ├── runtime/          앱이 사용하는 인식·줄 연결·검토 모듈
│   ├── native/           Apple Vision 실행 파일의 Swift 소스
│   ├── packaging/        Mac 앱 제작
│   ├── experiments/      샘플 제작, 인식 실험, 샘플 검증
│   ├── review/           품질 감사와 검토 보고서 제작
│   └── fixtures/         실험용 검토·교정 데이터
├── tests/                앱 엔진과 저장 기능 테스트
├── models/               로컬 수식 탐지·인식 모델
├── build/                컴파일 결과와 앱 제작 중간 파일
├── artifacts/
│   ├── jobs/             개발·통합 테스트의 OCR 작업 기록
│   ├── cache/            AI용 저장 테스트의 분석 캐시
│   └── tests/            테스트 결과와 검증 자료
├── output/               사용자가 저장한 최종 결과
├── tmp/                  임시 자료와 이전 실험 파일
├── .venv-app/            앱 개발용 Python 환경
├── .venv-math/           수식 실험용 Python 환경
└── OCR 도우미.app/        실행 앱
```

`artifacts/`, `build/`, `output/`, `tmp/`, 실행 앱과 가상 환경은 Git에서 제외합니다.
모델의 ONNX 파일도 Git에서 제외합니다.

## 파일을 추가할 위치

- 앱의 기능은 `mac_app/` 또는 `ocr_app/`에 추가합니다.
- 앱이 사용하는 공통 인식 도구는 `scripts/runtime/`에 둡니다.
- 문서별 실험과 수동 교정은 `scripts/experiments/`, `scripts/fixtures/`에 둡니다.
- 자동 검증 코드는 `tests/`, 생성한 검증 자료는 `artifacts/tests/`에 둡니다.
- 앱 제작 파일은 `build/`, 최종 변환 결과는 `output/`에 둡니다.

앱이 실제로 실행하며 저장하는 작업 기록은 `~/Library/Application Support/OCR Helper/jobs`에 있습니다.
이 폴더의 `artifacts/jobs/`는 개발·테스트 기록입니다.

## 개발 명령

아래 명령은 프로젝트 루트에서 실행합니다. `.venv-app/`에 필요한 의존성이 설치되어 있어야 합니다.

기능 테스트:

```sh
.venv-app/bin/python -m unittest discover -s tests -p 'test_*.py'
```

작업 프로세스의 명령 도움말:

```sh
.venv-app/bin/python ocr_app/worker.py --help
```

PDF 편집 검증 (페이지 순서, OCR 텍스트, 화면, 회전, 원본 보호):

```sh
swiftc -module-cache-path /private/tmp/ocr-swift-cache -swift-version 5 -parse-as-library -framework SwiftUI -framework AppKit -framework PDFKit mac_app/PDFEditor.swift tests/check_pdf_editor.swift -o build/check_pdf_editor
build/check_pdf_editor artifacts/tests/pdf-editor
```

앱 제작:

```sh
.venv-app/bin/python scripts/packaging/package_macos_app.py
```

앱 제작 명령은 기존 `OCR 도우미.app`을 교체합니다.
Apple Silicon Mac과 macOS 26 이상이 필요합니다.
제작 스크립트에는 이 Mac의 Poppler 경로가 들어 있습니다. 다른 Mac에서는 경로를 확인하세요.
소스만 수정하면 기존 앱에는 반영되지 않습니다.

## 이전 파일과 경로

2026-10-13에 폴더를 정리했습니다. 기존 처리 기록의 경로를 유지하려고 다음 연결을 남겼습니다.

| 이전 경로 | 실제 저장 위치 |
|---|---|
| `build/final-app-jobs` | `artifacts/jobs/final-app-jobs` |
| `build/random-app-jobs` | `artifacts/jobs/random-app-jobs` |
| `build/ai-package-cache` | `artifacts/cache/ai-package-cache` |
| `build/ai-structure-check` | `artifacts/tests/ai-structure-check` |
| `build/tests` | `artifacts/tests/regression` |

이 연결은 복사본이 아닙니다. 두 경로는 같은 파일을 가리킵니다.
이전 기록을 확인할 때에는 연결을 유지하세요.
새 스크립트는 위 실제 저장 위치를 사용하세요.

`output/`에는 현재 결과 파일이 없습니다. 인계 문서의 과거 출력 경로는 현재 파일의 존재를 보장하지 않습니다.
원본 `ADsP.pdf`도 현재 루트에 없습니다. 샘플 검증과 품질 감사 중 일부는 이전 입력·결과 자료가 필요합니다.
과거 코드 해시를 확인하는 감사는 파일 이동에 필요한 경로 변경 때문에 이전 해시와 다를 수 있습니다.
이전 검증 기록은 그대로 두고, 새 검증은 별도 기록으로 저장하세요.

용도가 확인되지 않은 `:memory:.ses`는 삭제하지 않고 `tmp/legacy/`로 옮겼습니다.
