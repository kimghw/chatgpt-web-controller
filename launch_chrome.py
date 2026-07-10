"""디버그 Chrome 기동 + (선택) ChatGPT 자동 로그인.

Chrome 136+ 는 기본 프로필에선 --remote-debugging-port 를 무시하므로 (chatgpt.md §1)
config.json 의 user_data_dir 전용 프로필로 Chrome 을 띄운다. 프로필이 유지되므로
로그인은 최초 1회면 충분.

사용: python launch_chrome.py
  1) 포트가 이미 열려 있으면 기동 생략
  2) config.json 의 chrome_path/user_data_dir/cdp_port 로 Chrome 실행
  3) 로그인 안 된 상태 + config 의 login.email/password 가 있으면 자동 입력 시도
     (OpenAI 이메일 계정만. 소셜 로그인/2FA/CAPTCHA 는 열린 창에서 직접 진행)
결과: {launched, logged_in, email} JSON (ASCII) 출력.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

import chatgpt_client as cc

CFG = cc.load_config()
PORT = cc.PORT
LOGIN = CFG.get("login") or {}


def log(m):
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {m}\n"); sys.stderr.flush()


def port_alive() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def launch() -> bool:
    """디버그 Chrome 기동. 이미 떠 있으면 False(기동 안 함) 반환."""
    if port_alive():
        log(f"포트 {PORT} 이미 활성 — 기동 생략")
        return False
    chrome = CFG.get("chrome_path") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    profile = os.path.expandvars(CFG.get("user_data_dir") or r"%USERPROFILE%\.chrome-chatgpt-debug")
    log(f"Chrome 기동: port={PORT} profile={profile}")
    subprocess.Popen([
        chrome,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://chatgpt.com/",
    ])
    for _ in range(30):
        if port_alive():
            return True
        time.sleep(1)
    raise RuntimeError(f"포트 {PORT} 가 열리지 않음 — chrome_path/user_data_dir 확인")


def try_login(page) -> None:
    """login-button → 이메일/비밀번호 입력 (best-effort). 실패해도 예외 없이 반환."""
    email, password = LOGIN.get("email"), LOGIN.get("password")
    if not email:
        log("config 에 login.email 없음 — 열린 창에서 직접 로그인")
        return
    try:
        btn = page.query_selector('[data-testid="login-button"]')
        if btn:
            btn.click()
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        page.wait_for_selector('input[type="email"], input[name="username"]', timeout=20000)
        page.fill('input[type="email"], input[name="username"]', email)
        page.keyboard.press("Enter")
        log("이메일 입력 완료")
        if password:
            page.wait_for_selector('input[type="password"]', timeout=20000)
            page.fill('input[type="password"]', password)
            page.keyboard.press("Enter")
            log("비밀번호 입력 완료 — 2FA/CAPTCHA 있으면 창에서 직접 진행")
    except Exception as e:
        log(f"자동 로그인 실패({e.__class__.__name__}) — 열린 창에서 직접 로그인")


def main():
    launched = launch()
    time.sleep(3)

    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(cc.CDP)
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = next((pg for pg in ctx.pages if "chatgpt.com" in pg.url), None)
        if page is None:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        page.bring_to_front()
        time.sleep(3)

        sess = page.evaluate(cc.JS_SESSION)
        if not sess.get("logged_in"):
            try_login(page)
            # 로그인 완료(사람 개입 포함) 대기 — 최대 180초
            deadline = time.time() + 180
            while time.time() < deadline:
                try:
                    sess = page.evaluate(cc.JS_SESSION)
                except Exception:
                    sess = {"logged_in": False}
                if sess.get("logged_in"):
                    break
                time.sleep(3)
        result = {"launched": launched, "logged_in": bool(sess.get("logged_in")), "email": sess.get("email")}
    finally:
        browser.close()   # CDP 연결만 끊음 — Chrome 은 계속 떠 있음
        p.stop()
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
