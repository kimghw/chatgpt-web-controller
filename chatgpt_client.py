"""ChatGPT 자동화 코어 (CDP attach, 동기 Playwright).

server.py(MCP) 와 CLI 스크립트가 공유. playwrite.md / chatgpt.md 의 검증된 기법:
  - 사람이 로그인한 Chrome 에 CDP attach (port=CHATGPT_CDP_PORT, 기본 9223), contexts[0] 재사용
  - 인증은 /api/auth/session 의 accessToken 을 Bearer 로 (쿠키만으론 익명 ua-)
  - 리스트/대화는 내부 API, UI 동작만 data-testid/#prompt-textarea
  - browser.close() 만 호출 → 사람 Chrome/세션 보존
  - 결과는 호출자가 JSON 직렬화 (UTF-8). 콘솔 print 안 함.
"""
from __future__ import annotations

import os
import re
import socket
import time
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

PORT = int(os.environ.get("CHATGPT_CDP_PORT", "9223"))
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

JS_LAST_ASST = r"""() => { const n=document.querySelectorAll('[data-message-author-role="assistant"]'); return n.length ? (n[n.length-1].innerText||'').trim() : ''; }"""
JS_COUNT_ASST = r"""() => document.querySelectorAll('[data-message-author-role="assistant"]').length"""
JS_IS_GEN = r"""() => !!document.querySelector('[data-testid="stop-button"], [aria-label*="중지"], [aria-label*="Stop streaming"]')"""


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


def _send_and_wait(page, prompt: str, wait_timeout: float, before_count: int):
    """컴포저에 입력·전송 후 새 응답 완료까지 폴링. (dom_text, after_count, url) 반환."""
    page.wait_for_selector("#prompt-textarea", timeout=15000)
    page.click("#prompt-textarea")
    page.keyboard.type(prompt, delay=15)
    time.sleep(0.3)
    sent = False
    try:
        btn = page.query_selector('[data-testid="send-button"]')
        if btn and btn.is_enabled():
            btn.click(); sent = True
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
        if cnt > before_count and cur and cur == text and not gen:
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


def ask(prompt: str, wait_timeout: float = 150.0, clean: bool = True) -> dict:
    """새 채팅에서 질문 → 답변 회수. {ok, conversation_id, answer, model}."""
    p, browser, page = _with_page(navigate=None)
    try:
        page.bring_to_front()
        try:
            page.click('[data-testid="create-new-chat-button"]', timeout=5000)
        except Exception:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        before = 0
        dom_text, after, url = _send_and_wait(page, prompt, wait_timeout, before)
        m = re.search(r"/c/([0-9a-f-]+)", url)
        cid = m.group(1) if m else None
        conv = page.evaluate(JS_GET_CONV, cid) if cid else {"ok": False}
        return _answer_result(prompt, cid, conv, dom_text, clean)
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
        dom_text, after, url = _send_and_wait(page, prompt, wait_timeout, before)
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
