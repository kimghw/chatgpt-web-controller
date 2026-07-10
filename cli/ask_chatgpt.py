"""ChatGPT 에 질문 보내고 답변 회수 (새 채팅).

사용: python ask_chatgpt.py "질문" ["세션 제목"]   (인자 없으면 기본 질문)
세션 제목을 주면(또는 config 의 session.title_prefix 가 있으면) 생성된 대화
제목을 바꿔 목록에서 추적관리할 수 있다.
playwrite.md 원칙: CDP attach(9223), live-verified 선택자, 응답완료 폴링,
URL=/c/<id> 진실신호, 결과는 API(Bearer)로 회수, UTF-8 저장 / ASCII 요약.
"""
from __future__ import annotations
import socket, json, sys, re, time
from datetime import datetime, timezone, timedelta
_o = socket.getaddrinfo
def _f(h, p, *a, **k):
    if isinstance(h, str) and h.lower() in ("localhost", "::1"): h = "127.0.0.1"
    if len(a) >= 1: a = (socket.AF_INET,) + a[1:]
    else: k["family"] = socket.AF_INET
    return _o(h, p, *a, **k)
socket.getaddrinfo = _f
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 루트의 chatgpt_client 임포트
from playwright.sync_api import sync_playwright
import chatgpt_client as cc
from chatgpt_client import CDP  # 포트: env CHATGPT_CDP_PORT > config.json > 9223

KST = timezone(timedelta(hours=9))
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "한국선급(Korean Register)에 대해서 알려줘."
TITLE = (sys.argv[2] if len(sys.argv) > 2 else None) or cc._auto_title(PROMPT)  # 세션 제목(추적관리)

LAST_ASSISTANT = r"""() => {
  const n = document.querySelectorAll('[data-message-author-role="assistant"]');
  if (!n.length) return null;
  const el = n[n.length-1];
  return (el.innerText || el.textContent || '').trim();
}"""
IS_GENERATING = r"""() => !!document.querySelector('[data-testid="stop-button"], [data-testid="composer-stop-button"], button[aria-label*="stop" i], [aria-label*="중지"]')"""
GET_CONV = r"""
async (cid) => {
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  if (!s || !s.accessToken) return {ok:false, why:'not logged in'};
  const r = await fetch(`/backend-api/conversation/${cid}`, {credentials:'include',
              headers:{'authorization':'Bearer '+s.accessToken,'accept':'application/json'}});
  if (!r.ok) return {ok:false, status:r.status};
  const j = await r.json(); const mp = j.mapping||{};
  const chain=[]; let cur=j.current_node; const seen=new Set();
  while(cur && mp[cur] && !seen.has(cur)){ seen.add(cur); chain.push(cur); cur=mp[cur].parent; }
  chain.reverse();
  const ids = chain.length ? chain : Object.keys(mp);
  const msgs=[];
  for(const k of ids){ const m=mp[k] && mp[k].message; if(!m) continue;
    const role=m.author&&m.author.role; if(role==='system') continue;
    const parts=(m.content&&m.content.parts)||[];
    const text=parts.filter(x=>typeof x==='string').join('\n').trim();
    if(!text) continue; msgs.push({role,text,create_time:m.create_time}); }
  return {ok:true, title:j.title, model:j.default_model_slug, create_time:j.create_time, msgs};
}"""

def log(m): sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {m}\n"); sys.stderr.flush()
def fmt(ts):
    try: return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return "?"

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(CDP)
    page = next((pg for pg in b.contexts[0].pages if "chatgpt.com" in pg.url), None) or b.contexts[0].new_page()
    page.bring_to_front()

    log("새 채팅")
    try: page.click('[data-testid="create-new-chat-button"]', timeout=5000)
    except Exception: page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector('#prompt-textarea', timeout=15000)
    time.sleep(0.6)

    log(f"질문 입력: {PROMPT[:30]}...")
    page.click('#prompt-textarea')
    page.keyboard.type(PROMPT, delay=20)
    time.sleep(0.3)
    sent = False
    for sel in ('[data-testid="send-button"]', '#composer-submit-button', '[data-testid="composer-submit-button"]', 'button[aria-label="Send prompt"]'):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_enabled(): btn.click(); sent = True; break
        except Exception: pass
    if not sent: page.keyboard.press("Enter")

    log("응답 대기(폴링)")
    text, stable, deadline = "", 0, time.time() + 150
    while time.time() < deadline:
        try: cur, gen = (page.evaluate(LAST_ASSISTANT) or ""), page.evaluate(IS_GENERATING)
        except Exception: cur, gen = text, True
        if cur and cur == text and not gen:
            stable += 1
            if stable >= 3: break
        else: stable = 0
        text = cur
        time.sleep(1.0)
    log(f"응답 길이(DOM): {len(text)}")

    url = page.evaluate("() => location.href")
    cid = (re.search(r"/c/([0-9a-f-]+)", url) or [None, None])[1]
    data = page.evaluate(GET_CONV, cid) if cid else {"ok": False, "why": "no /c/ url", "url": url}
    data["conversation_id"] = cid
    data.setdefault("dom_text", text)
    if cid and TITLE:
        rn = page.evaluate(cc.JS_RENAME, [cid, TITLE])
        data["renamed"] = bool(rn.get("ok"))
        if rn.get("ok"): data["title"] = TITLE
    page.screenshot(path="_ask.png")
    b.close()

if data.get("ok"):
    asst = [m for m in data["msgs"] if m["role"] == "assistant"]
    answer = asst[-1]["text"] if asst else data.get("dom_text", "")
    lines = [f"# {data.get('title') or '(제목 없음)'}",
             f"_id: {data['conversation_id']} · model: {data.get('model')} · {fmt(data.get('create_time'))} (KST)_", "",
             f"## ❓ 질문\n{PROMPT}", "", f"## 💬 답변\n{answer}", ""]
    with open("answer.md", "w", encoding="utf-8") as f: f.write("\n".join(lines))
    with open("answer.json", "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    summary = {"ok": True, "conversation_id": data["conversation_id"], "answer_chars": len(answer),
               "messages": len(data["msgs"]), "saved": "answer.md / answer.json"}
else:
    summary = {"ok": False, **{k: v for k, v in data.items() if k != "msgs"}}
sys.stdout.write(json.dumps(summary, ensure_ascii=True) + "\n")
