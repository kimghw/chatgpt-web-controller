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
async def _reset_composer(page):
    """이전 요청에서 남은 도구 칩 제거 (탭 풀 재사용 시 도구 상태 누수 방지).
    칩은 ProseMirror 인라인 pill — Ctrl+A→Delete 로만 제거됨 (2026-07-10 확인)."""
    try:
        await page.click("#prompt-textarea")
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def _new_chat(page):
    # 항상 fresh new-chat 으로 goto — 풀 재사용 탭이 옛 /c/<id> 대화에 머물러 있어도
    # 확실히 빈 새 채팅에서 시작 (create-new-chat 버튼은 그 상태에서 신뢰 불가, 2026-07-10 확인).
    await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_selector("#prompt-textarea", timeout=15000)
    await asyncio.sleep(0.6)
    await _reset_composer(page)   # 이전 요청의 도구 칩 누수 방지


async def _send_and_wait(page, prompt: str, wait_timeout: float, before: int, baseline: str,
                         send_ready_timeout: float = 0.0):
    """입력·전송 후 완료 폴링. bring_to_front 없음 — 백그라운드 탭에서도 동작(병렬용).

    send_ready_timeout > 0 이면 전송 전에 전송버튼 활성화 대기 (파일 업로드 완료).
    이미지 답변(텍스트 없음)도 완료로 인정.
    """
    await page.click("#prompt-textarea")
    await page.keyboard.type(prompt, delay=10)
    await asyncio.sleep(0.3)
    if send_ready_timeout > 0 and not await _wait_send_ready_on(page, send_ready_timeout):
        return "", page.url
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
            imgs = len(await page.evaluate(cc.JS_LAST_IMGS)) if cnt > before else 0
        except Exception:
            cnt, cur, gen, imgs = before, text, True, 0
        is_new = cnt > before or (cur and cur != baseline) or imgs > 0
        if is_new and (cur or imgs) and cur == text and not gen:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        text = cur
        await asyncio.sleep(1.0)
    return text, page.url


async def _select_tool_on(page, tool: str) -> dict:
    """+ 메뉴에서 도구(create_image/web_search/deep_research) 선택 → 칩 확인."""
    label = cc.TOOL_LABELS.get(tool)
    if not label:
        return {"ok": False, "reason": f"지원하지 않는 tool '{tool}' (가능: {list(cc.TOOL_LABELS)})"}
    await page.click(cc.PLUS_BTN)
    await asyncio.sleep(1.2)
    # 팝오버 메뉴 안의 항목으로 한정 (컴포저 칩 span 과 텍스트가 겹칠 수 있어 scope 필요)
    menu = page.locator('[data-radix-popper-content-wrapper]')
    loc = menu.locator(f'span:text-is("{label}")') if await menu.count() else page.locator(f'span:text-is("{label}")')
    try:
        await loc.first.click(timeout=5000)
    except Exception:
        await page.keyboard.press("Escape")
        return {"ok": False, "reason": f"+ 메뉴에서 '{label}' 못 찾음"}
    await asyncio.sleep(0.8)
    form = await page.query_selector('form[data-type="unified-composer"]') or await page.query_selector("form")
    form_text = (await form.inner_text()) if form else ""
    if label.lower() not in form_text.lower():
        return {"ok": False, "reason": f"'{label}' 칩이 컴포저에 안 생김"}
    return {"ok": True, "tool": tool}


async def _attach_files_on(page, paths: list) -> dict:
    import os
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return {"ok": False, "reason": f"파일 없음: {missing}"}
    inp = await page.query_selector('form input[type="file"]:not([accept])') or await page.query_selector('form input[type="file"]')
    if inp is None:
        return {"ok": False, "reason": "컴포저 file input 없음"}
    await inp.set_input_files(paths)
    return {"ok": True, "count": len(paths)}


async def _wait_send_ready_on(page, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in cc.SEND_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_enabled():
                    return True
            except Exception:
                pass
        await asyncio.sleep(1.0)
    return False


async def _save_images_on(page) -> tuple:
    """마지막 assistant 턴의 이미지 URL 목록과 로컬 저장 경로 목록."""
    import base64
    import os
    srcs = await page.evaluate(cc.JS_LAST_IMGS)
    if not srcs:
        return [], []
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for i, src in enumerate(srcs):
        try:
            data_url = await page.evaluate(cc.JS_FETCH_B64, src)
            head, b64 = data_url.split(",", 1)
            ext = "jpg" if "image/jpeg" in head else ("webp" if "image/webp" in head else "png")
            path = os.path.join(out_dir, f"img_{time.strftime('%y%m%d-%H%M%S')}_{i}.{ext}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64))
            saved.append(path)
        except Exception:
            pass
    return srcs, saved


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
    files: Optional[list[str]] = None  # 첨부할 로컬 파일 경로 목록
    tool: Optional[str] = None         # "create_image" | "web_search" | "deep_research"
    wait_timeout: float = 150.0        # deep_research 는 1800 이상 권장


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
    """새 채팅 질문 (탭 풀 병렬). model/tool/files 지정 시 그 탭에서 선택·첨부 후 전송."""
    page = await pool.acquire()
    try:
        await _new_chat(page)
        if body.model:
            sel = await _select_model_on(page, body.model)
            if not sel.get("ok"):
                raise HTTPException(400, f"모델 선택 실패: {sel}")
        if body.tool:
            t = await _select_tool_on(page, body.tool)
            if not t.get("ok"):
                raise HTTPException(400, f"도구 선택 실패: {t}")
        if body.files:
            a = await _attach_files_on(page, body.files)
            if not a.get("ok"):
                raise HTTPException(400, f"파일 첨부 실패: {a}")
        dom_text, url = await _send_and_wait(page, body.prompt, body.wait_timeout, before=0, baseline="",
                                             send_ready_timeout=(120.0 if body.files else 0.0))
        m = re.search(r"/c/([0-9a-f-]+)", url)
        cid = m.group(1) if m else None
        res = await _conv_result(page, body.prompt, cid, dom_text, body.title)
        img_urls, img_files = await _save_images_on(page)
        if img_urls:
            res["images"] = img_urls
            res["image_files"] = img_files
            res["ok"] = True
        return res
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


def _port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    import os
    import sys

    if sys.stdout is None or sys.stderr is None:
        # pythonw(무콘솔, 윈도우 자동 실행) — 로그를 server.log 로
        _log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.log"), "a", encoding="utf-8")
        sys.stdout = sys.stderr = _log
    if _port_in_use("127.0.0.1", PORT):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] port {PORT} already in use - exit (중복 실행 방지)")
        sys.exit(0)
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
