"""ChatGPT 웹컨트롤 HTTP 서버 — localhost 전용, 탭 풀 병렬 처리.

로그인된 전용 디버그 Chrome(CDP)에 async Playwright 로 상주 연결한다.
  - 새 채팅(POST /ask)은 탭 풀에서 병렬 처리 (탭 수: config server.max_tabs, 기본 5)
  - 기존 대화(POST /ask_in)는 대화별 락으로 직렬화 (같은 대화에 동시 전송 금지)
  - 서버 시작 시 포트가 죽어 있으면 launch_chrome 로직으로 자동 기동(+자동 로그인)
  - 조회(목록/대화/모델)는 내부 API(Bearer) — chatgpt_client 의 JS 를 그대로 사용

실행: python http_server.py        (기본 http://127.0.0.1:8765, Swagger: /docs)
주의: 이 서버는 계정 전체(열람+대필)를 다룬다. host 는 127.0.0.1 밖으로 열지 말 것.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import chatgpt_client as cc
import launch_chrome

CFG = cc.load_config()
SRV = CFG.get("server") or {}
HOST = SRV.get("host") or "127.0.0.1"
PORT = int(SRV.get("port") or 8765)
MAX_TABS = int(SRV.get("max_tabs") or 5)


# ---------------- 탭 풀 ----------------
class TabPool:
    """chatgpt.com 탭 N개를 빌려주고 돌려받는 풀. 탭은 필요할 때 lazy 생성."""

    def __init__(self, ctx, max_tabs: int):
        self.ctx = ctx
        self.max_tabs = max_tabs
        self.q: asyncio.Queue = asyncio.Queue()
        self.created = 0
        self._grow_lock = asyncio.Lock()

    def adopt(self, page):
        """이미 열려 있는 chatgpt 탭을 풀에 흡수."""
        if self.created < self.max_tabs:
            self.created += 1
            self.q.put_nowait(page)

    async def acquire(self):
        if self.q.empty():
            async with self._grow_lock:
                if self.created < self.max_tabs:
                    self.created += 1
                    try:
                        page = await self.ctx.new_page()
                        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
                        return page
                    except Exception:
                        self.created -= 1
                        raise
        return await self.q.get()

    def release(self, page):
        if page.is_closed():
            self.created -= 1   # 사람이 탭을 닫았으면 다음 acquire 때 새로 만든다
        else:
            self.q.put_nowait(page)


pw = browser = ctx = pool = None
conv_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------- 브라우저 조작 (chatgpt_client 의 셀렉터/JS 재사용, async) ----------------
async def _new_chat(page):
    try:
        await page.click('[data-testid="create-new-chat-button"]', timeout=5000)
    except Exception:
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_selector("#prompt-textarea", timeout=15000)
    await asyncio.sleep(0.5)


async def _send_and_wait(page, prompt: str, wait_timeout: float, before: int, baseline: str):
    """입력·전송 후 완료 폴링. bring_to_front 없음 — 백그라운드 탭에서도 동작(병렬용)."""
    await page.click("#prompt-textarea")
    await page.keyboard.type(prompt, delay=10)
    await asyncio.sleep(0.3)
    sent = False
    for sel in cc.SEND_SELECTORS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_enabled():
                await btn.click(); sent = True
                break
        except Exception:
            pass
    if not sent:
        await page.keyboard.press("Enter")

    text, stable, deadline = "", 0, time.time() + wait_timeout
    while time.time() < deadline:
        try:
            cnt = await page.evaluate(cc.JS_COUNT_ASST)
            cur = (await page.evaluate(cc.JS_LAST_ASST)) or ""
            gen = await page.evaluate(cc.JS_IS_GEN)
        except Exception:
            cnt, cur, gen = before, text, True
        is_new = cnt > before or (cur and cur != baseline)
        if is_new and cur and cur == text and not gen:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        text = cur
        await asyncio.sleep(1.0)
    return text, page.url


async def _select_model_on(page, label: str) -> dict:
    trig = None
    for sel in cc.PICKER_TRIGGERS:
        trig = await page.query_selector(sel)
        if trig:
            break
    if trig is None:
        return {"ok": False, "reason": "피커 트리거 없음"}
    await trig.click()
    await asyncio.sleep(1.2)
    items = await page.query_selector_all('[role="menuitemradio"]')
    want = label.strip().lower()
    target, labels = None, []
    for el in items:
        txt = ((await el.inner_text()) or "").strip().replace("\n", " ")
        labels.append(txt)
        if target is None and (want == txt.lower() or want in txt.lower()):
            target = el
    if target is None:
        await page.keyboard.press("Escape")
        return {"ok": False, "reason": f"'{label}' 일치 항목 없음", "options": labels}
    if (await target.get_attribute("aria-checked")) == "true":
        await page.keyboard.press("Escape")
    else:
        await target.click()
    await asyncio.sleep(0.8)
    return {"ok": True, "selected": label}


async def _conv_result(page, prompt, cid, dom_text, title: Optional[str]):
    conv = (await page.evaluate(cc.JS_GET_CONV, cid)) if cid else {"ok": False}
    res = cc._answer_result(prompt, cid, conv, dom_text, clean=True)
    final_title = title or cc._auto_title(prompt)
    if cid and final_title:
        rn = await page.evaluate(cc.JS_RENAME, [cid, final_title])
        res["renamed"] = bool(rn.get("ok"))
        if rn.get("ok"):
            res["title"] = final_title
    return res


# ---------------- FastAPI ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pw, browser, ctx, pool
    if not launch_chrome.port_alive():
        await asyncio.to_thread(launch_chrome.launch)
        await asyncio.sleep(3)
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cc.CDP)
    ctx = browser.contexts[0]
    pool = TabPool(ctx, MAX_TABS)
    for pg in ctx.pages:
        if "chatgpt.com" in pg.url:
            pool.adopt(pg)
    yield
    await browser.close()   # CDP 연결만 끊음 — Chrome 은 계속 떠 있음
    await pw.stop()


app = FastAPI(title="chatgpt-web-controller", lifespan=lifespan)


class AskBody(BaseModel):
    prompt: str
    title: Optional[str] = None
    model: Optional[str] = None        # 예: "High", "Pro", "Instant" (피커 label 부분일치)
    wait_timeout: float = 150.0


class AskInBody(BaseModel):
    conversation_id: str
    prompt: str
    wait_timeout: float = 150.0


class LabelBody(BaseModel):
    label: str


class RenameBody(BaseModel):
    conversation_id: str
    title: str


@app.get("/status")
async def status():
    page = await pool.acquire()
    try:
        sess = await page.evaluate(cc.JS_SESSION)
    finally:
        pool.release(page)
    return {"session": sess, "tabs": {"max": pool.max_tabs, "created": pool.created, "free": pool.q.qsize()}}


@app.post("/ask")
async def ask(body: AskBody):
    """새 채팅 질문 (탭 풀 병렬). model 지정 시 그 탭의 피커에서 선택 후 전송."""
    page = await pool.acquire()
    try:
        await _new_chat(page)
        if body.model:
            sel = await _select_model_on(page, body.model)
            if not sel.get("ok"):
                raise HTTPException(400, f"모델 선택 실패: {sel}")
        dom_text, url = await _send_and_wait(page, body.prompt, body.wait_timeout, before=0, baseline="")
        m = re.search(r"/c/([0-9a-f-]+)", url)
        cid = m.group(1) if m else None
        return await _conv_result(page, body.prompt, cid, dom_text, body.title)
    finally:
        pool.release(page)


@app.post("/ask_in")
async def ask_in(body: AskInBody):
    """기존 대화 이어쓰기 — 같은 대화는 락으로 직렬화."""
    async with conv_locks[body.conversation_id]:
        page = await pool.acquire()
        try:
            await page.goto(f"https://chatgpt.com/c/{body.conversation_id}",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector("#prompt-textarea", timeout=15000)
            t0 = time.time()
            while time.time() - t0 < 15:
                if await page.evaluate(cc.JS_COUNT_ASST) > 0:
                    break
                await asyncio.sleep(0.5)
            before = await page.evaluate(cc.JS_COUNT_ASST)
            baseline = (await page.evaluate(cc.JS_LAST_ASST)) or ""
            dom_text, _ = await _send_and_wait(page, body.prompt, body.wait_timeout, before, baseline)
            res = await _conv_result(page, body.prompt, body.conversation_id, dom_text, None)
            res["assistant_before"] = before
            return res
        finally:
            pool.release(page)


@app.get("/conversations")
async def conversations(limit: int = 50, include_archived: bool = False):
    page = await pool.acquire()
    try:
        res = await page.evaluate(cc.JS_LIST, limit or 0)
    finally:
        pool.release(page)
    if res.get("ok") and not include_archived:
        res["items"] = [x for x in res["items"] if not x.get("is_archived")]
        res["count"] = len(res["items"])
    return res


@app.get("/conversation/{cid}")
async def conversation(cid: str):
    page = await pool.acquire()
    try:
        res = await page.evaluate(cc.JS_GET_CONV, cid)
    finally:
        pool.release(page)
    if res.get("ok"):
        for msg in res["msgs"]:
            msg["text"] = cc.clean_text(msg["text"])
    res["conversation_id"] = cid
    return res


@app.get("/models")
async def models():
    page = await pool.acquire()
    try:
        await page.wait_for_selector("#prompt-textarea", timeout=15000)
        trig = None
        for sel in cc.PICKER_TRIGGERS:
            trig = await page.query_selector(sel)
            if trig:
                break
        if trig is None:
            return {"ok": False, "reason": "피커 트리거 없음"}
        await trig.click()
        await asyncio.sleep(1.2)
        items = await page.query_selector_all('[role="menuitemradio"]')
        options = []
        for el in items:
            txt = ((await el.inner_text()) or "").strip().replace("\n", " ")
            options.append({"label": txt, "checked": (await el.get_attribute("aria-checked")) == "true"})
        await page.keyboard.press("Escape")
    finally:
        pool.release(page)
    return {"ok": True, "current": next((o["label"] for o in options if o["checked"]), None), "options": options}


@app.post("/select_model")
async def select_model(body: LabelBody):
    page = await pool.acquire()
    try:
        await page.wait_for_selector("#prompt-textarea", timeout=15000)
        return await _select_model_on(page, body.label)
    finally:
        pool.release(page)


@app.post("/rename")
async def rename(body: RenameBody):
    page = await pool.acquire()
    try:
        res = await page.evaluate(cc.JS_RENAME, [body.conversation_id, body.title])
    finally:
        pool.release(page)
    res["conversation_id"] = body.conversation_id
    return res


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
