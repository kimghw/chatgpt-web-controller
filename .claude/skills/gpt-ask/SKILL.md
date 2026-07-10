---
name: gpt-ask
description: ChatGPT 웹으로 질문 전송 — 먼저 AskUserQuestion 으로 모델/effort 를 선택받고, 피커에 적용한 뒤 질문을 보내 답변을 회수한다 (세션 제목 추적관리 포함). 사용자가 "GPT에게 물어봐/보내줘" 류 요청을 하면 이 스킬을 따른다.
---

# gpt-ask — 모델/effort 선택받고 ChatGPT 에 질문

이 프로젝트의 ChatGPT 웹컨트롤로 질문을 보낼 때는 **반드시 아래 순서**를 따른다.

## 순서

1. **연결 확인**: `http://127.0.0.1:9223/json/version` 이 200 인지 확인. 죽어 있으면 /gpt-chrome 절차(또는
   `python launch_chrome.py`)로 전용 Chrome 을 먼저 띄운다.

2. **모델 목록 live 조회**: MCP 도구 `chatgpt_list_models` (또는
   `python -c "import chatgpt_client,json;print(json.dumps(chatgpt_client.list_models(),ensure_ascii=False))"`).
   - 추측 금지 — 피커 옵션은 플랜/AB 에 따라 다르다. 2026-07 기준 예: Instant(5.5) / Medium / High / Extra High / Pro.

3. **AskUserQuestion 으로 선택받기** (질문마다):
   - question: "어떤 모델/effort 로 질문할까요?" / header: "모델·effort"
   - options 는 **2번의 live 결과로 구성**. 현재 선택된 항목(checked)을 첫 옵션 + "(Recommended)" 로.
   - 옵션이 4개를 넘으면 상위 4개만 넣고, 나머지는 question 문구에 "기타: …" 로 명시 (사용자는 Other 로 입력 가능).
   - 사용자가 제목 추적을 원할 수 있으니, 필요하면 같은 호출에 "세션 제목" 질문을 추가해도 된다
     (기본은 config.json 의 session.title_prefix 자동 제목).

4. **선택 적용**: `chatgpt_select_model(label=선택값)`. `ok:false` 면 반환된 options 를 보여주고 다시 선택받는다.
   `current_pill` 로 적용 결과를 확인한다.

5. **질문 전송**: `chatgpt_ask(prompt, title=...)` (새 채팅) 또는 `chatgpt_ask_in_conversation` (이어쓰기).
   - 쓰기 동작임을 유의 — 계정에 대화가 생성된다. 답변과 conversation_id, 적용된 제목을 사용자에게 보고한다.

## 주의

- 모델 선택은 **새 채팅 컴포저의 피커**에 적용된다. 기존 대화 이어쓰기는 그 대화의 모델을 따른다.
- 피커 트리거가 안 잡히면 chatgpt_client.PICKER_TRIGGERS 를 chatgpt.md §2.2/§7 를 참고해 갱신한다.
- 사용자의 일반 Chrome 은 절대 종료하지 않는다 (chatgpt.md §3-②).
