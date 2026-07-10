"""ChatGPT 자동화 코어 (CDP attach, 동기 Playwright).

http_server.py 와 CLI 스크립트가 공유. playwrite.md / chatgpt.md 의 검증된 기법:
  - 로그인된 디버그 Chrome 에 CDP attach (포트: env CHATGPT_CDP_PORT > config.json > 9223), contexts[0] 재사용
    (Chrome 136+ 는 기본 프로필 디버깅 불가 → launch_chrome.py 로 전용 프로필 기동)
  - 인증은 /api/auth/session 의 accessToken 을 Bearer 로 (쿠키만으론 익명 ua-)
  - 리스트/대화는 내부 API, UI 동작만 data-testid/#prompt-textarea
  - browser.close() 만 호출 → 사람 Chrome/세션 보존
  - 결과는 호출자가 JSON 직렬화 (UTF-8). 콘솔 print 안 함.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Optional

# --- localhost → IPv4 only (Windows CDP EADDRINUSE/IPv6 회피) ---
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, *a, **k):
    if isinstance(host, str) and host.lower() in ("localhost", "::1"):
        host = "127.0.0.1"
    if len(a) >= 1:
        a = (socket.AF_INET,) + a[1:]
    else:
        k["family"] = socket.AF_INET
    return _orig_getaddrinfo(host, port, *a, **k)


socket.getaddrinfo = _ipv4_only  # type: ignore[assignment]

# ---------------- 로컬 설정 (config.json — git 제외, 로그인 정보/포트/Chrome 경로) ----------------
_CONFIG_PATH = Path(__file__).with_name("config.json")


def load_config() -> dict:
    """config.json 로드 (없거나 깨졌으면 빈 dict). 템플릿은 config.example.json 참고."""
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_CFG = load_config()
# 우선순위: 환경변수 CHATGPT_CDP_PORT > config.json cdp_port > 9223
PORT = int(os.environ.get("CHATGPT_CDP_PORT") or _CFG.get("cdp_port") or 9223)
CDP = f"http://localhost:{PORT}"

# ---------------- 본문 토큰 정리 (ChatGPT 내부 마크업 제거) ----------------
_PUA = re.compile("[-]")  # ChatGPT 내부 마커 구분자(private-use)
_ENTITY = re.compile(r'entity\["[^"]*","([^"]*)"(?:,"[^"]*")*\]')


def clean_text(s: Optional[str]) -> str:
    """API content.parts 의 entity[...]/cite…/PUA 마커를 읽기 좋게 정리."""
    if not s:
        return s or ""
    s = _ENTITY.sub(r"\1", s)          # entity["org","NAME",...] → NAME
    s = _PUA.sub("", s)                # private-use 구분자 제거
    s = re.sub(r"cite(turn\w+)+", "", s)
    s = re.sub(r"\bturn\d+\w+", "", s)
    s = re.sub(r"navlist\w*", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# ---------------- JS 평가기 ----------------
JS_SESSION = r"""async () => {
  const r = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await r.json().catch(()=>({}));
  return { logged_in: !!(s && s.accessToken), email: s && s.user && s.user.email || null, expires: s && s.expires || null };
}"""

JS_LIST = r"""async (maxItems) => {
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  if (!s || !s.accessToken) return { ok:false, reason:'NOT_LOGGED_IN' };
  const auth = { 'authorization':'Bearer '+s.accessToken, 'accept':'application/json' };
  const items = []; const limit = 100; let offset = 0; let total = null;
  for (let g=0; g<300; g++) {
    const r = await fetch(`/backend-api/conversations?offset=${offset}&limit=${limit}&order=updated`, {credentials:'include', headers:auth});
    if (!r.ok) return { ok:false, reason:'HTTP '+r.status, items };
    const j = await r.json(); total = j.total;
    for (const it of (j.items||[])) items.push({ id:it.id, title:it.title, create_time:it.create_time, update_time:it.update_time, is_archived:it.is_archived });
    offset += (j.items||[]).length;
    if ((j.items||[]).length < limit || (total!=null && offset>=total)) break;
    if (maxItems && items.length >= maxItems) break;
  }
  return { ok:true, email:s.user&&s.user.email, total, count:items.length, items: maxItems ? items.slice(0,maxItems) : items };
}"""

JS_RENAME = r"""async (args) => {
  const cid = args[0], title = args[1];
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  if (!s || !s.accessToken) return { ok:false, reason:'NOT_LOGGED_IN' };
  const r = await fetch(`/backend-api/conversation/${cid}`, {
    method:'PATCH', credentials:'include',
    headers:{'authorization':'Bearer '+s.accessToken, 'content-type':'application/json', 'accept':'application/json'},
    body: JSON.stringify({ title })
  });
  if (!r.ok) return { ok:false, reason:'HTTP '+r.status };
  return { ok:true, title };
}"""

JS_GET_CONV = r"""async (cid) => {
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  if (!s || !s.accessToken) return { ok:false, reason:'NOT_LOGGED_IN' };
  const r = await fetch(`/backend-api/conversation/${cid}`, {credentials:'include', headers:{'authorization':'Bearer '+s.accessToken,'accept':'application/json'}});
  if (!r.ok) return { ok:false, reason:'HTTP '+r.status };
  const j = await r.json(); const mp = j.mapping||{};
  const chain = []; let cur = j.current_node; const seen = new Set();
  while (cur && mp[cur] && !seen.has(cur)) { seen.add(cur); chain.push(cur); cur = mp[cur].parent; }
  chain.reverse();
  const ids = chain.length ? chain : Object.keys(mp); const msgs = [];
  for (const k of ids) { const m = mp[k] && mp[k].message; if (!m) continue;
    const role = m.author && m.author.role; if (role==='system') continue;
    const parts = (m.content && m.content.parts)||[]; const text = parts.filter(x=>typeof x==='string').join('\n').trim();
    if (!text) continue; msgs.push({ role, text, create_time:m.create_time }); }
  return { ok:true, title:j.title, model:j.default_model_slug, create_time:j.create_time, msgs };
}"""

# 전송 버튼 후보 (우선순위순). 2026-07-10 live: send-button(aria "Send prompt") 확인.
# 2026-05 리디자인 후 A/B 버킷에 따라 #composer-submit-button 만 있는 계정도 있음 → 체인 + Enter 폴백.
SEND_SELECTORS = (
    '[data-testid="send-button"]',
    '#composer-submit-button',
    '[data-testid="composer-submit-button"]',
    '[data-testid="composer-send-button"]',
    'button[aria-label="Send prompt"]',
    'button[aria-label="프롬프트 보내기"]',
)

# assistant 노드: data-message-author-role 이 1차(2026-07 live 확인).
# 2026-05 이후 턴 컨테이너가 section[data-turn="assistant"] 로 바뀌어 폴백으로만 사용
# (콤마로 합치면 부모+자식이 중복 카운트되므로 금지).
JS_LAST_ASST = r"""() => { let n=document.querySelectorAll('[data-message-author-role="assistant"]'); if(!n.length) n=document.querySelectorAll('section[data-turn="assistant"]'); return n.length ? (n[n.length-1].innerText||'').trim() : ''; }"""
JS_COUNT_ASST = r"""() => { let n=document.querySelectorAll('[data-message-author-role="assistant"]'); if(!n.length) n=document.querySelectorAll('section[data-turn="assistant"]'); return n.length; }"""
# 생성중: aria 라벨 문구가 자주 바뀌므로("Stop streaming"→"Stop answering") 부분·대소문자 무시 매칭
JS_IS_GEN = r"""() => !!document.querySelector('[data-testid="stop-button"], [data-testid="composer-stop-button"], button[aria-label*="stop" i], [aria-label*="중지"]')"""


# ---------------- 연결 헬퍼 ----------------
def _with_page(navigate: Optional[str] = None):
    """playwright 연결 + chatgpt 페이지 확보. (playwright, browser, page) 반환."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = next((pg for pg in ctx.pages if "chatgpt.com" in pg.url), None)
    if page is None:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        navigate = navigate or "https://chatgpt.com/"
    if navigate:
        page.goto(navigate, wait_until="domcontentloaded", timeout=45000)
    return p, browser, page


def _close(p, browser):
    try:
        browser.close()   # CDP 연결만 끊음 (사람 Chrome 안 닫힘)
    finally:
        p.stop()


def _send_and_wait(page, prompt: str, wait_timeout: float, before_count: int, baseline_text: str = ""):
    """컴포저에 입력·전송 후 새 응답 완료까지 폴링. (dom_text, after_count, url) 반환.

    새 답변 판정: assistant 수 증가 OR 마지막 assistant 텍스트가 전송 전(baseline)과 달라짐.
    (긴 대화는 DOM 가상화로 스크롤 밖 턴이 언로드돼 개수만으론 놓칠 수 있음 — 2026-07 확인)
    """
    page.wait_for_selector("#prompt-textarea", timeout=15000)
    page.click("#prompt-textarea")
    page.keyboard.type(prompt, delay=15)
    time.sleep(0.3)
    sent = False
    for sel in SEND_SELECTORS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_enabled():
                btn.click(); sent = True
                break
        except Exception:
            pass
    if not sent:
        page.keyboard.press("Enter")

    text, stable, deadline = "", 0, time.time() + wait_timeout
    while time.time() < deadline:
        try:
            cnt = page.evaluate(JS_COUNT_ASST); cur = page.evaluate(JS_LAST_ASST) or ""; gen = page.evaluate(JS_IS_GEN)
        except Exception:
            cnt, cur, gen = before_count, text, True
        is_new = cnt > before_count or (cur and cur != baseline_text)
        if is_new and cur and cur == text and not gen:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        text = cur
        time.sleep(1.0)
    return text, page.evaluate(JS_COUNT_ASST), page.evaluate("() => location.href")


# ---------------- 공개 API ----------------
def check_session() -> dict:
    """로그인/세션 상태. {logged_in, email, expires}."""
    p, browser, page = _with_page()
    try:
        return page.evaluate(JS_SESSION)
    finally:
        _close(p, browser)


def list_conversations(limit: Optional[int] = None, include_archived: bool = True) -> dict:
    """대화 리스트(최신순). limit=None 이면 전체. {ok, total, count, items[]}."""
    p, browser, page = _with_page()
    try:
        res = page.evaluate(JS_LIST, limit or 0)
        if res.get("ok") and not include_archived:
            res["items"] = [x for x in res["items"] if not x.get("is_archived")]
            res["count"] = len(res["items"])
        return res
    finally:
        _close(p, browser)


def get_conversation(conversation_id: Optional[str] = None, clean: bool = True) -> dict:
    """대화 내용 회수. conversation_id 없으면 활성 탭의 /c/<id>. {ok, title, model, msgs[]}."""
    p, browser, page = _with_page(navigate=None)
    try:
        cid = conversation_id
        if not cid:
            url = page.evaluate("() => location.href")
            m = re.search(r"/c/([0-9a-f-]+)", url)
            if not m:
                return {"ok": False, "reason": "활성 탭이 /c/<id> 대화가 아님", "url": url}
            cid = m.group(1)
        res = page.evaluate(JS_GET_CONV, cid)
        res["conversation_id"] = cid
        if res.get("ok") and clean:
            for msg in res["msgs"]:
                msg["text"] = clean_text(msg["text"])
        return res
    finally:
        _close(p, browser)


def _auto_title(prompt: str) -> Optional[str]:
    """config session.title_prefix 가 있으면 '접두사 YYMMDD-HHMM 질문머리' 자동 제목 생성."""
    prefix = (_CFG.get("session") or {}).get("title_prefix") or ""
    if not prefix:
        return None
    return f"{prefix}{time.strftime('%y%m%d-%H%M')} {prompt[:30]}".strip()


def ask(prompt: str, wait_timeout: float = 150.0, clean: bool = True, title: Optional[str] = None) -> dict:
    """새 채팅에서 질문 → 답변 회수. {ok, conversation_id, answer, model}.

    title 을 주면(또는 config session.title_prefix 가 있으면) 생성된 대화의 제목을
    바꿔 목록에서 추적관리할 수 있게 한다.
    """
    p, browser, page = _with_page(navigate=None)
    try:
        page.bring_to_front()
        try:
            page.click('[data-testid="create-new-chat-button"]', timeout=5000)
        except Exception:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        before = 0
        dom_text, after, url = _send_and_wait(page, prompt, wait_timeout, before, baseline_text="")
        m = re.search(r"/c/([0-9a-f-]+)", url)
        cid = m.group(1) if m else None
        conv = page.evaluate(JS_GET_CONV, cid) if cid else {"ok": False}
        res = _answer_result(prompt, cid, conv, dom_text, clean)
        final_title = title or _auto_title(prompt)
        if cid and final_title:
            rn = page.evaluate(JS_RENAME, [cid, final_title])
            res["renamed"] = bool(rn.get("ok"))
            if rn.get("ok"):
                res["title"] = final_title
        return res
    finally:
        _close(p, browser)


def rename_conversation(conversation_id: str, title: str) -> dict:
    """대화 제목 변경 (추적관리용). {ok, title, conversation_id}."""
    p, browser, page = _with_page()
    try:
        res = page.evaluate(JS_RENAME, [conversation_id, title])
        res["conversation_id"] = conversation_id
        return res
    finally:
        _close(p, browser)


# 모델/effort 피커 트리거 (우선순위순). 2026-07-10 live: 이 계정은 testid 없이 pill 버튼만 존재
PICKER_TRIGGERS = (
    '[data-testid="model-switcher-dropdown-button"]',
    'button[class*="__composer-pill"][aria-haspopup="menu"]',
)


def _open_picker(page):
    """피커 열고 (trigger, menuitemradio 목록) 반환. 못 열면 (None, [])."""
    page.wait_for_selector("#prompt-textarea", timeout=15000)
    trig = None
    for sel in PICKER_TRIGGERS:
        trig = page.query_selector(sel)
        if trig:
            break
    if not trig:
        return None, []
    trig.click()
    time.sleep(1.2)
    return trig, page.query_selector_all('[role="menuitemradio"]')


def list_models() -> dict:
    """모델/effort 피커 옵션 live 조회 (추측 금지 원칙).

    2026-07 피커는 effort 티어 목록 (예: Instant(5.5)/Medium/High/Extra High/Pro).
    Returns: {ok, current, options:[{label, checked}]}
    """
    p, browser, page = _with_page()
    try:
        page.bring_to_front()
        trig, items = _open_picker(page)
        if trig is None:
            return {"ok": False, "reason": "피커 트리거 없음 (PICKER_TRIGGERS 갱신 필요)"}
        options = []
        for el in items:
            txt = (el.inner_text() or "").strip().replace("\n", " ")
            options.append({"label": txt, "checked": el.get_attribute("aria-checked") == "true"})
        page.keyboard.press("Escape")
        current = next((o["label"] for o in options if o["checked"]), None)
        return {"ok": True, "current": current, "options": options}
    finally:
        _close(p, browser)


def select_model(label: str) -> dict:
    """피커에서 label(부분일치·대소문자 무시)의 모델/effort 를 선택.

    Returns: {ok, selected, current_pill} / 실패 시 {ok:False, reason, options}
    """
    p, browser, page = _with_page()
    try:
        page.bring_to_front()
        trig, items = _open_picker(page)
        if trig is None:
            return {"ok": False, "reason": "피커 트리거 없음"}
        want = label.strip().lower()
        target, labels = None, []
        for el in items:
            txt = (el.inner_text() or "").strip().replace("\n", " ")
            labels.append(txt)
            if target is None and (want == txt.lower() or want in txt.lower()):
                target = el
        if target is None:
            page.keyboard.press("Escape")
            return {"ok": False, "reason": f"'{label}' 일치 항목 없음", "options": labels}
        already = target.get_attribute("aria-checked") == "true"
        if already:
            page.keyboard.press("Escape")
        else:
            target.click()
        time.sleep(0.8)
        pill = page.query_selector(PICKER_TRIGGERS[1]) or page.query_selector(PICKER_TRIGGERS[0])
        pill_text = (pill.inner_text() or "").strip() if pill else None
        return {"ok": True, "selected": label, "already_selected": already, "current_pill": pill_text}
    finally:
        _close(p, browser)


def ask_in_conversation(conversation_id: str, prompt: str, wait_timeout: float = 150.0, clean: bool = True) -> dict:
    """기존 대화(/c/<id>)에 이어서 질문 → 답변 회수 (맥락 유지)."""
    p, browser, page = _with_page(navigate=f"https://chatgpt.com/c/{conversation_id}")
    try:
        page.bring_to_front()
        page.wait_for_selector("#prompt-textarea", timeout=15000)
        t0 = time.time()
        while time.time() - t0 < 15:
            if page.evaluate(JS_COUNT_ASST) > 0:
                break
            time.sleep(0.5)
        before = page.evaluate(JS_COUNT_ASST)
        baseline = page.evaluate(JS_LAST_ASST) or ""
        dom_text, after, url = _send_and_wait(page, prompt, wait_timeout, before, baseline_text=baseline)
        conv = page.evaluate(JS_GET_CONV, conversation_id)
        res = _answer_result(prompt, conversation_id, conv, dom_text, clean)
        res["assistant_before_after"] = [before, after]
        return res
    finally:
        _close(p, browser)


def _answer_result(prompt, cid, conv, dom_text, clean) -> dict:
    if conv.get("ok"):
        asst = [m for m in conv["msgs"] if m["role"] == "assistant"]
        answer = asst[-1]["text"] if asst else dom_text
    else:
        answer = dom_text
    if clean:
        answer = clean_text(answer)
    return {
        "ok": bool(answer),
        "conversation_id": cid,
        "prompt": prompt,
        "answer": answer,
        "title": conv.get("title") if conv.get("ok") else None,
        "model": conv.get("model") if conv.get("ok") else None,
        "total_messages": len(conv.get("msgs", [])) if conv.get("ok") else None,
    }
