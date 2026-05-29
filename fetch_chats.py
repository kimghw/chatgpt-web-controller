"""ChatGPT 채팅(대화) 리스트 추출 — CDP attach + backend-api (Bearer token).

핵심 교훈: /backend-api/me 를 쿠키만으로 부르면 익명(ua-)처럼 보인다.
ChatGPT 웹앱은 /api/auth/session 의 accessToken 을 받아
Authorization: Bearer 로 /backend-api/* 를 호출한다. 그 흐름을 그대로 재현.

playwrite.md 원칙: contexts[0] 재사용(port 9223 Default 프로필), browser.close() 만.
"""
from __future__ import annotations

import json
import socket
import sys

_orig = socket.getaddrinfo


def _ipv4(host, port, *a, **k):
    if isinstance(host, str) and host.lower() in ("localhost", "::1"):
        host = "127.0.0.1"
    if len(a) >= 1:
        a = (socket.AF_INET,) + a[1:]
    else:
        k["family"] = socket.AF_INET
    return _orig(host, port, *a, **k)


socket.getaddrinfo = _ipv4  # type: ignore[assignment]

PORT = 9223
CDP = f"http://localhost:{PORT}"

JS_FETCH_ALL = r"""
async () => {
  // 1) 세션 → accessToken + 계정
  const sR = await fetch('/api/auth/session', {credentials:'include', headers:{'accept':'application/json'}});
  const s = await sR.json().catch(()=>({}));
  const token = s && s.accessToken;
  const email = s && s.user && s.user.email;
  if (!token) return { loggedIn:false, session:s };

  const auth = { 'authorization': 'Bearer ' + token, 'accept':'application/json' };
  const items = [];
  const limit = 100;
  let offset = 0, total = null;
  for (let guard=0; guard<300; guard++) {
    const url = `/backend-api/conversations?offset=${offset}&limit=${limit}&order=updated`;
    const r = await fetch(url, {credentials:'include', headers: auth});
    if (!r.ok) { return { loggedIn:true, email, error:`conversations ${r.status}`, body:(await r.text()).slice(0,200), count:items.length, items }; }
    const j = await r.json();
    total = j.total;
    const batch = j.items || [];
    for (const it of batch) items.push({ id: it.id, title: it.title, create_time: it.create_time, update_time: it.update_time, is_archived: it.is_archived });
    offset += batch.length;
    if (batch.length < limit || (total!=null && offset >= total)) break;
  }
  return { loggedIn:true, email, total, count: items.length, items };
}
"""


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        if page is None:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)

        data = page.evaluate(JS_FETCH_ALL)
        browser.close()

    # UTF-8 파일로 저장 (Windows 콘솔 cp949 인코딩 회피)
    with open("chats.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    summary = {
        "loggedIn": data.get("loggedIn"),
        "email": data.get("email"),
        "total": data.get("total"),
        "count": data.get("count"),
        "error": data.get("error"),
        "saved": "chats.json",
    }
    # 요약은 ASCII-safe
    sys.stdout.write(json.dumps(summary, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"loggedIn": False, "error": repr(e)}, ensure_ascii=False))
        sys.exit(1)
