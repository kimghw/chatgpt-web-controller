---
name: gpt-server
description: ChatGPT HTTP 서버(127.0.0.1:8765) 관리 — 시작/중지/상태 확인, 그리고 Windows 로그온 시 자동 실행 등록/해제. 사용자가 "서버 켜줘/꺼줘/상태/자동실행" 류 요청을 하면 이 스킬을 따른다.
---

# gpt-server — HTTP 서버 관리

프로젝트 루트: `C:\Users\sscb\chatgpt-web-controller`. 인자에 따라 동작:

## (없음) 또는 `start` — 서버 시작
1. `GET http://127.0.0.1:8765/status` 확인. 이미 응답하면 "이미 실행 중" + 상태 보고하고 끝.
2. 프로젝트 루트에서 `python http_server.py` 를 **백그라운드**로 실행.
   (서버가 전용 Chrome 자동 기동 + config.json 로그인까지 처리함)
3. 10초쯤 후 `/status` 재확인해 `{session, tabs}` 를 보고. `logged_in: false` 면 열린 Chrome 창에서 로그인 안내.

## `stop` — 서버 중지
1. 8765 포트 소유 프로세스 확인: `Get-NetTCPConnection -LocalPort 8765 -State Listen | Select -Expand OwningProcess`
2. 그 PID 가 python/pythonw 인지 확인 후 `Stop-Process -Id <PID>`. **Chrome 은 끄지 않는다.**
3. 포트가 닫혔는지 재확인 후 보고.

## `status` — 상태 확인
- `GET /status` 결과(로그인 세션, 탭 풀)를 보고. 응답 없으면 "서버 꺼짐" + server.log 마지막 몇 줄 확인.

## `autostart` — Windows 시작 시 자동 실행 등록
- PowerShell 로 `setup\autostart.ps1` 실행 (프로젝트 루트에서).
- 시작프로그램 폴더에 `chatgpt-web-controller.lnk` (pythonw, 무콘솔) 가 생긴다.
- 로그는 콘솔 대신 프로젝트 루트 `server.log` 에 쌓임. 중복 실행은 서버가 스스로 방지(포트 가드).

## `autostart off` — 자동 실행 해제
- `powershell -File setup\autostart.ps1 -Remove`

## 주의
- 서버 중지 시 **사용자의 Chrome(일반/전용 모두)은 절대 종료하지 않는다** — 8765 포트의 python 프로세스만.
- 포트를 바꿨다면(config server.port) 위 URL/포트 번호를 그에 맞춘다.
