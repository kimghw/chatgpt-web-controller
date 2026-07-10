---
name: gpt-chrome
description: ChatGPT 웹컨트롤 초기 준비 — 바탕화면 전용 바로가기(ChatGPT 전용.lnk) 생성/복구, 전용 디버그 Chrome(CDP 9223) 기동, config.json 로그인 정보로 자동 로그인까지 한 번에.
---

# gpt-chrome — ChatGPT 전용 Chrome 준비

이 프로젝트(chatgpt-web-controller)의 자동화는 **전용 프로필 디버그 Chrome**(기본 포트 9223)에만 붙는다
(Chrome 136+ 는 기본 프로필 디버깅 불가 — chatgpt.md §1). 이 스킬은 그 Chrome 을 쓸 준비를 끝까지 해준다.

## 실행 순서 (모두 프로젝트 루트에서)

1. **바로가기 생성/복구**: PowerShell 로 `setup\create_shortcut.ps1` 실행 (프로젝트 루트에서).
   - 바탕화면에 `ChatGPT 전용.lnk` 가 만들어진다 (이미 있으면 config.json 기준으로 갱신).
   - 출력 `SHORTCUT OK: ...` 확인.
2. **Chrome 기동 + 자동 로그인**: `python launch_chrome.py` 실행 (2~3분까지 걸릴 수 있음).
   - 포트가 이미 살아 있으면 기동은 건너뛰고 로그인 상태만 확인한다.
   - config.json 의 `login.email/password` 로 자동 입력. 2FA/CAPTCHA/소셜 로그인이 뜨면
     사용자에게 "열린 Chrome 창에서 직접 완료해 달라"고 안내하고 스크립트가 최대 180초 대기한다.
   - 마지막 줄 JSON `{launched, logged_in, email}` 이 결과다.
3. **결과 보고**: `logged_in: true` 면 준비 완료 — 이후 HTTP 서버(`python http_server.py`)와 CLI 스크립트를 바로 쓸 수 있다.
   `false` 면 사용자에게 그 Chrome 창에서 로그인해 달라고 안내한다 (프로필이 유지되므로 1회면 충분).

## 주의

- **사용자의 일반 Chrome 을 절대 종료하지 말 것** (chatgpt.md §3-②: 세션 쿠키 소멸 → 로그아웃).
- 포트/프로필 경로/로그인 정보는 전부 `config.json` 에서 읽는다. 없으면 `config.example.json` 을 복사해 채우라고 안내.
- ChatGPT 이용은 이 바로가기로 연 창(전용 프로필)만 쓰도록 안내 — 사람과 자동화가 같은 세션을 공유해야 추적관리가 일관된다.
