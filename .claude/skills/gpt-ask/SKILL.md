---
name: gpt-ask
description: ChatGPT 웹으로 질문 전송 — 먼저 AskUserQuestion 으로 모델/effort 를 선택받고, HTTP 서버(127.0.0.1:8765)로 질문을 보내 답변을 회수한다 (세션 제목 추적관리 포함). 사용자가 "GPT에게 물어봐/보내줘" 류 요청을 하면 이 스킬을 따른다.
---

# gpt-ask — 모델/effort 선택받고 ChatGPT 에 질문

이 프로젝트의 ChatGPT 웹컨트롤로 질문을 보낼 때는 **반드시 아래 순서**를 따른다.
모든 호출은 HTTP 서버([http_server.py](../../../http_server.py), `http://127.0.0.1:8765`)를 거친다.

## 순서

1. **서버 확인**: `GET http://127.0.0.1:8765/status` 가 응답하는지 확인.
   - 안 뜨면 `python http_server.py` 를 백그라운드로 기동 (서버가 전용 Chrome 자동 기동+로그인까지 처리, 10초쯤 대기 후 /status 재확인).
   - `session.logged_in: false` 면 사용자에게 열린 Chrome 창에서 로그인해 달라고 안내.

2. **모델 목록 live 조회**: `GET /models` → `{current, options:[{label, checked}]}`.
   - 추측 금지 — 피커 옵션은 플랜/AB 에 따라 다르다. 2026-07 기준 예: Instant 5.5 / Medium / High / Extra High / Pro.

3. **AskUserQuestion 으로 선택받기** (질문마다):
   - question: "어떤 모델/effort 로 질문할까요?" / header: "모델·effort"
   - options 는 **2번의 live 결과로 구성**. 현재 선택된 항목(checked)을 첫 옵션 + "(Recommended)" 로.
   - 옵션이 4개를 넘으면 상위 4개만 넣고, 나머지는 question 문구에 "기타: …" 로 명시 (사용자는 Other 로 입력 가능).
   - 사용자가 제목 추적을 원할 수 있으니, 필요하면 같은 호출에 "세션 제목" 질문을 추가해도 된다
     (기본은 config.json 의 session.title_prefix 자동 제목).

4. **질문 전송**:
   - 새 채팅: `POST /ask` body `{"prompt": "...", "model": "<선택 label>", "title": "<제목|생략>"}`
     — model 은 그 탭의 피커에서 자동 선택된다. 응답 대기는 수십 초~(Pro 는 몇 분) 걸릴 수 있다.
   - 기존 대화 이어쓰기: `POST /ask_in` body `{"conversation_id": "...", "prompt": "..."}` (모델은 그 대화를 따름).
   - **파일 첨부**: 사용자가 파일을 언급하면 body 에 `"files": ["<로컬 경로>", ...]` 추가.
   - **도구**: 요청 성격에 맞으면 `"tool"` 추가 — 이미지 생성 → `create_image` (결과는 `image_files` 로 로컬 저장),
     실시간/최신 정보 → `web_search`, 심층 조사 리포트 → `deep_research` (`wait_timeout: 1800` 이상 + 사용자에게
     수십 분 걸리고 할당량을 소모함을 먼저 알릴 것).

5. **보고**: 답변, `conversation_id`, 적용된 제목(`title`/`renamed`)을 사용자에게 전달한다.
   쓰기 동작임을 유의 — 계정에 대화가 생성된다.

## 주의

- 서버는 새 채팅을 탭 풀(config server.max_tabs, 기본 5)로 **병렬** 처리하고, 같은 대화 이어쓰기는 자동 직렬화한다.
- 피커 트리거가 안 잡히면(`/models` 실패) chatgpt_client.PICKER_TRIGGERS 를 chatgpt.md §2.2/§7 를 참고해 갱신한다.
- 사용자의 일반 Chrome 은 절대 종료하지 않는다 (chatgpt.md §3-②).
