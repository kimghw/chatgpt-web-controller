# CLAUDE.md — chatgpt-web-controller

로그인된 실제 Chrome(CDP)을 조종해 chatgpt.com 을 자동화하는 프로젝트.

- 전체 사용법: [README.md](README.md) · 기술 상세(셀렉터·내부 API·검증 기록): [chatgpt.md](chatgpt.md)
- 질문 전송은 HTTP 서버([http_server.py](http_server.py), `127.0.0.1:8765`)의 `POST /ask`.
  파일 첨부는 `files`, 도구는 `tool`(`create_image`/`web_search`/`deep_research`), 모델은 `model` 필드.
- **스킬**: /gpt-chrome (전용 Chrome 준비) · /gpt-server (서버 관리·자동실행) · /gpt-ask (모델 선택받고 질문)
  · **/gpt-transcribe (오디오 전사 — ChatGPT 샌드박스에서, 검증된 레시피 포함)**
- 주의: 사람이 쓰는 일반 Chrome 은 절대 종료 금지 (세션 쿠키 소멸 → 로그아웃, chatgpt.md §3-②).
