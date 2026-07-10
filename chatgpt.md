# ChatGPT 자동화 — 작업 기록 & 애로사항

`https://chatgpt.com` 을 **사람이 로그인해 둔 실제 Chrome 에 CDP attach** 해서
(1) 전체 대화 리스트 회수, (2) 새 채팅 질의·응답, (3) 기존 대화 이어쓰기, (4) 대화 결과 회수 를 자동화한 기록.

공통 뼈대(연결·진실신호·정지점·Windows 함정)는 [playwrite.md](playwrite.md) 로 빼고,
여기엔 **ChatGPT 고유 사실(셀렉터·엔드포인트·플랜 제약)과 이번에 겪은 애로사항**만 적는다.

검증 환경: Windows 11 · Python 3.12 + Playwright · Chrome 148 · 계정 `geohwa@naver.com`(김거화, Free, 대화 1,495개).
재검증: **2026-07-10 · Chrome 150 · ChatGPT 5.6 시점** — 핵심 셀렉터/엔드포인트 전부 유효 확인 (§7).

---

## 0. 한눈 요약

- ChatGPT 는 예매 사이트가 아니라 **SPA + 내부 인증 API** 유형이다. 봇 방어(Cloudflare)는 진짜 프로필이면 사실상 투명하고, **진짜 관문은 access token 인증**이다.
- 리스트/대화 내용은 **DOM 스크래핑하지 말고 내부 REST(`/backend-api/*`, Bearer)** 로 회수한다. UI 동작(새 채팅·모델선택·전송)만 DOM 셀렉터를 쓴다.
- 함정 4개에서 시간 다 썼다: ① 9222 는 Chrome 아님(svchost) ② 사람 Chrome 강제종료 → 로그아웃 ③ 쿠키만으론 `/backend-api/me` 가 익명 ④ Windows 콘솔 cp949 인코딩.

---

## 1. 작업 절차 (단계별)

| 단계 | 무엇을 | 어떻게 |
|---|---|---|
| 1. 연결 | 로그인된 디버그 Chrome 에 CDP attach | **Chrome 136+ (현재 150) 는 기본 User Data 에선 `--remote-debugging-port` 를 무시한다** → 기본 프로필 재실행 방식은 더 이상 불가. **전용 디버그 프로필**로 실행: `python launch_chrome.py` (설정은 [config.json](config.example.json)) 또는 수동으로 `chrome.exe --remote-debugging-port=9223 --user-data-dir="%USERPROFILE%\.chrome-chatgpt-debug"`. 그 창에서 ChatGPT 에 **1회 로그인**(프로필이 유지되므로 이후 재로그인 불요). `connect_over_cdp("http://localhost:9223")` 로 `contexts[0]` 재사용 |
| 2. 프로필 식별 | 어느 프로필이 로그인됐나 | 한 User Data 에 `Default`/`Profile 1` 공존. `Local State` 의 `profile.info_cache`·`last_used`, 각 프로필 `Network/Cookies` 의 `host_key` 로 식별 |
| 3. 로그인 확인 | 진짜 로그인 상태인가 | `/api/auth/session` 에 `accessToken` 있으면 로그인. (쿠키 존재나 UI 만으로 판정 금지 — §3 함정 ③) |
| 4-a. 리스트 회수 | 전체 대화 | `GET /backend-api/conversations?offset=N&limit=100&order=updated` (Bearer) 페이지네이션 → 1,495개 전부 |
| 4-b. 새 채팅 Q&A | 모델선택→질문→답변 | 새 채팅 → 모델 드롭다운 → `#prompt-textarea` 입력 → 전송 → 응답완료 폴링 → API 회수 |
| 4-c. 기존 대화 이어쓰기 | 맥락 유지 Q&A | `/c/<id>` 로 이동 → 기존 assistant 수 기록 → 입력·전송 → **새 답변 생길 때까지** 폴링 → API 회수 |
| 4-d. 결과 회수 | 특정/열린 대화 내용 | `GET /backend-api/conversation/<id>` (Bearer) → `mapping` 을 `current_node` 부터 `parent` 체인으로 순서 복원 |
| 5. 저장 | 결과 보관 | 한글 깨짐 회피 위해 **UTF-8 파일** 저장, 콘솔엔 ASCII 요약만 (§3 함정 ④) |

정지점/주의: 읽기/조회는 자유. **쓰기(메시지 전송)는 계정에 대화로 기록**되니 최소화하고 테스트 대화는 정리한다. 대화 제목 등은 개인정보 → 산출물은 로컬 보관.

---

## 2. ChatGPT 고유 사실 (live-verified)

### 2.1 엔드포인트 (내부 REST)
| 용도 | 엔드포인트 | 인증/비고 |
|---|---|---|
| 세션/토큰 | `GET /api/auth/session` | `{accessToken, user:{email}}`. accessToken 이 backend-api 인증 키 |
| 내 정보 | `GET /backend-api/me` | **쿠키만이면 익명**: `{"id":"ua-...","email":""}`. Bearer 필요 |
| 대화 목록 | `GET /backend-api/conversations?offset&limit&order=updated` | Bearer. `items[]`: `id,title,create_time,update_time(ISO8601),is_archived` |
| 대화 상세 | `GET /backend-api/conversation/<id>` | Bearer. `mapping`(노드 그래프) + `current_node` + `title`,`default_model_slug` |
| 제목 변경 | `PATCH /backend-api/conversation/<id>` | Bearer + JSON body `{"title": "..."}`. 세션 제목 추적관리용 (UI rename 과 동일 경로) |

호출 시 헤더: `Authorization: Bearer <accessToken>` (+ `credentials:'include'`). 페이지 컨텍스트 안에서 `fetch` 로 호출.

### 2.2 UI 셀렉터 (쓰기 동작용) — 2026-07-10 전항목 재확인 (§7)
| 대상 | 셀렉터 | 비고 |
|---|---|---|
| 새 채팅 | `[data-testid="create-new-chat-button"]` | 2026-07 현재 `<a>` 태그 (click 동작 동일) |
| 모델/effort 피커 | `[data-testid="model-switcher-dropdown-button"]` → 없으면 `button[class*="__composer-pill"][aria-haspopup="menu"]` | 2026-07 live: 이 계정은 testid 없이 **pill 버튼**(현재 티어 라벨 표시)만 존재. 열면 `[role="menuitemradio"]` = effort 티어 (Instant 5.5/Medium/High/Extra High/Pro), 서브메뉴 `GPT-5.6 Sol`. `chatgpt_client.list_models()/select_model()` 사용 |
| 입력창 | `#prompt-textarea` | **ProseMirror contenteditable** (textarea 아님). `fill` 말고 click→type. aria `Chat with ChatGPT` |
| 전송 | `[data-testid="send-button"]` | **입력 후에야 생성됨**. aria `Send prompt`. 2026-05 리디자인 후 A/B 에 따라 `#composer-submit-button` 만 있는 계정도 있음 → 체인: `send-button` → `#composer-submit-button` → aria → `Enter` |
| assistant 메시지 | `[data-message-author-role="assistant"]` | 마지막 노드의 `innerText`. 없으면 `section[data-turn="assistant"]` 폴백 (2026-05 턴 컨테이너 변경). **긴 대화는 가상화** — 스크롤 밖 턴이 DOM 에서 언로드되어 개수가 부정확할 수 있음 |
| 생성중 표시 | `[data-testid="stop-button"]` | aria 라벨은 자주 바뀜("Stop streaming"→"Stop answering") → 정확 매칭 금지, `aria-label*="stop" i` 부분 매칭. 사라지면 응답 완료 |
| 완료 확정 신호 | `[data-testid="copy-turn-action-button"]` | 턴이 완전히 끝나야 나타남 (2026-07-10 live 확인, aria `Copy response`) |
| + 메뉴 (파일/도구) | `[data-testid="composer-plus-btn"]` | 열리면 **role 없는 행**들 — `span:text-is("...")` 로 클릭. 항목: `Add photos & files` / `Create image` / `Web search` / `Deep research`. 선택 시 컴포저에 칩 생김 |
| 파일 첨부 input | `form input[type="file"]:not([accept])` | 파일종류 제한 없음·multiple. `set_input_files()` 로 주입, 업로드 완료는 전송버튼 활성화로 판정 |
| 이미지 답변 | 마지막 assistant 턴의 `img` | blob:/서명 URL — 페이지 컨텍스트 fetch→base64 로 로컬 저장 |

### 2.3 플랜/모델 제약 (2026-07, GPT-5.6 시점)
- **Free 플랜**: 선택 가능 모델은 **"ChatGPT (일상적인 작업에 적합)"** 하나 + "ChatGPT Plus 업그레이드" CTA(이건 `menuitem`, 선택 모델 아님). `default_model_slug` 는 `auto`. GPT-5.6 은 Free 미제공.
- **피커가 모델명 → effort 티어로 개편** (2026-06): Instant(GPT-5.5) / Medium·High·Extra High(GPT-5.6 Sol) / Pro. 기본값은 GPT-5.5 Instant + 유료 플랜은 자동 에스컬레이션(Instant→Medium) — **한 대화 안에서 턴마다 모델이 바뀔 수 있고** `data-message-model-slug` 도 턴별로 다를 수 있다.
- 모델 목록은 **추측 금지** — 드롭다운 열어 `menuitemradio` 를 live 확인 (플랜에 따라 달라짐). 익명(비로그인) 세션은 드롭다운 메뉴가 아예 비어 있음(2026-07-10 확인).

### 2.4 응답 완료 판정 (스트리밍)
- 마지막 assistant 텍스트가 **안정(2~3회 연속 동일) AND** 생성중 버튼이 사라짐 → 완료.
- 대화 생성 진실 신호: 전송 후 URL 이 `/` → `/c/<id>` 전환.

### 2.5 회수 데이터 특이점
- DOM 텍스트는 폴링 타이밍상 **부분(미완)** 일 수 있음 (예: DOM 578자 vs API 982자). **API(`mapping`) 가 완전본**.
- API `content.parts` 에는 UI 렌더 전 **내부 토큰이 그대로** 들어온다: `entity["org","Korean Register",...]`, `cite…turn0search…`, `url…`. 깔끔히 쓰려면 후처리 제거 필요.
- (2026-05~) Canvas 폐지 → 글/코드 출력이 assistant 턴 안의 **inline writing/code block** 컨테이너로 렌더됨. DOM `innerText` 로는 섞여 나올 수 있으니 역시 API 회수가 안전.

### 2.6 쓰기(전송) 경로 주의 (2026)
- **`POST /backend-api/conversation` 직접 호출은 사실상 봉쇄** — Sentinel 파이프라인(PoW proof-token, turnstile 등 최대 5종 헤더) 필요. **전송은 지금처럼 DOM 컴포저로만** 한다. GET 조회는 Sentinel 불요.
- **10,000자 초과 입력을 붙여넣기(paste)하면 자동으로 파일 첨부로 변환**됨(2026-06~). 본 프로젝트는 `keyboard.type` 이라 해당 없음 — 장문 주입을 클립보드 방식으로 바꾸지 말 것.

---

## 3. 애로사항 & 해결 (이번에 막힌 지점)

**① 포트가 열려 있다고 CDP 가 아니다.**
9222 가 TCP open 이라 붙으려 했으나 `/json/version` 이 `RemoteDisconnected`. 알고 보니 Chrome 이 아니라 Windows **`svchost`** 가 9222 점유 중. → `netstat -ano` 로 PID 확인, 비어있는 **9223** 사용. 붙기 전 `/json/version` **200** 으로 검증.

**② 사람 Chrome 을 강제 종료하면 ChatGPT 가 로그아웃된다.**
디버그 포트가 없어 `Stop-Process` 로 닫고 재실행했더니 로그아웃. **세션 스코프 인증 쿠키가 브라우저 종료 시 소멸**(디스크 쿠키는 남아도 인증 안 됨). → 사람 로그인 Chrome 을 죽이지 말 것. 부득이 재실행했으면 재로그인 각오 + **이후 그 디버그 Chrome 을 끄지 말고 유지**(다음부턴 재로그인 불요).

**③ 쿠키만으론 `/backend-api/me` 가 익명을 준다 (가장 헷갈림).**
로그인 상태인데 `fetch('/backend-api/me',{credentials:'include'})` 가 `{"id":"ua-...","email":""}`(`ua-`=anonymous). UI 는 로그인인데 API 만 로그아웃처럼 보였다. → **스크린샷(진짜 진실 신호)** 으로 UI 가 로그인임을 확인하고, ChatGPT 가 **access token(Bearer)** 으로 인증함을 파악. `/api/auth/session` 의 `accessToken` 을 Bearer 로 붙이니 정상.

**④ Windows 콘솔 cp949 인코딩.**
한글/이모지 JSON 을 `print` 하면 `UnicodeEncodeError: 'cp949'`. → 결과는 `open(..., encoding='utf-8')` 로 파일 저장, 콘솔엔 ASCII 요약만 출력.

**⑤ 다중 프로필 혼동.**
`Default`(geohwakim@gmail.com)와 `Profile 1`(KOREAN REGISTER) 둘 다 openai 쿠키 보유. 실제 ChatGPT 로그인은 **네이버 계정 `geohwa@naver.com`** (그래서 로그인 시 네이버 탭이 떴음) — 구글 프로필명과 별개. → `--profile-directory` 로 정확히 지정.

**⑥ SPA 사이드바는 전체가 안 보인다.**
난독화 Tailwind + 가상화/지연로딩으로 사이드바엔 ~40개만. → DOM 스크래핑 포기, 내부 API 페이지네이션으로 1,495개 전부 회수.

---

## 4. 스크립트 & 사용법

모두 9223 디버그 Chrome 에 attach. 결과는 UTF-8 파일로 저장(아래 산출물은 실행 시 생성).

| 스크립트 | 하는 일 | 사용 / 산출물 |
|---|---|---|
| [launch_chrome.py](launch_chrome.py) | 디버그 Chrome 기동(+선택 자동 로그인) | `python launch_chrome.py` → `{launched, logged_in, email}`. 설정: `config.json` |
| [http_server.py](http_server.py) | localhost HTTP 서버 (탭 풀 병렬, §6) | `python http_server.py` → `http://127.0.0.1:8765/docs` |
| [autostart.ps1](autostart.ps1) | 서버 Windows 자동 실행 등록/해제 | 시작프로그램에 pythonw 바로가기. 해제: `-Remove`. 로그: `server.log` |
| [fetch_chats.py](fetch_chats.py) | 전체 대화 리스트 회수 | `python fetch_chats.py` → `chats.json` (+ 별도 정리: `chats_list.md`) |
| [ask_chatgpt.py](ask_chatgpt.py) | 새 채팅: 질문→답변 회수(+세션 제목) | `python ask_chatgpt.py "질문" ["세션 제목"]` → `answer.md`/`answer.json` |
| [ask_in_existing.py](ask_in_existing.py) | 기존 대화 이어쓰기(맥락 유지) | `python ask_in_existing.py <conversation_id> "질문"` → `existing_answer.md`/`.json` |
| [fetch_result.py](fetch_result.py) | 열린/특정 대화 결과만 회수 | `python fetch_result.py [conversation_id]` → `result_conversation.md`/`.json` |

`<conversation_id>` 는 `chats.json` 의 `id` 또는 활성 탭 URL `/c/<id>` 에서 얻는다.

전제: 9223 디버그 Chrome 이 떠 있고 ChatGPT 에 로그인된 상태. 안 떠 있으면 §1 단계 1 로 기동.

---

## 5. 검증된 동작 (이번 세션)

- ✅ 대화 리스트 회수 (API 페이지네이션) — 누적 `count` 기준 1,497개 (`total` 필드는 호출마다 들쭉날쭉해 신뢰 금지, 누적 count 를 쓴다)
- ✅ 새 채팅 왕복: 마커 `AUTOMATION-TEST-OK-7391` 전송→정확히 동일 응답, DOM·API 양쪽 일치
- ✅ 기존 대화 이어쓰기: 한국선급 대화에 "위 답변 기준으로…" 후속질문 → 맥락 반영 답변(assistant 1→2개)
- ✅ 임의 질문(한국선급) 전송 후 답변 회수
- ✅ MCP 서버 5개 도구 등록 + `session_status`/`list_conversations` end-to-end 호출 확인

---

## 6. HTTP 서버 (탭 풀 병렬)

[http_server.py](http_server.py) — localhost FastAPI. 로그인된 디버그 Chrome 에 상주 연결(async Playwright)하고,
**새 채팅은 탭 풀에서 병렬 처리**한다. 탭 수는 `config.json` `server.max_tabs` (기본 **5**, 2~5 권장).
서버 시작 시 포트가 죽어 있으면 자동 기동+로그인(launch_chrome).
(과거 MCP 서버 `server.py`/`.mcp.json` 은 2026-07-10 제거 — 필요 시 git 이력 `27cba0d` 에서 복원.)

**구성 파일**
- [chatgpt_client.py](chatgpt_client.py) — 코어 로직 (HTTP 서버·CLI 스크립트가 공유)
- [http_server.py](http_server.py) — localhost FastAPI 서버
- [launch_chrome.py](launch_chrome.py) — 디버그 Chrome 기동(+선택 자동 로그인)
- `config.json` — 로컬 설정: 포트·Chrome 경로·로그인 정보·세션 제목 접두사·`server.max_tabs` (git 제외, 템플릿 [config.example.json](config.example.json))

**스킬**: [/gpt-chrome](.claude/skills/gpt-chrome/SKILL.md) — 바탕화면 전용 바로가기 생성 + Chrome 기동 + 자동 로그인.
[/gpt-ask](.claude/skills/gpt-ask/SKILL.md) — 질문 전 AskUserQuestion 으로 모델/effort 선택받고 HTTP 서버로 전송.
[/gpt-server](.claude/skills/gpt-server/SKILL.md) — 서버 시작/중지/상태 + Windows 자동 실행(autostart) 등록/해제.

**Windows 자동 실행**: 시작프로그램 폴더의 `chatgpt-web-controller.lnk` (pythonw, 무콘솔) 로 로그온 시 서버 자동 기동.
등록/해제는 [autostart.ps1](autostart.ps1) (`-Remove` 로 해제). 무콘솔 실행 로그는 `server.log`, 중복 실행은 포트 가드로 자동 차단.

**전제**: ChatGPT 에 로그인된 Chrome 이 `--remote-debugging-port=9223` 으로 떠 있어야 함 — 없으면 서버가 자동 기동.
포트/Chrome 경로/로그인 정보는 `config.json`. 포트 우선순위: env `CHATGPT_CDP_PORT` > `config.json` > 9223.

**의존성**: `pip install fastapi uvicorn playwright` (Playwright 브라우저는 CDP attach 라 별도 설치 불요).

실행: `python http_server.py` → `http://127.0.0.1:8765` (Swagger: `/docs`)

| 엔드포인트 | 하는 일 |
|---|---|
| `GET /status` | 세션 + 탭 풀 상태 |
| `POST /ask` `{prompt, title?, model?, files?, tool?, wait_timeout?}` | 새 채팅 질문 — **병렬**. `model`: 피커 선택("Instant"/"High"/"Pro"…) · `files`: 로컬 경로 목록 첨부 · `tool`: `create_image`/`web_search`/`deep_research`. 이미지 답변은 `images`(URL)+`image_files`(`downloads/` 저장) 반환. deep_research 는 `wait_timeout` 1800+ 권장 |
| `POST /ask_in` `{conversation_id, prompt}` | 기존 대화 이어쓰기 — 같은 대화는 락으로 직렬 |
| `GET /conversations` / `GET /conversation/{cid}` | 목록 / 상세 (내부 API) |
| `GET /models` / `POST /select_model` | 피커 옵션 조회 / 선택 |
| `POST /rename` `{conversation_id, title}` | 세션 제목 변경 |

주의: 계정 전체(열람+대필) 권한이므로 **host 는 127.0.0.1 밖으로 열지 말 것**. 같은 대화 동시 전송 금지(자동 직렬화됨).
검증(2026-07-10): 병렬 `/ask` 2건 동시 실행 — 각 32.6s/26.0s, 총 32.6s (동시성 확인), 제목·모델선택 정상.

---

## 7. 2026-07-10 재검증 (ChatGPT 5.6 시점)

환경: Windows 11 · Chrome 150 · 임시 프로필(익명) CDP attach + 웹 리서치 교차 검증.

**live 확인 (이 PC, 익명 세션 왕복 테스트 성공)**
- ✅ `#prompt-textarea` (ProseMirror DIV, aria `Chat with ChatGPT`, placeholder "Ask anything")
- ✅ `[data-testid="send-button"]` (입력 후 생성, aria `Send prompt`) → 전송 클릭 동작
- ✅ `[data-testid="stop-button"]` (생성 중 표시) / 완료 후 `copy-turn-action-button` 출현
- ✅ `[data-message-author-role="assistant"|"user"]` — 마커 질문에 정확 응답 회수
- ✅ `[data-testid="create-new-chat-button"]` (현재 `<a>` 태그) · `model-switcher-dropdown-button` (aria `Model selector`)
- ✅ `/api/auth/session` `/backend-api/conversations` `/backend-api/me` — 경로·응답 구조 불변 (익명이면 `ua-` id, 문서 §3-③ 그대로)
- 📌 익명 세션은 전송 후에도 URL 이 `/c/<id>` 로 안 바뀜 → `/c/<id>` 진실신호는 로그인 세션 전용

**리서치 확인 (2026 상반기 변경, 코드에 반영됨)**
- 2026-05 리디자인: 일부 A/B 버킷에서 `send-button` testid 제거 → `#composer-submit-button`. 전송은 폴백 체인으로 대응
- stop 버튼 aria 라벨 2회 변경("Stop generating"→"Stop streaming"→"Stop answering") → `aria-label*="stop" i` 부분 매칭으로 대응
- 턴 컨테이너 `article` → `section[data-turn=...]`, 긴 대화 DOM 가상화 → assistant 폴백 셀렉터 + baseline 텍스트 비교로 대응
- Chrome 136+ 기본 프로필 CDP 봉쇄 → §1 단계 1 을 전용 디버그 프로필 방식으로 교체
- 모델 피커 effort 티어 개편 / GPT-5.6(Sol) 제공 플랜 → §2.3 갱신
- raw HTTP POST 봉쇄(Sentinel), 10k+ 붙여넣기 첨부 변환 → §2.6 신설

**로그인 세션 추가 검증 (2026-07-10, geohwa@naver.com 전용 프로필)**
- ✅ 자동 로그인(launch_chrome.py, 이메일/비밀번호 자동 입력) → `logged_in: true`
- ✅ 대화 목록 39개 회수, 로그인 `ask()` 왕복 + `/c/<id>` URL 전환 정상
- ✅ `PATCH /backend-api/conversation/<id>` 제목 변경 → 목록 반영까지 확인 (세션 제목 추적관리 실동작)
- ✅ `default_model_slug` = **`gpt-5-6-pro`** (이 계정 기본값 — 5.6 시대 슬러그는 `gpt-5-6-*` 형태)
- ✅ 파일 업로드: `form input[type=file]` 주입 → 마커 파일 내용 정확 복창 (53s)
- ✅ web_search 도구: 실시간 서울 날씨 회수 (39s) / ✅ create_image: 빨간 원 이미지 3장 로컬 저장 (51s)
- ✅ deep_research 칩 선택 확인 (실행은 할당량 보호로 미실시 — 수십 분 소요, `wait_timeout` 크게)
- ✅ 모델/effort 피커: 이 계정 버킷은 `model-switcher-dropdown-button` testid **없음** →
  `button[class*="__composer-pill"][aria-haspopup="menu"]` (pill, 현재 티어 라벨 표시)가 트리거.
  메뉴 `menuitemradio` = **Instant 5.5 / Medium / High / Extra High / Pro** (+ 서브메뉴 `GPT-5.6 Sol`,
  dialog testid `composer-intelligence-picker-content`). `select_model("High")`→pill "High" 전환→"Pro" 원복 검증 완료.
