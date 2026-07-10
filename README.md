# chatgpt-web-controller

로그인된 **실제 Chrome** 을 CDP(DevTools Protocol)로 조종해 ChatGPT 웹(chatgpt.com)을 자동화하는 도구.
공식 API 키 없이, 내 계정 그대로 질문을 보내고 답변·대화 목록을 회수한다.

```
[클라이언트]                [이 프로젝트]                      [Chrome]
curl / python / Claude ──→ http_server.py (127.0.0.1:8765) ──→ 전용 프로필 Chrome (CDP 9223)
                            · 탭 풀 병렬 (기본 5탭)                · ChatGPT 로그인 세션
                            · 세션 제목 추적관리                    · 사람도 같은 창 사용 가능
                            · 모델/effort 선택
```

- 조회(목록/대화 내용)는 ChatGPT **내부 REST API**(Bearer)로 — 빠르고 완전함
- 쓰기(질문 전송)는 **웹 UI 조작**으로 — 봇 방어(Sentinel/PoW)에 안 걸리는 유일한 경로
- 검증 기준: **2026-07-10, ChatGPT 5.6 / Chrome 150** (상세: [chatgpt.md](chatgpt.md) §7)

---

## 1. 요구사항

| 항목 | 내용 |
|---|---|
| OS | Windows 11 (경로·스크립트가 Windows 기준) |
| Python | 3.12+ |
| 패키지 | `pip install playwright fastapi uvicorn` (Playwright 브라우저 설치 불요 — CDP attach) |
| Chrome | 일반 설치본. **단, Chrome 136+ 는 기본 프로필 디버깅이 막혀 있어 전용 프로필을 쓴다** (자동 처리됨) |
| ChatGPT 계정 | 사용할 계정 (플랜에 따라 선택 가능한 모델/effort 가 다름) |

## 2. 최초 설정 (1회)

**① 설정 파일 만들기**

```powershell
copy config.example.json config.json
```

`config.json` 을 열어 채운다 (git 에 올라가지 않음):

```jsonc
{
  "cdp_port": 9223,                          // 전용 Chrome 의 CDP 포트
  "chrome_path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "user_data_dir": "%USERPROFILE%\\.chrome-chatgpt-debug",   // 전용 프로필 위치
  "login": {
    "email": "...",                          // OpenAI 이메일/비밀번호 계정 → 자동 로그인
    "password": "..."                        // 소셜 로그인·2FA 는 창에서 직접 1회 진행
  },
  "session": {
    "title_prefix": "[AUTO] "                // 자동화가 만든 대화 제목 접두사 (추적관리, 비우면 끔)
  },
  "server": {
    "host": "127.0.0.1",                     // 보안상 절대 밖으로 열지 말 것
    "port": 8765,
    "max_tabs": 5                            // 병렬 처리 탭 수 (2~5 권장)
  }
}
```

**② 바탕화면 바로가기 + 첫 로그인**

```powershell
powershell -File create_shortcut.ps1    # 바탕화면에 "ChatGPT 전용.lnk" 생성
python launch_chrome.py                 # 전용 Chrome 기동 + 자동 로그인 (config 의 login)
```

- 이후 ChatGPT 를 직접 쓸 때도 **이 바로가기로 연 창**을 쓰면 사람과 자동화가 같은 세션을 공유한다.
- 프로필이 유지되므로 로그인은 사실상 최초 1회.

**③ (선택) Windows 시작 시 서버 자동 실행**

```powershell
powershell -File autostart.ps1          # 등록  (해제: powershell -File autostart.ps1 -Remove)
```

로그온하면 서버가 콘솔 창 없이(pythonw) 자동으로 뜨고, Chrome 기동+로그인까지 이어서 한다.
로그: 프로젝트 루트 `server.log`. 중복 실행은 포트 가드로 자동 차단.

## 3. 서버 실행과 사용

```powershell
python http_server.py                   # http://127.0.0.1:8765  (Swagger 문서: /docs)
```

서버 시작 시 전용 Chrome 이 안 떠 있으면 **자동 기동+로그인**한다.

### HTTP API

| 메서드/경로 | 설명 |
|---|---|
| `GET /status` | 로그인 세션 + 탭 풀 상태 |
| `POST /ask` | **새 채팅 질문 (병렬)**. body: `{"prompt", "title"?, "model"?, "files"?, "tool"?, "wait_timeout"?}` |
| `POST /ask_in` | 기존 대화 이어쓰기. body: `{"conversation_id", "prompt"}` — 같은 대화는 자동 직렬화 |
| `GET /conversations?limit=50` | 대화 목록 (최신순) |
| `GET /conversation/{id}` | 대화 전체 메시지 (내부 마커 정리됨) |
| `GET /models` | 선택 가능한 모델/effort live 조회 |
| `POST /select_model` | body: `{"label": "High"}` — 피커에서 선택 |
| `POST /rename` | body: `{"conversation_id", "title"}` — 세션 제목 변경 |

**예제**

```powershell
# 질문 (High effort, 제목 지정)
curl.exe -X POST http://127.0.0.1:8765/ask -H "Content-Type: application/json" `
  -d '{\"prompt\":\"파이썬 GIL 이 뭐야?\",\"model\":\"High\",\"title\":\"[공부] GIL\"}'

# 답변 이어서 질문
curl.exe -X POST http://127.0.0.1:8765/ask_in -H "Content-Type: application/json" `
  -d '{\"conversation_id\":\"<위 응답의 conversation_id>\",\"prompt\":\"예제 코드로 보여줘\"}'
```

```python
import requests
r = requests.post("http://127.0.0.1:8765/ask",
                  json={"prompt": "안녕?", "model": "Instant"}, timeout=300).json()
print(r["answer"], r["conversation_id"])

# 파일 첨부
r = requests.post("http://127.0.0.1:8765/ask", timeout=400,
                  json={"prompt": "첨부 문서를 요약해줘", "files": ["C:\\docs\\report.pdf"]}).json()

# 이미지 생성 (결과는 downloads/ 폴더에 저장됨)
r = requests.post("http://127.0.0.1:8765/ask", timeout=400,
                  json={"prompt": "노을 지는 바다 그림", "tool": "create_image", "wait_timeout": 300}).json()
print(r["image_files"])

# 웹 검색 / 딥 리서치
r = requests.post("http://127.0.0.1:8765/ask", timeout=400,
                  json={"prompt": "오늘 코스피 지수는?", "tool": "web_search"}).json()
r = requests.post("http://127.0.0.1:8765/ask", timeout=2000,
                  json={"prompt": "전고체 배터리 상용화 동향 리포트", "tool": "deep_research",
                        "wait_timeout": 1800}).json()
```

- `model` 은 `GET /models` 의 label 부분일치: 예) `"Instant"`, `"Medium"`, `"High"`, `"Extra High"`, `"Pro"`
  (플랜/시기에 따라 다름 — 항상 `/models` 로 확인). 생략하면 계정 기본값.
- `files`: 로컬 파일 경로 목록 — 종류 제한 없음(multiple). 업로드 완료를 기다렸다가 전송한다.
- `tool`: `create_image` | `web_search` | `deep_research`. 이미지 답변은 `images`(URL) + `image_files`(로컬 저장 경로) 로 반환.
  **deep research 는 수 분~수십 분** 걸리고 계정 할당량을 소모한다 — `wait_timeout` 을 1800 이상으로.
- `title` 생략 시 config 의 `session.title_prefix` 규칙으로 자동 제목 (`[AUTO] 260710-1430 질문머리…`).
- 응답 대기: 보통 수십 초, **Pro 는 몇 분**까지 걸릴 수 있다 (`wait_timeout` 기본 150초).

### Claude Code 스킬

이 폴더에서 Claude Code 를 열면 쓸 수 있는 명령:

| 스킬 | 하는 일 |
|---|---|
| `/gpt-chrome` | 바로가기 생성/복구 + 전용 Chrome 기동 + 자동 로그인 |
| `/gpt-server` `[start|stop|status|autostart|autostart off]` | 서버 관리 + Windows 자동 실행 |
| `/gpt-ask` | 모델/effort 를 물어본 뒤 질문 전송 ("GPT에게 물어봐줘" 라고만 해도 됨) |

### CLI 스크립트 (서버 없이 단발 실행)

| 스크립트 | 사용 |
|---|---|
| [ask_chatgpt.py](ask_chatgpt.py) | `python ask_chatgpt.py "질문" ["세션 제목"]` → `answer.md/.json` |
| [ask_in_existing.py](ask_in_existing.py) | `python ask_in_existing.py <대화id> "후속 질문"` |
| [fetch_chats.py](fetch_chats.py) | 전체 대화 목록 → `chats.json` |
| [fetch_result.py](fetch_result.py) | 열린/특정 대화 내용 회수 → `result_conversation.md/.json` |

## 4. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| 서버가 "포트 이미 사용 중" 로그만 남기고 종료 | 정상 (중복 실행 방지). 이미 서버가 떠 있음 — `GET /status` 확인 |
| `logged_in: false` | 전용 Chrome 창에서 직접 로그인 (2FA/CAPTCHA/소셜 로그인은 자동화 안 됨). 세션 토큰은 약 3개월 유지 |
| CDP 연결 실패 (9223 죽음) | `python launch_chrome.py` 로 재기동. **일반 Chrome 을 디버그용으로 쓰려 하지 말 것** (Chrome 136+ 제한) |
| 답변이 잘리거나 미완 | DOM 폴링 타이밍 문제 — 결과는 내부 API 가 완전본이므로 `GET /conversation/{id}` 로 재회수 |
| 전송/응답감지가 아예 안 됨 | ChatGPT UI 개편으로 셀렉터 변경 가능성. [chatgpt.md](chatgpt.md) §2.2 의 폴백 목록을 live 확인 후 갱신 |
| 한글 콘솔 깨짐 | 산출물은 UTF-8 파일로 저장됨 — 파일을 볼 것 (Windows cp949 콘솔 함정) |

**절대 하지 말 것**
- ❌ 사람이 쓰는 **일반 Chrome 을 강제 종료** — ChatGPT 세션 쿠키가 날아가 로그아웃된다
- ❌ `server.host` 를 127.0.0.1 밖으로 개방 — 이 서버는 계정 전체(열람+대필) 권한이다
- ❌ `config.json` 을 git 에 커밋 (기본 .gitignore 처리됨 — 로그인 정보 평문)

## 5. 더 읽기

- [chatgpt.md](chatgpt.md) — ChatGPT 고유 사실(셀렉터·내부 API·플랜 제약)과 검증 기록, 애로사항
- [playwrite.md](playwrite.md) — CDP attach 공통 기법 (연결·진실신호·Windows 함정)

> ⚠️ 웹 UI 자동화는 OpenAI 이용약관상 회색지대다. 개인적·저빈도 사용을 전제로 하며,
> 대량 처리나 다중 사용자 서비스가 목적이라면 공식 API 를 사용할 것.
