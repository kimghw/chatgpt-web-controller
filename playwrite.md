# 웹 자동화 가이드라인 — 케이스별 대응법

이 프로젝트의 세 워처([ktx_watcher](ktx_watcher/), [srt_watcher](srt_watcher/), [korean_air_watcher](korean_air_watcher/))가
실제로 검증한 자동화 기법을 "**어떤 상황에서 → 어떻게**" 형태로 정리한 문서.
새 사이트를 자동화하거나 기존 워처가 깨졌을 때 의사결정 순서표로 사용한다.

세 사이트는 봇 방어 기제가 **전부 다르지만**(Korail=매크로 모달, SRT=NetFunnel 대기열, Korean Air=Akamai Bot Manager)
대응 뼈대는 동일하다. 그 공통 뼈대가 이 문서의 핵심이다.

> 네 번째 실전 예시로 **ChatGPT(SPA + 내부 인증 API 유형)** 자동화 기록은 [chatgpt.md](chatgpt.md) 에 따로 있다.
> 아래 공통 기법(케이스 A 보강·토큰 인증·SPA 회수·Windows 콘솔 함정)은 거기서 일반화해 가져온 것이다.

---

## 0. 3줄 요약 (이것만 지켜도 80%)

1. **사람이 로그인해 둔 진짜 Chrome 에 CDP 로 붙는다.** 봇 우회의 대부분이 여기서 끝난다. Playwright 가 띄운 브라우저로는 막힌다.
2. **추측하지 말고 캡쳐해서 본다.** 클릭 후 기대한 반응이 없으면 즉시 CDP 로 DOM/스크린샷 확인 ([CLAUDE.md](CLAUDE.md) 룰).
3. **결제 직전에 멈춘다.** 좌석 hold 까지만 자동화하고 결제는 env 플래그로 opt-in, 또는 수동.

---

## 1. 세 사이트 한눈 비교

| 항목 | KTX/Korail | SRT | Korean Air |
|---|---|---|---|
| 봇 방어 | 매크로 차단 모달 (CODE -8002/-8003) | NetFunnel 대기열 | Akamai Bot Manager |
| 브라우저 연결 | CDP attach (launcher 가 Chrome 기동) | CDP attach (+ stealth launch fallback) | CDP attach 전용 |
| 로그인 | 워밍업 후 폼 로그인 | 회원번호 라디오 + 폼 | **필수** (Akamai 쿠키 seed, 무로그인 403) |
| 선택자 전략 | 중앙 [selectors.py](ktx_watcher/korail/selectors.py), live-verified만 | 중앙 [selectors.py](srt_watcher/srt/selectors.py), 다중후보 콤마-OR | 중앙집중 없음, JS evaluate 인라인 + shadow DOM |
| 진입 방식 | navigate + 폼 | main.do→직접URL→메뉴클릭 폴백 | **워밍업 위젯 클릭만** (직접 goto 는 홈으로 리다이렉트) |
| API 호출 | 페이지 폼 제출 | 페이지 폼 제출 (dynaPath 토큰) | XHR (fetch 아님, Akamai 가 더 관대) |
| 정지점 | 검색=후보발견 / 예약=좌석hold (결제 opt-in) | 예약=좌석hold (결제 opt-in) | 검색/알림까지 (`attempt_reservation` 미구현, 결제 수동) |

---

## 2. 케이스별 대응

### 2.1 브라우저에 어떻게 붙을까

**케이스 A — 사이트가 로그인/세션/IP 로 봇을 거른다 (대부분):**
**사람이 로그인한 실제 Chrome 에 CDP attach.** Playwright 자체 브라우저는 쓰지 않는다.
세 워처 전부 이 방식. 세션 쿠키·로그인·실제 IP 가 그대로 살아 있는 게 봇 우회의 핵심이다.

```python
browser = playwright.chromium.connect_over_cdp(cdp_url)
ctx  = browser.contexts[0]   # 사람이 로그인한 context 재사용 (새 context 만들면 비로그인)
page = ctx.pages[0]
# stop() 시: browser.close() 만 호출, ctx/page 는 건드리지 않는다 → 사람 Chrome 은 계속 살아있음
```

- Chrome 기동은 [chrome_launcher.py](ktx_watcher/chrome_launcher.py) 의 `ensure_running()`:
  `--remote-debugging-port=<port> --user-data-dir=<profile>` 로 띄우되, **이미 살아있으면(`_cdp_alive`) 재사용하고 새로 안 띄운다.**
  `shutdown_if_owned()` 는 워처가 직접 띄운 Chrome 만 종료 — 사람 세션은 절대 안 죽인다.
- **Windows IPv6 함정:** Chrome 디버그 포트는 IPv4 로 듣는데 Python 은 `localhost`→`::1`(IPv6) 로 풀어 `EADDRINUSE`.
  → `_force_ipv4_for_localhost` 가 import 시점에 `socket.getaddrinfo` 를 IPv4-only 로 monkeypatch ([chrome_launcher.py](ktx_watcher/chrome_launcher.py)).
  WebSocket URL 도 `ws://localhost:`→`ws://127.0.0.1:` 로 치환 ([client.py](ktx_watcher/korail/client.py) `_resolve_ws_url`).

**케이스 B — 무로그인 standalone (방어 약함):**
번들 Chromium 을 stealth 플래그로 직접 launch. SRT 의 fallback 경로 ([client.py](srt_watcher/srt/client.py)):
`--disable-blink-features=AutomationControlled`, `--exclude-switches=enable-automation`,
실제 설치된 Chrome 버전으로 만든 UA(`_get_chrome_version`), `timezone_id="Asia/Seoul"`, `locale="ko-KR"`,
`playwright_stealth.Stealth()` 로 `navigator.webdriver` 패치.
→ 단, **Akamai 급은 이걸로 못 뚫는다.** KE 워처가 stealth 플래그를 안 쓰고 "진짜 프로필 Chrome" 만 쓰는 이유.

**케이스 A 보강 — 사람 Chrome 에 디버그 포트가 없을 때 (로그인 SPA 일반, [chatgpt.md](chatgpt.md) 실전):**
일반 사용자 Chrome 은 `--remote-debugging-port` 없이 떠 있어 그대로는 CDP attach 가 안 된다.
닫고 `--remote-debugging-port=<port> --user-data-dir=<기존 프로필> --profile-directory=<프로필>` 로 **재실행**해야 붙는다. 단:
- **포트가 TCP open 이라고 CDP 가 아니다.** 다른 프로세스(예: Windows `svchost`)가 점유 중일 수 있다 → 붙기 전 `/json/version` 이 **200** 인지로 검증(`netstat -ano` 로 PID 확인). 점유됐으면 다른 포트.
- **사람의 로그인된 Chrome 을 강제 종료하지 말 것.** 강제종료 후 재실행하면 **세션 스코프 인증 쿠키가 소멸**해 로그아웃되는 사이트가 있다(ChatGPT 가 그랬다 — 디스크 쿠키는 남아도 인증 안 됨). 부득이 재실행했으면 재로그인 각오 + **이후 그 디버그 Chrome 을 끄지 말고 유지**(켜둔 동안만 로그인 캐시 유효).
- **다중 프로필.** 한 `User Data` 에 프로필이 여러 개면 로그인된 프로필을 `--profile-directory` 로 지정. `Local State` 의 `profile.info_cache`/`last_used`, 각 프로필 `Network/Cookies` 의 `host_key` 로 식별(복호화 불요).

### 2.2 봇 탐지 유형별 대응

먼저 **무슨 방어인지 식별**한다. 그 다음:

**Akamai Bot Manager (Korean Air 유형 — 가장 빡셈):**
- **로그인 필수.** 익명 API 호출은 403, 로그인 후 Akamai 인증 쿠키가 생기면 200 ([reserve.py](korean_air_watcher/koreanair/reserve.py) docstring). config 가 빈 자격증명을 거부.
- **직접 URL 진입 금지.** `goto('/booking/select-flight')` 는 홈으로 리다이렉트됨. **워밍업 내비게이션만 합법 진입로** — 홈 위젯(노선·날짜)을 실제로 조작하고 "검색" 클릭해서 도달 (`warm_up_select_flight`).
- **fetch 대신 XMLHttpRequest.** `withCredentials=true` XHR 가 Angular HttpClient 와 동일해 Akamai 가 fetch 보다 관대 ([search.py](korean_air_watcher/koreanair/search.py)).
- **JS click 대신 실제 마우스.** `page.mouse.click(x, y)` 를 bounding-rect 중심 좌표로. JS `.click()` 은 폴백만.

**대기열 / NetFunnel (SRT 유형):**
- 캡차가 아니라 줄서기. `window.NetFunnel_Action` 을 monkeypatch 해서 `ret.data.key` 를 가로채 `window.netfunnelKey` 에 저장 (`_inject_netfunnel_handler`), `input[name="key"]` 를 폴링 (`_wait_for_netfunnel_key`) ([reserve.py](srt_watcher/srt/reserve.py)).

**매크로/캡차 모달 (Korail 유형):**
- 사이트가 `CODE: -8002/-8003`, "매크로 등의 프로그램", "비정상적인 접속" 모달을 띄움 ([selectors.py](ktx_watcher/korail/selectors.py)).
- 탐지하면 `CaptchaDetected` 로 raise → main 루프가 **중단 대신 백오프**로 재시도 ([reserve.py](ktx_watcher/korail/reserve.py)).

**navigator.webdriver 등 핑거프린트:** 케이스 B 의 stealth + 실버전 UA. (단 케이스 A 면 애초에 불필요 — 진짜 브라우저니까.)

**토큰 인증 SPA (ChatGPT 유형 — 봇 방어 약함, 관문은 인증):** Cloudflare 등은 진짜 프로필 Chrome 이면 read GET 에 사실상 투명하다. 진짜 관문은 핑거프린트가 아니라 **access token** — §2.7 "쿠키 존재 ≠ 인증" 참고. 상세 [chatgpt.md](chatgpt.md).

**공통 휴머나이제이션 (모든 사이트에 적용):**
- `human_click`: hover → 랜덤 정지(0.35~0.85s) → 실제 `click()` → 정지. 실제 입력이라 `isTrusted=true` 유지.
- `human_type`: `fill()` 말고 `press_sequentially` 로 키당 80~170ms.
- 폼 값은 **다를 때만** 다시 채운다 (반복 재입력 패턴 회피).
- 제출 직전 긴 정지(`human_pause(3, 6)`), 폴링 간격도 jitter (`_sleep_with_jitter`).
- 모두 `*_HUMANIZE` env 로 on/off. ([client.py](ktx_watcher/korail/client.py))

### 2.3 요소 찾기 — DOM 성격별

**대원칙: 추측한 선택자 금지. live-verified 만.** [selectors.py](ktx_watcher/korail/selectors.py) 헤더가 "모두 CDP 직접 DOM probe 로 확정한 것만" — 정적 분석 추측은 오매칭이 너무 많았다.

| DOM 특성 | 전략 | 예시 |
|---|---|---|
| 안정적 `name`/`id` | CSS 직접 | `input[name='txtGoStart']`, `input#startDate` |
| 텍스트 안정, 클래스 변동 | `:has()` 구조 + `has-text` | `ul.tab_bar button:has(div.korail_logo_tab)` |
| "매진"은 빼고 "매진임박"은 포함 | negative lookahead | `has_not_text=re.compile(r"매진(?!임)")` |
| 구버전+신버전 DOM 공존 | 콤마-OR 다중후보 | `button[onclick*='selectScheduleList'], button.krds-btn:has-text('조회'), input.inquery_btn` |
| 클래스가 빌드마다 바뀜 | `page.evaluate` 로 JS 스캔 | row 파싱, datepicker 셀 |
| 식별 클래스 사라짐 | **위치 기반 폴백** | priceBox: `.gen` 없으면 `allBoxes[0]`(일반실), `[1]`(특실) |
| 난독화/동적 클래스 (SPA) | 속성 substring | `[class*=flight-list], [data-testid*=flight]` |
| Shadow DOM (웹컴포넌트) | `shadowRoot` 재귀 walk | KE `_JS_WALK` depth 14 ([reserve.py](korean_air_watcher/koreanair/reserve.py)) |
| 클래스 전부 무의미 | 텍스트/기하 앵커링 | KE 위젯: "출발지↔도착지 바꾸기" 버튼 기준 y행에서 x정렬 매핑 |
| input 역할 불명확 | placeholder 정규식 | `/도시\|공항\|city\|airport/` |
| 난독화 클래스 + 가상화/지연로딩 SPA | **DOM 스크래핑 포기, 내부 API 로 회수** | 사이드바엔 일부만 보임 → REST 페이지네이션(Bearer). ChatGPT `/backend-api/conversations` |
| SPA 동작 트리거 (클래스 무의미) | `data-testid`/`role` 앵커 | 입력창이 contenteditable(ProseMirror)이면 `fill` 말고 click→type, 전송버튼은 입력 후 등장→없으면 Enter |

### 2.4 동적 콘텐츠 / 대기 — 절대 고정 sleep 금지, 폴링

| 상황 | 처리 |
|---|---|
| SPA 네비게이션 후 결과 | `wait_for_url(패턴)` → `wait_for_load_state("networkidle")` → `wait_for_selector(결과감지)`, **각각 try/except** 로 감싸 타임아웃은 로그만 |
| 비동기 mount 모달 | 폴링 루프 (예: 8×500ms 동안 dismiss 시도) — React 가 늦게 mount 하므로 단발 호출은 놓침 |
| 결과 로딩 | content 정규식(`KE\d{4,5}`)이 뜨고 **AND** 스피너("찾고 있어요")가 사라질 때까지 폴링 |
| readonly datepicker input | `removeAttribute('readonly')` → `value=...` → `setAttribute('readonly')` → `change` 발사 (SRT `input#cal`) |
| 멀티월 slick 캐러셀 datepicker | `.slick-active` 슬라이드 헤더 읽고 `slick-next/prev` 로 목표월 정렬 후 해당 월 카드 안에서만 날짜 클릭 |
| 날짜 포맷이 점표기 ("2026. 06.") | 정규식 `/(20\d{2})\s*[.년]\s*(\d{1,2})/` |
| **CDP 에서 `page.url` 이 stale** (SPA nav 후) | `page.evaluate("location.href")` 로 다시 읽는다 (KE 필수, troubleshooting §2) |
| 폼 action 이 토큰으로 재작성됨 | 재작성 완료까지 대기 후 제출 (SRT `_wait_for_dynapath_init` — 너무 일찍 제출하면 엉뚱한 endpoint 로 POST) |
| SPA 스트리밍 응답 완료 (ChatGPT) | 마지막 응답 노드 텍스트가 **안정 AND** 생성중 버튼(`stop-button`/`중지`) 사라짐 — 2~3회 연속이면 완료 |

### 2.5 팝업 / 모달 / 다이얼로그

종류를 구분하는 게 먼저다. **별도 window(`window.open`)** 인지 **메인 페이지의 in-page 모달**인지.

| 종류 | 처리 |
|---|---|
| `window.open` 광고/공지 팝업 | (a) 클릭 전에 `window.open = function(){return null}` 주입해 원천봉쇄 (SRT), 또는 (b) `context.on("page")` 가드로 새 page 자동 닫기 + 액션마다 `dismiss_all_popups` 명시 호출 (KTX, 이벤트 핸들러 레이스 때문에 둘 다) |
| 네이티브 `alert`/`confirm` | `page.on("dialog", lambda d: d.accept())`. 검색 폼은 메시지 수집해 검증실패 탐지에 활용 |
| SweetAlert2 (DOM 모달, JS dialog 아님) | `button.swal2-confirm` 클릭. 예약 분기에선 `window.Swal.fire` 를 `{isConfirmed:true}` 반환하도록 stub (booking 이 이 반환에 의존) |
| ReactModal 공지 | "N일간 그만보기" 체크(쿠키로 24h 억제) → "창닫기" 클릭. **매크로 핸들러의 "확인" 키워드와 안 맞으므로 전용 selector 필요** (troubleshooting §1) |

### 2.6 플로우 단계와 정지점

`search → select → reserve → payment` 4단계. **결제는 항상 명시적 opt-in 또는 수동.**

- **search**: 이미 결과 페이지면 `reload()`(사람스러운 새로고침), 아니면 진입→폼채움→제출→행 파싱→시간창/좌석등급 필터 → 후보 리스트. KE 는 왕복을 편도 2회로 분해, leg 전환 시 `force_warmup`.
- **select**: 후보 정렬 후 `candidates[0]`.
- **reserve**: 행 앵커 클릭(상태별 텍스트 다름) → 등급별 액션 버튼 → 팝업 dismiss → 성공 키워드 확인. 매진행은 `status_kind`(reserve/waitlist/standing) 분류해 분기.
- **payment**: **env 플래그(`KTXA_PAYMENT_MODE`/`PAYMENT_MODE`) 기본 off.** 카드 입력은 `press_sequentially`, 동의 체크박스는 클릭 안 되면 JS `el.checked=true; dispatchEvent('change')` 폴백.
- **정지점:**
  - 검색 모드 → "후보 발견"에서 정지(알림만).
  - 예약 모드 + 결제 off → 좌석 hold(10분 타이머)에서 정지, 수동 결제.
  - 예약대기(waitlist) → 결제 강제 skip.
  - **Korean Air → `attempt_reservation` 자체가 `NotImplementedError`.** KE 보안모듈 + SMS 인증으로 자동화 신뢰 불가, 좌석 hold 이후 전부 수동.
  - **카드번호가 placeholder(`0000-...`)면 결제 강제 off** — hold 만 남기고 수동 결제 (잘못된 결제 시도가 hold 를 날리는 사고 방지).

### 2.7 검증 & 실패 처리

**진실 신호(truth signal)로 성공/실패를 판정한다. "버튼 눌렀으니 됐겠지" 금지.**

- **URL / 쿠키가 진실:** 결제 성공 = URL 이 `/myticket/list`·`/payment/complete` 로 바뀜. 안 바뀌면 body 에서 실패 키워드 긁어 `UserActionRequired` raise.
  로그인 = 특정 쿠키 존재(`JSESSIONID_ETK`, Akamai 쿠키)가 하드 요구조건.
- **쿠키 존재 ≠ 인증 (토큰 인증 앱 — 핵심 함정):** 로그인 상태인데도 쿠키만으로 API 를 부르면 익명 응답이 올 수 있다(ChatGPT `/backend-api/me` → `{"id":"ua-..."}`). 세션/토큰 엔드포인트(ChatGPT `/api/auth/session`)의 access token 을 `Authorization: Bearer` 로 붙여야 진짜 인증. **로그인 판정도 쿠키가 아니라 토큰 존재로.**
- **API ↔ UI 가 어긋나면 스크린샷이 최종 진실 신호.** (ChatGPT 인증 함정을 스크린샷으로 잡았다.)
- **Windows 콘솔 인코딩:** 한글/이모지 결과를 `print` 하면 cp949 `UnicodeEncodeError` → **UTF-8 파일 저장 + 콘솔은 ASCII 요약만.**
- **verify-then-raise:** 역/날짜 세팅 후 `input_value` 재확인, 반영 안 됐으면 `SiteLayoutChanged` raise.
- **실패하면 캡쳐:** `dump_artifacts` 가 full-page PNG + HTML 을 타임스탬프 폴더에 저장 (`runs/*_fail.png`). 빈 결과는 스크린샷 타임아웃 회피 위해 HTML 만.
- **폴링 루프 + jitter 백오프:** 후보 없음 → jitter sleep 후 재시도. 큐 폴링은 0.5s×최대180s.
- **복구 가능 vs 치명적 구분:**
  - `CaptchaDetected` / `BotGuardDetected` → 백오프 후 **재시도** (5~45s).
  - `SiteLayoutChanged` → **즉시 종료** (사람이 selector 고쳐야 함).
  - 세션 만료 (URL 에 `selectLoginForm`/`로그인폼` 감지) → 로그인 캐시 무효화 후 재로그인.

---

## 3. 새 사이트 자동화 체크리스트 (순서대로)

1. **사람이 직접 한 번 해본다.** 로그인·검색·예약을 손으로 하며 단계·URL·모달을 관찰. (자동화가 깨지면 사람 동작과 한 액션씩 비교 — [CLAUDE.md](CLAUDE.md))
2. **봇 방어 식별.** 대기열인가 / 캡차 모달인가 / Akamai 류 쿠키게이트인가 / 단순 webdriver 체크인가. → §2.2 에서 대응 선택.
3. **연결 방식 결정.** 로그인·세션 필요하면 무조건 CDP attach(케이스 A). 아니면 stealth launch(케이스 B).
4. **진입로 확인.** 직접 URL goto 가 먹히는지, 아니면 워밍업 클릭만 되는지 (Akamai 류는 후자).
5. **선택자를 CDP DOM probe 로 live-verify** 하고 [selectors.py](ktx_watcher/korail/selectors.py) 처럼 중앙화. §2.3 표로 전략 선택.
6. **대기는 폴링으로.** 고정 sleep 대신 §2.4.
7. **팝업/모달/다이얼로그 가드** 미리 등록 (§2.5).
8. **진실 신호 정의** (성공=어떤 URL/쿠키/텍스트?) 후 verify-then-raise + 실패 시 캡쳐 (§2.7).
9. **결제 직전 정지.** 플래그 opt-in, placeholder 카드면 강제 off.
10. **예외를 복구가능/치명적으로 분류**해 백오프 vs 종료 (§2.7).

---

## 4. 안티패턴 (하지 말 것)

- Playwright 가 띄운 새 브라우저/새 context 로 로그인 사이트 접근 → 비로그인·봇 탐지로 막힘.
- `stop()` 에서 사람 Chrome 의 context/page 를 닫기 → 사람 세션 파괴.
- JS `dispatchEvent`/`.click()` 을 1순위로 → `isTrusted=false` 로 탐지. 실제 click/mouse 가 1순위, JS 는 폴백.
- 추측 선택자, 전역적으로 흔한 클래스(`.gen`, `.btn`)에 의존 → 오매칭. 컨테이너 스코프 + live-verify.
- 고정 `sleep(n)` 으로 로딩 대기 → 느리고 깨짐. 폴링.
- CDP 에서 `page.url` 신뢰 (SPA nav 후 stale) → `evaluate("location.href")`.
- "버튼 눌렀으니 됐다" 가정 → 진실 신호로 검증, 안 맞으면 캡쳐해서 확인.
- 결제까지 무인 자동화 → opt-in/수동. 특히 고보안 사이트(KE)는 hold 까지만.
- 사람의 로그인된 Chrome 을 강제 종료 후 재실행 → 세션 스코프 인증 쿠키 소멸로 로그아웃. 죽이지 말고, 부득이 재실행했으면 디버그 Chrome 을 계속 살려둔다.
- 포트가 TCP open 이라고 CDP 라 단정 → `/json/version` 200 으로 검증(svchost 가 포트 점유한 사례).
- 토큰 인증 앱의 API 를 쿠키만으로 호출 / 쿠키 존재로 로그인 판정 → 익명 응답. 세션 엔드포인트의 access token 을 Bearer 로.
- 난독화 SPA 사이드바 스크래핑으로 전체 목록 수집 → 가상화로 일부만. 내부 API 페이지네이션으로.
- 한글/이모지를 Windows 콘솔에 print → cp949 에러. UTF-8 파일 + ASCII 요약.