"""현재 열린(또는 인자로 받은) ChatGPT 대화의 결과 내용을 회수.

- CDP attach(9223), 활성 chatgpt 탭의 /c/<id> 에서 conversation_id 추출
- /api/auth/session 의 accessToken → Bearer 로 /backend-api/conversation/<id>
- mapping 파싱해 user/assistant 메시지를 시간순으로 정리
- UTF-8 .md/.json 저장(cp949 회피), 콘솔엔 ASCII 요약
"""
from __future__ import annotations
import socket, json, sys, re
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
from chatgpt_client import CDP  # 포트: env CHATGPT_CDP_PORT > config.json > 9223

KST = timezone(timedelta(hours=9))

GET_CONV = r"""
async (cid) => {
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  if (!s || !s.accessToken) return {ok:false, why:'not logged in'};
  const r = await fetch(`/backend-api/conversation/${cid}`, {credentials:'include',
              headers:{'authorization':'Bearer '+s.accessToken,'accept':'application/json'}});
  if (!r.ok) return {ok:false, status:r.status, body:(await r.text()).slice(0,200)};
  const j = await r.json();
  const mp = j.mapping || {};
  // current_node 에서 parent 체인을 따라 루트까지 올라가며 활성 스레드 순서 복원
  const chain = [];
  let cur = j.current_node;
  const guard = new Set();
  while (cur && mp[cur] && !guard.has(cur)) { guard.add(cur); chain.push(cur); cur = mp[cur].parent; }
  chain.reverse();
  const ids = chain.length ? chain : Object.keys(mp);   // 폴백: 전체 키
  const msgs = [];
  for (const k of ids) {
    const node = mp[k]; if (!node) continue;
    const m = node.message; if (!m) continue;
    const role = m.author && m.author.role;
    if (role === 'system') continue;
    const parts = (m.content && m.content.parts) || [];
    const text = parts.filter(x=>typeof x==='string').join('\n').trim();
    if (!text) continue;
    msgs.push({role, text, create_time: m.create_time});
  }
  return {ok:true, title:j.title, model:j.default_model_slug, create_time:j.create_time, msgs};
}
"""

def fmt(ts):
    try: return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return "?"

cid_arg = sys.argv[1] if len(sys.argv) > 1 else None
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(CDP)
    page = next((pg for pg in b.contexts[0].pages if "chatgpt.com" in pg.url), None)
    if page is None:
        print(json.dumps({"ok": False, "why": "no chatgpt tab"})); b.close(); sys.exit(1)
    page.bring_to_front()
    url = page.evaluate("() => location.href")
    cid = cid_arg or (re.search(r"/c/([0-9a-f-]+)", url) or [None, None])[1]
    if not cid:
        print(json.dumps({"ok": False, "why": "활성 탭이 /c/<id> 대화가 아님", "url": url}, ensure_ascii=True))
        b.close(); sys.exit(1)
    data = page.evaluate(GET_CONV, cid)
    data["conversation_id"] = cid
    b.close()

if data.get("ok"):
    lines = [f"# {data.get('title') or '(제목 없음)'}",
             f"_id: {data['conversation_id']} · model: {data.get('model')} · 생성: {fmt(data.get('create_time'))} (KST)_", ""]
    for m in data["msgs"]:
        who = {"user": "🧑 사용자", "assistant": "🤖 ChatGPT", "tool": "🔧 도구"}.get(m["role"], m["role"])
        lines.append(f"### {who}  ·  {fmt(m.get('create_time'))}")
        lines.append(m["text"]); lines.append("")
    md = "\n".join(lines)
    with open("result_conversation.md", "w", encoding="utf-8") as f: f.write(md)
    with open("result_conversation.json", "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    summary = {"ok": True, "conversation_id": cid, "title_len": len(data.get("title") or ""),
               "messages": len(data["msgs"]),
               "roles": [m["role"] for m in data["msgs"]],
               "saved": "result_conversation.md / .json"}
else:
    summary = {"ok": False, **{k: v for k, v in data.items() if k != "msgs"}}
sys.stdout.write(json.dumps(summary, ensure_ascii=True) + "\n")
