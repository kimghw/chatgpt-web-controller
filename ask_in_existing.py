"""기존 대화창(/c/<id>)에 이어서 질의·응답 + 답변 회수.

사용: python ask_in_existing.py <conversation_id> "후속 질문"
- 해당 대화로 이동(맥락 유지) → 기존 assistant 수 기록 → 입력/전송
- 새 assistant 메시지가 생기고 안정될 때까지 폴링 → API 로 최신 답변 회수
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
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
CID = sys.argv[1] if len(sys.argv) > 1 else None
PROMPT = sys.argv[2] if len(sys.argv) > 2 else "위 답변을 한 문장으로 요약해줘."
if not CID:
    print(json.dumps({"ok": False, "why": "conversation_id 인자 필요"})); sys.exit(1)

COUNT_ASST = r"""() => document.querySelectorAll('[data-message-author-role="assistant"]').length"""
LAST_ASST = r"""() => { const n=document.querySelectorAll('[data-message-author-role="assistant"]');
  return n.length ? (n[n.length-1].innerText||'').trim() : ''; }"""
IS_GEN = r"""() => !!document.querySelector('[data-testid="stop-button"], [aria-label*="중지"], [aria-label*="Stop streaming"]')"""
GET_CONV = r"""
async (cid) => {
  const sR=await fetch('/api/auth/session',{credentials:'include',headers:{'accept':'application/json'}});
  const s=await sR.json().catch(()=>({})); if(!s||!s.accessToken) return {ok:false,why:'not logged in'};
  const r=await fetch(`/backend-api/conversation/${cid}`,{credentials:'include',headers:{'authorization':'Bearer '+s.accessToken,'accept':'application/json'}});
  if(!r.ok) return {ok:false,status:r.status};
  const j=await r.json(); const mp=j.mapping||{}; const chain=[]; let cur=j.current_node; const seen=new Set();
  while(cur&&mp[cur]&&!seen.has(cur)){seen.add(cur);chain.push(cur);cur=mp[cur].parent;} chain.reverse();
  const ids=chain.length?chain:Object.keys(mp); const msgs=[];
  for(const k of ids){const m=mp[k]&&mp[k].message; if(!m)continue; const role=m.author&&m.author.role; if(role==='system')continue;
    const parts=(m.content&&m.content.parts)||[]; const text=parts.filter(x=>typeof x==='string').join('\n').trim();
    if(!text)continue; msgs.push({role,text,create_time:m.create_time});}
  return {ok:true,title:j.title,model:j.default_model_slug,msgs};
}"""

def log(m): sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {m}\n"); sys.stderr.flush()
def fmt(ts):
    try: return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return "?"

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9223")
    page = next((pg for pg in b.contexts[0].pages if "chatgpt.com" in pg.url), None) or b.contexts[0].new_page()
    page.bring_to_front()
    log(f"기존 대화 이동: {CID}")
    page.goto(f"https://chatgpt.com/c/{CID}", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector('#prompt-textarea', timeout=15000)
    # 기존 대화 로딩 대기(assistant 메시지가 한 번 잡힐 때까지)
    t0 = time.time()
    while time.time() - t0 < 15:
        if page.evaluate(COUNT_ASST) > 0: break
        time.sleep(0.5)
    before = page.evaluate(COUNT_ASST)
    log(f"기존 assistant 메시지 수: {before}")

    log(f"후속 질문 입력: {PROMPT[:30]}")
    page.click('#prompt-textarea')
    page.keyboard.type(PROMPT, delay=20)
    time.sleep(0.3)
    sent = False
    try:
        btn = page.query_selector('[data-testid="send-button"]')
        if btn and btn.is_enabled(): btn.click(); sent = True
    except Exception: pass
    if not sent: page.keyboard.press("Enter")

    log("새 답변 대기(폴링)")
    text, stable, deadline = "", 0, time.time() + 150
    while time.time() < deadline:
        try:
            cnt = page.evaluate(COUNT_ASST); cur = page.evaluate(LAST_ASST) or ""; gen = page.evaluate(IS_GEN)
        except Exception:
            cnt, cur, gen = before, text, True
        # 새 답변이 생겼고(개수 증가) 텍스트 안정 + 생성중 아님
        if cnt > before and cur and cur == text and not gen:
            stable += 1
            if stable >= 3: break
        else:
            stable = 0
        text = cur
        time.sleep(1.0)
    after = page.evaluate(COUNT_ASST)
    log(f"answer DOM len={len(text)}  assistant {before}->{after}")
    data = page.evaluate(GET_CONV, CID); data["conversation_id"] = CID
    page.screenshot(path="_existing.png")
    b.close()

if data.get("ok"):
    asst = [m for m in data["msgs"] if m["role"] == "assistant"]
    new_answer = asst[-1]["text"] if asst else text
    lines = [f"# (이어쓰기) {data.get('title') or ''}",
             f"_id: {CID} · model: {data.get('model')} · 누적 메시지 {len(data['msgs'])}_", "",
             f"## ❓ 후속 질문\n{PROMPT}", "", f"## 💬 답변\n{new_answer}", ""]
    open("existing_answer.md", "w", encoding="utf-8").write("\n".join(lines))
    open("existing_answer.json", "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2))
    summary = {"ok": True, "conversation_id": CID, "assistant_before_after": [before, after],
               "total_messages": len(data["msgs"]), "answer_chars": len(new_answer),
               "saved": "existing_answer.md / .json"}
else:
    summary = {"ok": False, **{k: v for k, v in data.items() if k != "msgs"}}
sys.stdout.write(json.dumps(summary, ensure_ascii=True) + "\n")
