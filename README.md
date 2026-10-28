# 🎙️ 연구실 회의록 자동화 (Lab Meeting Recorder & Notion Automation)

노트북 마이크로 오프라인 회의를 녹음하면, 회의가 끝난 뒤 자동으로

1. 녹음 파일을 저장하고
2. 음성을 텍스트로 전사하고 (OpenAI)
3. AI 가 회의록과 Action Items 를 만들고 (Claude 또는 OpenAI)
4. Notion **회의록 모음 DB** 에 회의 페이지, **AI 녹취+요약본 DB** 에 정리본 페이지를 만들고
5. 두 페이지를 Relation 으로 연결합니다.

macOS(MacBook Air)와 Windows(LG Gram)에서 동작합니다. 프로그래밍을 몰라도 **더블클릭 → 브라우저 화면에서 버튼**만으로 사용할 수 있습니다.

---

## 1. 처음 한 번만: 준비물

| 준비물 | 어디서 | 비고 |
|---|---|---|
| OpenAI API 키 | https://platform.openai.com/api-keys | **음성 전사**에 사용 |
| Anthropic API 키 | https://platform.claude.com/settings/keys | **회의록 생성**에 사용 (OpenAI 하나로만 쓰고 싶으면 생략 가능, 아래 참고) |
| Notion Integration 토큰 | https://www.notion.so/profile/integrations | 워크스페이스 관리자 승인이 필요할 수 있음 (4장) |

> ⚠️ **ChatGPT Plus·Claude Pro 구독과 API 요금은 별개입니다.** 구독 중이어도 API 를 쓰려면 각 콘솔의 **Billing** 메뉴에서 결제 수단을 등록하고 소액 크레딧(예: $5~10)을 충전해야 합니다. 충전하지 않으면 "크레딧이 없습니다" 오류가 납니다.

**OpenAI 키 하나만 쓰기**: 설정에서 `SUMMARY_PROVIDER` 를 `openai` 로 바꾸면 회의록도 OpenAI 로 만듭니다(Anthropic 키 불필요). 기본값은 Claude(`claude-opus-5-5`) 입니다. 맥락이 흩어진 발언을 재구성하는 작업이라 기본값을 Claude 로 두었습니다.

### 예상 비용 (1시간 회의 기준, 2026-10-28 가격)

| 항목 | 계산 | 예상 |
|---|---|---|
| 전사 (`gpt-transcribe`) | $0.0045/분 × 약 62분 (조각 겹침 포함) | 약 $0.28 |
| 회의록 (`claude-opus-5-5`) | 입력 약 3만 토큰 × $4/백만 + 출력(생각 포함) 약 2~3만 토큰 × $20/백만 | 약 $0.5~0.8 |
| **합계** | | **약 $0.8~1.1 / 시간** |

실제 사용량은 회의마다 화면과 `meeting.json` 에 **전사 비용(예상)** 과 **회의록 비용(실제 토큰 사용량 기준)** 으로 나눠 표시됩니다. 가격표는 `config/pricing.json` 에 있으며 가격이 바뀌면 이 파일만 고치면 됩니다.

---

## 2. 설치 및 실행

### 내려받기
GitHub 페이지에서 **Code → Download ZIP** → 압축 풀기. (원하는 위치에 폴더째 두면 됩니다. 예: `문서/lab-meeting-pipeline`)

### macOS
1. 폴더 안의 **`start-mac.command`** 를 더블클릭합니다.
2. "확인되지 않은 개발자" 경고가 뜨면: **시스템 설정 → 개인정보 보호 및 보안** 맨 아래의 **"그래도 열기"** 를 누른 뒤 다시 더블클릭합니다. (처음 한 번만)
3. 처음 실행 때는 필요한 도구(uv, Python, 라이브러리)를 자동으로 설치하느라 2~5분 걸립니다.
4. 브라우저에 `http://localhost:8501` 화면이 열립니다.
5. 첫 녹음 때 "터미널이 마이크에 접근하려고 합니다" 창이 뜨면 **허용**을 누릅니다. (실수로 거부했다면: 시스템 설정 → 개인정보 보호 및 보안 → 마이크 → 터미널 켜기)

### Windows
1. 폴더 안의 **`start-windows.bat`** 를 더블클릭합니다.
2. "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**을 누릅니다. (처음 한 번만)
3. 처음 실행은 2~5분 걸립니다. 브라우저에 화면이 열립니다.
4. 마이크가 잡히지 않으면: **설정 → 개인 정보 및 보안 → 마이크 → "데스크톱 앱이 마이크에 액세스하도록 허용"** 을 켭니다.

> 검은 창(터미널/명령 프롬프트)이 프로그램 본체입니다. **회의 중에는 닫지 마세요.** 브라우저 탭은 닫아도 녹음이 계속되며, `http://localhost:8501` 로 다시 열 수 있습니다.

---

## 3. 설정 (화면의 '설정' 탭)

프로그램 화면 → **설정** 탭에 키와 Notion 정보를 입력하고 **저장**, 그다음 **🔌 연결 테스트** 를 누르세요. 값은 프로그램 폴더의 `.env` 파일에 저장됩니다(직접 메모장으로 고쳐도 됩니다. 형식은 `.env.example` 참고).

| 항목 | 필수 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | 전사용 |
| `ANTHROPIC_API_KEY` | ✅* | 회의록 생성용 (*`SUMMARY_PROVIDER=openai` 면 불필요) |
| `NOTION_API_KEY` | ✅ | Notion Integration 토큰 (`ntn_…`) |
| `NOTION_MEETING_DATABASE_ID` | ✅ | 회의록 모음 DB 링크 또는 ID |
| `NOTION_AI_NOTES_DATABASE_ID` | ✅ | AI 녹취+요약본 DB 링크 또는 ID |
| `NOTION_MEETING_RELATION_PROPERTY` | ✅ | 회의록 모음 DB 에서 AI 녹취+요약본 DB 를 가리키는 **Relation 속성의 정확한 이름** |
| `NOTION_AI_NOTES_RELATION_PROPERTY` | | Relation 이 **단방향**일 때만: 반대쪽 Relation 속성 이름 |
| `NOTION_MEETING_DATE_PROPERTY` / `NOTION_AI_NOTES_DATE_PROPERTY` | | 날짜 속성 이름 (비우면 날짜 속성은 건드리지 않음) |
| `NOTION_MEETING_PROJECT_RELATION_PROPERTY` | | 화면에서 입력한 '관련 Notion 페이지'를 연결할 Relation 속성 이름 |
| `GLOSSARY` | | 연구실 용어·사람 이름 (쉼표 구분). 전사·회의록 정확도 향상 |
| `TRANSCRIPTION_MODEL` | | 기본 `gpt-transcribe` (5장 참고) |
| `SUMMARY_PROVIDER` / `SUMMARY_MODEL` / `SUMMARY_EFFORT` | | 회의록 생성 제공자·모델·꼼꼼함 (`high` 기본) |
| `RECORDING_DIR` `TRANSCRIPT_DIR` `MINUTES_DIR` `LOG_DIR` | | 저장 위치 변경 |
| `RETENTION_DAYS` | | 원본 녹음 보존 기간. 0 = 무기한(기본). **자동 삭제는 하지 않으며** '회의 기록' 탭의 삭제 버튼을 눌렀을 때만 삭제 |

프로그램은 키가 없어도 꺼지지 않습니다. 녹음은 언제든 가능하고, 빠진 설정은 화면 상단에 안내되며, 설정 후 **이어서 처리**를 누르면 나머지가 진행됩니다.

---

## 4. Notion Integration 설정

> 🔒 이 프로그램은 브라우저에 로그인된 계정(예: 교수님 계정)의 세션·비밀번호를 **절대 사용하지 않습니다.** Notion 이 공식 제공하는 Integration 토큰으로만 접근하며, Integration 은 **연결해 준 페이지/DB 안에서만** 동작합니다.

### 4-1. Integration 만들기 (워크스페이스 관리자 필요할 수 있음)
게스트 계정은 보통 워크스페이스에 Integration 을 만들거나 연결할 권한이 없습니다. 이 경우 **워크스페이스 소유자/관리자(예: 교수님)에게 아래 과정을 요청**하세요.

1. https://www.notion.so/profile/integrations → **새 API 통합(New integration)**
2. 이름: `Lab Meeting Bot` 등 / 워크스페이스: 연구실 워크스페이스 / 유형: **Internal(내부)**
3. **Capabilities(기능)**: `Read content`, `Update content`, `Insert content` 체크 (사용자 정보는 불필요)
4. **Internal Integration Secret** (`ntn_…`) 을 복사 → 설정의 `NOTION_API_KEY` 에 붙여넣기

### 4-2. 두 데이터베이스에 Integration 연결
**회의록 모음 DB** 와 **AI 녹취+요약본 DB** 각각에서:

1. 데이터베이스를 전체 페이지로 열기
2. 오른쪽 위 **•••** → **연결(Connections)** → 만든 Integration 검색 → 추가

(상위 페이지에 연결하면 하위 DB 에도 적용됩니다.) 연결하지 않으면 "Notion 에서 대상을 찾을 수 없습니다 … Connections 에 Integration 을 추가했는지 확인하세요" 오류가 납니다.

### 4-3. 데이터베이스 ID 확인
데이터베이스를 전체 페이지로 열고 **공유 → 링크 복사**. 링크가 아래와 같다면

```
https://www.notion.so/lab/1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?v=…
```

`?v=` 앞의 32자리가 ID 입니다. **링크 전체를 그대로 붙여 넣어도** 프로그램이 ID 를 찾아냅니다(`?v=` 뒤의 보기 ID 는 무시).

### 4-4. Relation 속성 확인
- 회의록 모음 DB 에서 AI 녹취+요약본 DB 를 가리키는 Relation 속성의 **이름을 정확히** `NOTION_MEETING_RELATION_PROPERTY` 에 적습니다. (이름이 틀리면 사용 가능한 Relation 속성 목록과 함께 오류가 표시됩니다.)
- 그 Relation 이 **양방향**(속성 편집 → "AI 녹취+요약본에 표시" 켜짐)이면 반대쪽은 Notion 이 자동으로 채웁니다. 프로그램이 반대쪽 연결까지 확인합니다.
- **단방향**이면 `NOTION_AI_NOTES_RELATION_PROPERTY` 에 반대쪽 속성 이름을 적으면 프로그램이 양쪽 모두 연결합니다. 비워 두면 한쪽만 연결하고 화면에 경고를 표시합니다.

설정 탭의 **🔌 연결 테스트**가 두 DB 접근 권한, 제목 속성, Relation 방향을 모두 검사합니다.

---

## 5. 사용법

1. **회의** 탭: 회의 날짜(기본 오늘, 바꿀 수 있음), 제목(자동: `261028(수) 회의`), 참석자, 관련 Notion 페이지 URL, 마이크 선택
2. 녹음 동의 확인 체크 → **⏺ Start Meeting**
3. 화면에 빨간 **● REC 00:12:34** 와 입력 음량이 표시됩니다. 소리가 전혀 안 들어오면 마이크 권한 경고가 뜹니다.
4. 회의가 끝나면 **⏹ Stop Meeting** → 자동 처리 시작. 단계가 화면에 표시됩니다:
   `Saving audio → Transcribing (3/20) → Generating meeting notes → Creating Notion pages → Linking pages → Completed`
5. 완료되면 **📒 회의록 모음 페이지 / 🤖 AI 녹취+요약본 페이지** 링크가 표시됩니다. Notion 에서 자유롭게 수정하세요.

1시간 회의 기준 처리 시간은 대략 5~10분입니다. 처리 중에도 다음 회의를 녹음할 수 있습니다.

### Notion 에 만들어지는 내용
- **회의록 모음 DB** 페이지 `261028(수) 회의`: 회의 정보, 핵심 요약, 확정된 결정, 확정된 Action Items
- **AI 녹취+요약본 DB** 페이지 `261028 회의 AI 녹취+요약본`:
  회의 개요 → ① 핵심 요약 → ② 주요 주제와 맥락 → ③ 추론 과정 재구성 → ④ 확정된 결정 → ⑤ 제안 및 검토한 대안 → ⑥ 방향 전환·중요한 정정 → ⑦ 확정된 Action Items → ⑧ 잠정 업무·확인 필요 → ⑨ 미결 사항·추가 확인 질문 → ⑩ 관련 녹취 발췌 → AI 자체 품질 점검 → **전체 녹취록(원문)**

### AI 회의록 규칙 (요약)
`prompts/minutes_system.md` 에 전체 지침이 있습니다(직접 수정 가능).
- **원문 녹취록은 고쳐 쓰지 않고 그대로 보존**, 해석(회의록)은 별도로 작성
- 흩어진 발언을 주제별로 묶고, 생각이 바뀐 과정(명확화/구체화/조건부 수정/번복/모순)을 기록. 마지막 발언을 자동으로 최종 결정으로 보지 않음
- 결정 상태: 확정 / 제안 / 검토 중 / 보류 / 기각 / 대체됨 / 미결. "해 봐도 되겠다"는 확정 업무가 아님
- 모든 결정·업무에 근거 발언과 타임스탬프, 근거 수준(명시적 / 맥락상 함축 / 해석 불확실)을 표시
- **담당자는 녹취록에서 명시적으로 지명된 경우에만**, 기한은 언급된 경우에만. "다음 주까지"처럼 요일 없는 표현은 날짜로 바꾸지 않고 원래 표현만 기록

AI 출력 뒤에 **코드가 한 번 더 검사**합니다: 지명 발언이 녹취록에 없는 담당자 제거, 녹취록에 없는 기한 표현·회의일 이전 기한 제거, 근거 없는 '확정'을 '잠정/미결'로 낮춤, 인용문이 실제 녹취록에 있는지 표시(⚠ 표시). 제거된 항목은 "자동 검증:" 메모로 남습니다.

---

## 6. 오류가 났을 때

화면에 **어느 단계에서 실패했는지**와 **복구 방법**이 표시됩니다. 원인을 해결하고 **▶ 이어서 처리**를 누르면, 이미 끝난 단계(전사된 조각, 생성된 회의록, 만들어진 Notion 페이지)는 다시 하지 않고 실패 지점부터 계속합니다. 같은 회의를 여러 번 처리해도 Notion 페이지가 중복 생성되지 않습니다.

| 상황 | 동작 |
|---|---|
| 마이크 권한 거부 / 장치 없음 | 시작 시 안내. 무음이 5초 이상이면 경고 |
| 녹음 중 마이크 분리 | 경고 표시, 그때까지 녹음 보존 |
| 녹음 중 프로그램/노트북 꺼짐 | 2초마다 디스크에 기록하므로 직전까지 보존. 다시 실행하면 '중단됨' 으로 표시 → 이어서 처리 |
| API 키 누락 | 앱은 계속 동작, 필요한 설정 안내 |
| 네트워크 오류·요청 한도(429)·서버 오류(5xx) | 지수형 백오프로 자동 재시도 후, 실패 시 안내 |
| 크레딧 없음 | "구독과 API 요금은 별개, Billing 에서 충전" 안내 |
| AI 응답 JSON 형식 오류 | 최대 3회 재시도, 실패 시 원본 응답을 `minutes/<회의ID>.raw_attempt*.txt` 로 보존 |
| Notion 권한 부족 / DB ID 오류 / Relation 이름 오류 | 무엇을 고쳐야 하는지 안내 (연결 테스트로 미리 확인 가능) |
| Notion 페이지 생성 후 오류 | 생성된 페이지 ID 를 저장해 두고 이어서 처리. ID 저장 전에 꺼졌어도 본문의 `meeting_id` 표식으로 기존 페이지를 찾아 재사용 |

자세한 기록은 `logs/app.log` (API 키·토큰은 자동으로 `[REDACTED]` 처리).

---

## 7. 저장 구조와 개인정보

```
lab-meeting-pipeline/
├─ start-mac.command / start-windows.bat   더블클릭 실행
├─ app.py                                  화면 (Streamlit)
├─ labmeeting/                             프로그램 코드
│   ├─ recorder.py        녹음 (크래시 안전)        ├─ transcription.py  전사·분할·중복 제거
│   ├─ schema.py          회의록 JSON 스키마        ├─ minutes.py        회의록 생성·검증·렌더링
│   ├─ llm.py             Claude/OpenAI 호출 분리   ├─ notion_client.py  Notion API
│   ├─ notion_sync.py     페이지 생성·Relation      ├─ pipeline.py       단계별 처리·복구
│   ├─ settings.py  storage.py  costs.py  titles.py  logging_setup.py  keepawake.py
├─ prompts/minutes_system.md               회의록 작성 지침 (수정 가능)
├─ config/pricing.json                     API 가격표
├─ recordings/<회의ID>/recording.flac      원본 녹음(무손실) + meeting.json(처리 상태·페이지 ID)
├─ transcripts/<회의ID>.txt / .json        녹취록 원문 (UTF-8)
├─ minutes/<회의ID>.json / .md             AI 회의록
├─ logs/app.log
├─ .env.example → .env                     설정 (.env 는 Git 에 올라가지 않음)
└─ tests/                                  자동 테스트
```

`meeting.json` 에는 `meeting_id, recording_path, transcript_path, minutes_path, meeting_page_id, ai_notes_page_id, processing_status, created_at`, 비용, 처리 이력이 기록됩니다.

**개인정보·연구 보안**
- 녹음 시작 전 참석자 동의와 연구실 데이터 관리 규칙 확인을 체크하도록 되어 있습니다.
- 원본 녹음은 **이 컴퓨터에만** 저장되며 자동 삭제되지 않습니다.
- 처리 시 **녹음 오디오는 OpenAI 로, 녹취록 텍스트는 Anthropic(또는 OpenAI)으로 전송**되고, 결과는 Notion 에 저장됩니다. 각 회사의 API 데이터 정책이 적용됩니다(기본적으로 API 데이터는 모델 학습에 쓰이지 않음). 미공개 연구 정보가 외부 전송되어도 되는지 연구실 규칙을 확인하세요.
- API 요청에는 녹음/녹취록과 화면에 입력한 제목·날짜·참석자 이름·용어집만 포함됩니다.
- `recordings/ transcripts/ minutes/ logs/ .env` 는 `.gitignore` 로 Git 에 올라가지 않습니다. **이 폴더를 통째로 공유할 때는 이 폴더들과 `.env` 를 빼고 공유하세요.**

---

## 8. 전사 모델 선택과 한계

| 모델 | 타임스탬프 | 화자 구분 | 용어 힌트 | 가격/분 | 비고 |
|---|---|---|---|---|---|
| `gpt-transcribe` (기본) | 추정(≈) | ✗ | ✓ | $0.0045 | OpenAI 공식 권장 모델 |
| `whisper-1` | 구간별 정확 | ✗ | ✓ (짧게) | $0.006 | 2027-02-26 종료 예정 |
| `gpt-4o-transcribe-diarize` | 구간별 정확 | 화자 A/B | ✗ | $0.006 | 2027-02-26 종료 예정 |

- **화자 분리**: 연구실 회의 녹음 품질(노트북 마이크 1개, 원거리)에서는 정확도를 보장할 수 없어 기본 꺼짐. 켜더라도 "화자 A/B" 라벨만 붙이며 **누구인지 추측하지 않습니다.**
- 파일 크기 제한(25MB) 대응: 녹음을 3분 조각(8초 겹침)으로 나눠 전송하고, 겹친 부분의 중복 문장을 제거해 합칩니다. 앞 조각 끝부분을 다음 조각의 문맥 힌트로 넘깁니다.
- `gpt-transcribe` 는 텍스트만 돌려주므로 타임스탬프는 조각 시작 시각과 문장 위치로 계산한 **추정치(≈)** 입니다(오차 수십 초 가능).

---

## 9. 테스트

```bash
uv run --group dev pytest
```

현재 결과: **55 passed, 1 skipped** (macOS, Python 3.12). 외부 API 는 모두 가짜(mock)로 대체해 API 키 없이 실행됩니다.

| 요구 테스트 | 파일 |
|---|---|
| 1·2 제목 형식, 요일 정확성 | `tests/test_titles.py` |
| 3·4·5 담당자 null 유지 / 명시 담당자 추출 / 기한 날조 방지 | `tests/test_minutes.py` |
| 6 잘못된 JSON 처리(재시도·원본 보존) | `tests/test_minutes.py` |
| 7·8·9·10 페이지 ID 저장 / Relation 연결 / 중복 방지 / 오류 후 복구 | `tests/test_notion_sync.py`, `tests/test_pipeline.py` |
| 11 API 키 로그 비노출 | `tests/test_logging.py` |
| 12 녹음 파일 저장·크래시 복구 | `tests/test_recorder.py` |
| 전사 분할·겹침 중복 제거·이어서 전사 | `tests/test_transcription.py` |

실제 Notion 접근 확인(읽기 전용) 통합 테스트는 명시적으로 켤 때만 실행됩니다:

```bash
RUN_INTEGRATION=1 uv run --group dev pytest tests/test_integration.py
```

---

## 10. 남아 있는 제약

- **실제 OpenAI·Anthropic·Notion API 로는 아직 끝까지 실행해 보지 않았습니다**(키 미보유 상태에서 개발). 요청 형식은 2026-10-28 기준 공식 문서와 SDK(`anthropic` 2.3, `openai` 3.40)를 따랐고, Notion 동작은 가짜 서버로 검증했습니다. 첫 회의 전에 **짧은 테스트 회의(1~2분)** 로 확인하세요.
- 회의록 모델의 출력 품질(맥락 재구성, 담당자·기한 판단)은 실제 연구실 녹음으로 확인이 필요합니다. 결과가 마음에 들지 않으면 `prompts/minutes_system.md` 를 고치거나 `SUMMARY_EFFORT` 를 `xhigh` 로 올려 보세요.
- `SUMMARY_PROVIDER=openai` 의 기본 모델명(`gpt-6.1-sol`)은 가격표 기준이며 실제 호출로 검증하지 않았습니다.
- 녹음은 실제 MacBook Air 마이크로 확인했습니다. **Windows(LG Gram)는 이 환경에서 실행해 보지 못했습니다.** 처음 한 번 짧게 시험해 주세요.
- 프로그램 창(터미널)을 닫거나 노트북 덮개를 닫으면 녹음이 멈춥니다(그때까지의 녹음은 보존). 자동 잠자기는 녹음 중 막아 둡니다.
- 데이터베이스에 페이지 템플릿이 있어도 프로그램은 템플릿을 적용하지 않고 본문을 직접 작성합니다.
- 노트북 마이크 1개로 녹음하므로 멀리 앉은 사람의 발언은 인식률이 떨어질 수 있습니다. 회의 테이블 가운데에 노트북을 두세요.
