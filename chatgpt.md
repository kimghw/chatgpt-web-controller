# ChatGPT 자동화 — 작업 기록 & 애로사항

`https://chatgpt.com` 을 **사람이 로그인해 둔 실제 Chrome 에 CDP attach** 해서
(1) 전체 대화 리스트 회수, (2) 새 채팅 질의·응답, (3) 기존 대화 이어쓰기, (4) 대화 결과 회수 를 자동화한 기록.

공통 뼈대(연결·진실신호·정지점·Windows 함정)는 [playwrite.md](playwrite.md) 로 빼고,
여기엔 **ChatGPT 고유 사실(셀렉터·엔드포인트·플랜 제약)과 이번에 겪은 애로사항**만 적는다.

검증 환경: Windows 11 · Python 3.12 + Playwright · Chrome 148 · 계정 `geohwa@naver.com`(김거화, Free, 대화 1,495개).

---

## 0. 한눈 요약

- ChatGPT 는 예매 사이트가 아니라 **SPA + 내부 인증 API** 유형이다. 봇 방어(Cloudflare)는 진짜 프로필이면 사실상 투명하고, **진짜 관문은 access token 인증**이다.
- 리스트/대화 내용은 **DOM 스크래핑하지 말고 내부 REST(`/backend-api/*`, Bearer)** 로 회수한다. UI 동작(새 채팅·모델선택·전송)만 DOM 셀렉터를 쓴다.
- 함정 4개에서 시간 다 썼다: ① 9222 는 Chrome 아님(svchost) ② 사람 Chrome 강제종료 → 로그아웃 ③ 쿠키만으론 `/backend-api/me` 가 익명 ④ Windows 콘솔 cp949 인코딩.

---

## 1. 작업 절차 (단계별)

| 단계 | 무엇을 | 어떻게 |
|---|---|---|
| 1. 연결 | 사람이 로그인한 Chrome 에 CDP attach | 일반 Chrome 은 디버그 포트가 없어 못 붙음 → 닫고 `--remote-debugging-port=9223 --user-data-dir="<기본 User Data>" --profile-directory=Default` 로 재실행. `connect_over_cdp("http://localhost:9223")` 로 `contexts[0]` 재사용 |
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

호출 시 헤더: `Authorization: Bearer <accessToken>` (+ `credentials:'include'`). 페이지 컨텍스트 안에서 `fetch` 로 호출.

### 2.2 UI 셀렉터 (쓰기 동작용)
| 대상 | 셀렉터 | 비고 |
|---|---|---|
| 새 채팅 | `[data-testid="create-new-chat-button"]` | |
| 모델 스위처 | `[data-testid="model-switcher-dropdown-button"]` | 열면 `[role="menuitemradio"]` 가 선택가능 모델 |
| 입력창 | `#prompt-textarea` | **ProseMirror contenteditable** (textarea 아님). `fill` 말고 click→type |
| 전송 | `[data-testid="send-button"]` | **입력 후에야 생성됨** → 없으면 `Enter` 폴백 |
| assistant 메시지 | `[data-message-author-role="assistant"]` | 마지막 노드의 `innerText` |
| 생성중 표시 | `[data-testid="stop-button"]` / `aria-label*="중지"` | 사라지면 응답 완료 |

### 2.3 플랜/모델 제약
- **Free 플랜**: 선택 가능 모델은 **"ChatGPT (일상적인 작업에 적합)"** 하나 + "ChatGPT Plus 업그레이드" CTA(이건 `menuitem`, 선택 모델 아님). `default_model_slug` 는 `auto`.
- 모델 목록은 **추측 금지** — 드롭다운 열어 `menuitemradio` 를 live 확인 (플랜에 따라 달라짐).

### 2.4 응답 완료 판정 (스트리밍)
- 마지막 assistant 텍스트가 **안정(2~3회 연속 동일) AND** 생성중 버튼이 사라짐 → 완료.
- 대화 생성 진실 신호: 전송 후 URL 이 `/` → `/c/<id>` 전환.

### 2.5 회수 데이터 특이점
- DOM 텍스트는 폴링 타이밍상 **부분(미완)** 일 수 있음 (예: DOM 578자 vs API 982자). **API(`mapping`) 가 완전본**.
- API `content.parts` 에는 UI 렌더 전 **내부 토큰이 그대로** 들어온다: `entity["org","Korean Register",...]`, `cite…turn0search…`, `url…`. 깔끔히 쓰려면 후처리 제거 필요.

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
| [fetch_chats.py](fetch_chats.py) | 전체 대화 리스트 회수 | `python fetch_chats.py` → `chats.json` (+ 별도 정리: `chats_list.md`) |
| [ask_chatgpt.py](ask_chatgpt.py) | 새 채팅: 모델선택→질문→답변 회수 | `python ask_chatgpt.py "질문"` → `answer.md`/`answer.json` |
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

## 6. MCP 서버

위 기능을 MCP(Model Context Protocol) 도구로 노출. 동기 Playwright 코어([chatgpt_client.py](chatgpt_client.py))를
`asyncio.to_thread` 로 감싼 FastMCP 서버([server.py](server.py)).

**구성 파일**
- [chatgpt_client.py](chatgpt_client.py) — 코어 로직 (CLI 스크립트와 공유)
- [server.py](server.py) — FastMCP stdio 서버
- [.mcp.json](.mcp.json) — 프로젝트 단위 등록(Claude Code 가 자동 감지, 승인 시 활성)

**도구**
| 도구 | 인자 | 하는 일 |
|---|---|---|
| `chatgpt_session_status` | — | 로그인/세션 상태 (`logged_in`,`email`,`expires`) |
| `chatgpt_list_conversations` | `limit=50`, `include_archived=false` | 대화 목록(최신순). `limit=0` 이면 전체 |
| `chatgpt_get_conversation` | `conversation_id=""` | 대화 전체 메시지(빈 값=활성 탭). 내부 마커 정리됨 |
| `chatgpt_ask` | `prompt`, `wait_timeout=150` | 새 채팅 질의→답변 회수 (계정에 대화 생성) |
| `chatgpt_ask_in_conversation` | `conversation_id`, `prompt`, `wait_timeout=150` | 기존 대화 이어쓰기 질의→답변 (맥락 유지) |

**전제**: ChatGPT 에 로그인된 Chrome 이 `--remote-debugging-port=9223` 으로 떠 있어야 함(없으면 §1 단계 1).
포트는 env `CHATGPT_CDP_PORT` 로 변경.

**등록**
- 프로젝트 자동 감지: 이 폴더에서 Claude Code 를 열면 `.mcp.json` 의 `chatgpt` 서버를 승인 후 사용.
- 또는 전역 등록: `claude mcp add chatgpt -- python "C:/Users/kimghw/web_chatgpt/server.py"`

**의존성**: `pip install "mcp[cli]" playwright` (Playwright 브라우저는 CDP attach 라 별도 설치 불요).

**직접 실행/디버그**: `python server.py` (stdio 대기). 동작 확인은 MCP 클라이언트로 `tools/list`·`call_tool`.
