#!/usr/bin/env python3
"""xiaohongshu-reply: auto-reply to XHS comments and @mentions.

Two-phase login via SMS verification:
  Phase 1: python main.py --limit 50
           → fills phone, sends code, outputs {"status":"need_code"}
  Phase 2: python main.py --limit 50 --code 123456
           → enters code, logs in, scrapes + replies
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from enterprise_db import get_enterprise_data, get_xhs_account, get_xhs_account_name, get_enabled_replies
from xhs_reply_utils import (
    human_delay, type_human, deterministic_id, strip_relative_time,
    load_replied_ids, append_replied_id, read_json, write_json,
    call_llm, extract_json, TZ_CN,
    is_spam_prefilter, extract_comment_body, FIXED_REPLIES, CONFIDENCE_THRESHOLD,
)

NOTIFICATION_URL = "https://www.xiaohongshu.com/notification"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

# ── Path helpers ───────────────────────────────────────────────────────────────

def get_reply_base() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    d = base / "BaiClaw" / "baiclaw" / "xhs-reply"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    d = base / "BaiClaw" / "browser-profiles" / f"xhs-reply-{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_run_dir() -> Path:
    run_id = datetime.now(TZ_CN).strftime("%Y%m%d-%H%M%S")
    d = get_reply_base() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Login helpers ──────────────────────────────────────────────────────────────

def is_on_login_page(page) -> bool:
    url = page.url.lower()
    return "login" in url or "passport" in url


def _debug_dump(page, run_dir: Path, label: str):
    """Save screenshot + body HTML for debugging when element finding fails."""
    try:
        ss = run_dir / f"debug_{label}.png"
        page.screenshot(path=str(ss), full_page=False)
        print(f"[debug] 截图已保存: {ss}", flush=True)
    except Exception as e:
        print(f"[debug] 截图失败: {e}", flush=True)
    try:
        html = run_dir / f"debug_{label}.html"
        body = page.evaluate("() => document.body.innerHTML")
        html.write_text(str(body), encoding="utf-8")
        print(f"[debug] HTML 已保存: {html}", flush=True)
    except Exception as e:
        print(f"[debug] HTML 保存失败: {e}", flush=True)


def _fill_phone(page, phone: str, run_dir: Path = None) -> bool:
    """Find and fill the phone number input field."""
    phone_filled = False
    for placeholder in ["手机号", "请输入手机号", "手机号码", "请输入手机号码", "phone"]:
        try:
            inp = page.locator(f"input[placeholder*='{placeholder}']").first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                human_delay(page, 200, 400)
                inp.fill(phone)
                phone_filled = True
                print("[login] 手机号已填入 (placeholder)", flush=True)
                break
        except Exception:
            continue

    if not phone_filled:
        for sel in ["input[type='tel']", "input[name*='phone']", "input[name*='mobile']"]:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click()
                    human_delay(page, 200, 400)
                    el.fill(phone)
                    phone_filled = True
                    print(f"[login] 手机号已填入 ({sel})", flush=True)
                    break
            except Exception:
                continue

    if not phone_filled:
        try:
            inputs = page.locator("input:visible").all()
            for inp in inputs:
                try:
                    tag = inp.evaluate("el => el.tagName.toLowerCase()")
                    tp = inp.evaluate("el => el.type")
                    if tag == "input" and tp not in ("hidden", "submit", "checkbox", "radio", "button"):
                        inp.click()
                        inp.fill(phone)
                        phone_filled = True
                        print("[login] 手机号已填入 (visible input fallback)", flush=True)
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not phone_filled:
        print("[login] 未找到手机号输入框", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "no_phone_input")
        return False
    return True


def _agree_terms(page, run_dir: Path = None) -> bool:
    """Check the 'agree to terms' checkbox. Returns True on success or if skipped."""
    # Tier 1: Playwright text locator → native click
    for keyword in ["同意", "协议"]:
        try:
            agree_el = page.locator(f"text={keyword}").first
            if agree_el.count() > 0 and agree_el.is_visible():
                agree_el.click(timeout=2000)
                print(f"[login] 已勾选协议 (Playwright text={keyword})", flush=True)
                return True
        except Exception as e:
            print(f"[login] text={keyword} click failed: {e}", flush=True)

    # Tier 2: Playwright — parent of agreement text
    try:
        agree_el = page.locator(":has-text('同意')").first
        if agree_el.count() > 0 and agree_el.is_visible():
            agree_el.click(timeout=2000)
            print("[login] 已勾选协议 (Playwright :has-text)", flush=True)
            return True
    except Exception as e:
        print(f"[login] :has-text click failed: {e}", flush=True)

    # Tier 3: Playwright — native checkbox
    for sel in ["input[type='checkbox']", "[role='checkbox']"]:
        try:
            cb = page.locator(sel).first
            if cb.count() > 0 and cb.is_visible():
                cb.check(timeout=2000)
                print(f"[login] 已勾选协议 (Playwright {sel})", flush=True)
                return True
        except Exception as e:
            print(f"[login] {sel} check failed: {e}", flush=True)

    # Tier 4: JS dispatchEvent
    try:
        result = page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.childElementCount > 5) continue;
                const t = (el.textContent || '').trim();
                if (t.includes('同意') && (t.includes('协议') || t.includes('政策') || t.includes('条款'))) {
                    let cb = el.previousElementSibling;
                    if (cb) {
                        cb.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return 'prev_sibling_dispatch';
                    }
                    const p = el.parentElement;
                    if (p) {
                        const icon = p.querySelector('svg, i, [class*="check"], [class*="icon"], [class*="agree"]');
                        if (icon) {
                            icon.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return 'icon_dispatch';
                        }
                        p.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return 'parent_dispatch';
                    }
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return 'text_dispatch';
                }
            }
            return null;
        }""")
        if result:
            print(f"[login] 已勾选协议 (JS dispatchEvent: {result})", flush=True)
            return True
    except Exception as e:
        print(f"[login] JS checkbox dispatchEvent: {e}", flush=True)

    print("[login] 警告: 未找到协议勾选框，尝试继续", flush=True)
    return False


def fill_phone_and_send_code(page, phone: str, run_dir: Path = None) -> bool:
    """Phase 1: Fill phone, agree terms, click send verification code.

    IMPORTANT: Use Playwright native clicks so React handlers fire.
    """
    print(f"[login] Phase 1: 填写手机号: {phone}", flush=True)
    human_delay(page, 1000, 2000)

    if not _fill_phone(page, phone, run_dir):
        return False

    human_delay(page, 300, 600)
    _agree_terms(page, run_dir)
    human_delay(page, 500, 800)

    # ── Click send code button ──
    clicked = False
    for text in ["发送验证码", "获取验证码"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                clicked = True
                print(f"[login] 已点击 '{text}' (Playwright button)", flush=True)
                break
        except Exception as e:
            print(f"[login] button:has-text failed: {e}", flush=True)

    if not clicked:
        for text in ["发送验证码", "获取验证码"]:
            try:
                btn = page.locator(f"text={text}").last
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=3000)
                    clicked = True
                    print(f"[login] 已点击 '{text}' (Playwright text last)", flush=True)
                    break
            except Exception as e:
                print(f"[login] text={text} click failed: {e}", flush=True)

    if not clicked:
        for text in ["发送验证码", "获取验证码"]:
            try:
                btn = page.get_by_text(text, exact=True).first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    clicked = True
                    print(f"[login] 已点击 '{text}' (Playwright exact)", flush=True)
                    break
            except Exception as e:
                print(f"[login] get_by_text exact failed: {e}", flush=True)

    if not clicked:
        try:
            result = page.evaluate("""() => {
                const targets = ['发送验证码', '获取验证码'];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    for (const target of targets) {
                        if (t === target && el.offsetParent !== null) {
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return target;
                        }
                    }
                }
                return null;
            }""")
            if result:
                clicked = True
                print(f"[login] 已点击 '{result}' (JS mouse events)", flush=True)
        except Exception as e:
            print(f"[login] JS button dispatchEvent: {e}", flush=True)

    if not clicked:
        print("[login] 未找到发送验证码按钮", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "03_no_send_btn")
        return False

    human_delay(page, 1000, 2000)
    return True


def enter_code_and_login(page, phone: str, code: str, run_dir: Path = None) -> bool:
    """Phase 2: Fill phone, agree terms, enter code, click login.

    Phone + agree are re-done because browser state is lost between phases.
    If the page is still in 'send code' state, clicks send-code first to
    transition to the code-entry UI.
    """
    print(f"[login] Phase 2: 手机号 {phone}, 验证码 {code}", flush=True)
    human_delay(page, 2000, 3000)

    # Step 1: Fill phone (state lost since Phase 1)
    if not _fill_phone(page, phone, run_dir):
        return False
    human_delay(page, 500, 1000)

    # Step 2: Agree to terms
    _agree_terms(page, run_dir)
    human_delay(page, 800, 1200)
    if run_dir:
        _debug_dump(page, run_dir, "p2_phone_and_checkbox")

    # Step 3: Check if code input is already visible, otherwise click send-code first
    code_input_visible = False
    for placeholder in ["验证码", "短信验证码", "请输入验证码", "code", "verify"]:
        try:
            inp = page.locator(f"input[placeholder*='{placeholder}']").first
            if inp.count() > 0 and inp.is_visible():
                code_input_visible = True
                break
        except Exception:
            continue

    if not code_input_visible:
        # Also check for 6 separate digit inputs
        inputs = page.locator("input:not([type='hidden']):not([type='submit']):not([type='checkbox']):not([type='radio'])").all()
        visible_count = 0
        for inp in inputs:
            try:
                if inp.is_visible():
                    visible_count += 1
            except Exception:
                continue
        if visible_count >= 6:
            code_input_visible = True

    if not code_input_visible:
        # Need to click send-code to transition to code entry
        print("[login] 未检测到验证码输入框，先点击发送验证码...", flush=True)
        send_clicked = False
        for text in ["发送验证码", "获取验证码"]:
            try:
                btn = page.locator(f"text={text}").first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    send_clicked = True
                    print(f"[login] 已点击 '{text}' 切换到验证码输入", flush=True)
                    break
            except Exception as e:
                print(f"[login] send-btn {text}: {e}", flush=True)

        if not send_clicked:
            try:
                result = page.evaluate("""() => {
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.textContent || '').trim();
                        if (t === '发送验证码' || t === '获取验证码') {
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return t;
                        }
                    }
                    return null;
                }""")
                if result:
                    send_clicked = True
                    print(f"[login] 已点击 '{result}' (JS)", flush=True)
            except Exception:
                pass

        if not send_clicked:
            print("[login] 无法切换到验证码输入页", flush=True)
            if run_dir:
                _debug_dump(page, run_dir, "p2_no_send_btn")
            return False

        # Wait for code input to appear
        human_delay(page, 2000, 3000)
        if run_dir:
            _debug_dump(page, run_dir, "p2_after_send_code")

    # Step 4: Enter verification code
    code_entered = False
    for placeholder in ["验证码", "短信验证码", "请输入验证码", "code", "verify"]:
        try:
            inp = page.locator(f"input[placeholder*='{placeholder}']").first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                human_delay(page, 200, 400)
                inp.fill(code)
                code_entered = True
                print(f"[login] 验证码已填入 (placeholder={placeholder})", flush=True)
                break
        except Exception:
            continue

    if not code_entered:
        inputs = page.locator("input:not([type='hidden']):not([type='submit']):not([type='checkbox']):not([type='radio'])").all()
        visible = [inp for inp in inputs if inp.is_visible()]
        if len(visible) >= len(code):
            try:
                for i, ch in enumerate(code):
                    visible[i].click()
                    human_delay(page, 50, 100)
                    visible[i].fill(ch)
                code_entered = True
                print(f"[login] 已填入 {len(code)} 位数字 ({len(visible)} inputs)", flush=True)
            except Exception as e:
                print(f"[login] 数字框填充失败: {e}", flush=True)

    if not code_entered:
        print("[login] 未找到验证码输入框", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "p2_no_code_input")
        return False

    human_delay(page, 500, 1000)
    if run_dir:
        _debug_dump(page, run_dir, "p2_code_entered")

    # Step 5: Click login/submit button
    submitted = False

    # Extended button text list
    all_texts = [
        "登录", "确定", "提交", "进入", "登 录",
        "完成", "确认", "下一步", "继续",
        "Login", "Submit", "OK", "Go",
    ]
    for text in all_texts:
        try:
            btn = page.locator(f"text={text}").first
            if btn.count() > 0:
                btn.click(timeout=3000)
                submitted = True
                print(f"[login] 已点击 '{text}' (Playwright)", flush=True)
                break
        except Exception as e:
            print(f"[login] text={text}: {e}", flush=True)

    # Tier 2: Button role
    if not submitted:
        try:
            btn = page.get_by_role("button").first
            if btn.count() > 0:
                btn.click(timeout=3000)
                submitted = True
                print("[login] 已点击第一个 button (role)", flush=True)
        except Exception as e:
            print(f"[login] role=button: {e}", flush=True)

    # Tier 3: JS dispatchEvent with broader text matching
    if not submitted:
        try:
            result = page.evaluate("""() => {
                const targets = ['登录', '确定', '提交', '进入', '完成', '确认', '下一步', '继续'];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    for (const target of targets) {
                        if (t === target && el.offsetParent !== null) {
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return target;
                        }
                    }
                }
                // Broader: find any button-like element
                for (const el of document.querySelectorAll('button, [role="button"], .btn, .button, [class*="submit"], [class*="login"]')) {
                    if (el.offsetParent !== null) {
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return 'generic_button';
                    }
                }
                return null;
            }""")
            if result:
                submitted = True
                print(f"[login] 已点击 '{result}' (JS)", flush=True)
        except Exception:
            pass

    # Tier 4: Keyboard Enter (form submit fallback)
    if not submitted:
        try:
            page.keyboard.press("Enter")
            submitted = True
            print("[login] 已按 Enter 提交", flush=True)
        except Exception:
            pass

    if not submitted:
        print("[login] 未找到登录/提交按钮", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "p2_no_submit_btn")
        return False

    if run_dir:
        _debug_dump(page, run_dir, "p2_after_submit")
    # Wait for redirect away from login
    for i in range(15):
        human_delay(page, 2000, 3000)
        if not is_on_login_page(page):
            print(f"[login] 登录成功，当前: {page.url}", flush=True)
            return True
        if i == 3 and run_dir:
            _debug_dump(page, run_dir, "p2_still_on_login")
        if i == 5:
            print("[login] 仍在登录页，继续等待...", flush=True)

    if run_dir:
        _debug_dump(page, run_dir, "p2_login_timeout")
    print("[login] 登录可能超时", flush=True)
    return not is_on_login_page(page)


def submit_code_and_login(page, code: str, run_dir: Path = None) -> bool:
    """Enter verification code and click login. Browser kept open from Phase 1.

    Assumes phone already filled, agreement checked, and send-code clicked.
    The code input should already be visible on the page.
    """
    print(f"[login] 输入验证码: {code}", flush=True)
    human_delay(page, 1000, 2000)

    code_entered = False
    for placeholder in ["验证码", "短信验证码", "请输入验证码", "code", "verify"]:
        try:
            inp = page.locator(f"input[placeholder*='{placeholder}']").first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                human_delay(page, 200, 400)
                inp.fill(code)
                code_entered = True
                print(f"[login] 验证码已填入 (placeholder={placeholder})", flush=True)
                break
        except Exception:
            continue

    if not code_entered:
        inputs = page.locator("input:not([type='hidden']):not([type='submit']):not([type='checkbox']):not([type='radio'])").all()
        visible = [inp for inp in inputs if inp.is_visible()]
        if len(visible) >= len(code):
            try:
                for i, ch in enumerate(code):
                    visible[i].click()
                    human_delay(page, 50, 100)
                    visible[i].fill(ch)
                code_entered = True
                print(f"[login] 已填入 {len(code)} 位数字 ({len(visible)} inputs)", flush=True)
            except Exception as e:
                print(f"[login] 数字框填充失败: {e}", flush=True)

    if not code_entered:
        print("[login] 未找到验证码输入框", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "no_code_input")
        return False

    human_delay(page, 500, 1000)

    submitted = False
    # Try specific button selectors first (avoid clicking header/nav "登录" text)
    for sel in [
        "button.submit.active",
        "button.submit",
        "[class*='submit']",
        "button:has-text('登录')",
        "button:has-text('进入')",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                submitted = True
                print(f"[login] 已点击 '{sel}' (Playwright)", flush=True)
                break
        except Exception as e:
            print(f"[login] {sel}: {e}", flush=True)

    # Fallback: text locator, use .last (button usually after header)
    if not submitted:
        for text in ["登录", "确定", "提交", "进入", "完成", "确认", "下一步", "继续"]:
            try:
                btn = page.locator(f"text={text}").last
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=3000)
                    submitted = True
                    print(f"[login] 已点击 '{text}' (Playwright text last)", flush=True)
                    break
            except Exception as e:
                print(f"[login] text={text}: {e}", flush=True)

    if not submitted:
        try:
            btn = page.get_by_role("button").first
            if btn.count() > 0:
                btn.click(timeout=3000)
                submitted = True
                print("[login] 已点击第一个 button (role)", flush=True)
        except Exception:
            pass

    if not submitted:
        try:
            page.evaluate("""() => {
                // Click visible login/submit buttons (prefer ones with submit class)
                for (const el of document.querySelectorAll('button.submit, button[class*=\"submit\"], button[class*=\"active\"]')) {
                    if (el.offsetParent !== null) {
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return;
                    }
                }
                for (const el of document.querySelectorAll('button, [role=\"button\"], .btn, [class*=\"submit\"], [class*=\"login\"]')) {
                    if (el.offsetParent !== null) {
                        const t = (el.textContent || '').trim();
                        if (['登录','进入','确定','提交','完成','确认'].includes(t)) {
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return;
                        }
                    }
                }
            }""")
            submitted = True
            print("[login] 已点击提交 (JS)", flush=True)
        except Exception:
            pass

    if not submitted:
        try:
            page.keyboard.press("Enter")
            submitted = True
            print("[login] 已按 Enter 提交", flush=True)
        except Exception:
            pass

    if not submitted:
        print("[login] 未找到登录/提交按钮", flush=True)
        if run_dir:
            _debug_dump(page, run_dir, "no_submit_btn")
        return False

    if run_dir:
        _debug_dump(page, run_dir, "p2_after_submit")

    for i in range(15):
        human_delay(page, 2000, 3000)
        if not is_on_login_page(page):
            print(f"[login] 登录成功，当前: {page.url}", flush=True)
            return True
        if i == 3 and run_dir:
            _debug_dump(page, run_dir, f"p2_still_login_{i}")
        if i == 5:
            print("[login] 仍在登录页，继续等待...", flush=True)

    if run_dir:
        _debug_dump(page, run_dir, "p2_login_timeout")
    print("[login] 登录可能超时", flush=True)
    return not is_on_login_page(page)


# ── Notification scraping ──────────────────────────────────────────────────────

_JS_EXTRACT = """(arg) => {
    const limit = arg.limit, lastTs = arg.lastTs || '';
    const results = [], seen = new Set();
    // Directly query interaction-hint elements — the exact notification item marker
    const hints = document.querySelectorAll('[class*="interaction-hint"]');
    const timeRe = /(刚刚|\\d+\\s*(分钟|小时|天|周|月)前|\\d{4}[-/]\\d{2}[-/]\\d{2}\\s*\\d{2}:\\d{2}|昨天\\s*\\d{2}:\\d{2}|今天\\s*\\d{2}:\\d{2})/;
    for (const hint of hints) {
        if (!hint.offsetParent) continue;
        // Get parent container text (includes hint + content sibling)
        const parent = hint.parentElement;
        if (!parent) continue;
        const text = parent.innerText.trim();
        if (!text || text.length < 10 || seen.has(text)) continue;
        // Determine type from hint text
        const hintText = hint.innerText.trim();
        const hasC = /评论/.test(hintText);
        const hasM = /@|提到/.test(hintText);
        if (!hasC && !hasM) continue;
        seen.add(text);
        let nick = '';
        const nc = text.match(/^(.+?)评论/), nm = text.match(/^(.+?)@/);
        if (nc) nick = nc[1].trim();
        else if (nm) nick = nm[1].trim();
        let timeText = '';
        const tm = text.match(timeRe);
        if (tm) timeText = tm[1];
        // Extract comment body: everything after the time marker
        let body = '';
        if (tm) {
            const idx = text.indexOf(tm[1]) + tm[1].length;
            body = text.substring(idx).trim();
        }
        results.push({
            text: text.substring(0, 500), nickname: nick,
            hasComment: hasC, hasMention: hasM,
            timeText: timeText, body: body,
        });
        if (results.length >= limit) break;
    }
    return results;
}"""


def _normalize_ts(ts: str) -> str:
    """Ensure a timestamp string has the correct +08:00 timezone offset.
    Returns the normalized ISO string or empty string."""
    from datetime import datetime, timezone, timedelta
    if not ts:
        return ""
    tz_cn = timezone(timedelta(hours=8))
    try:
        # Try parsing various ISO formats
        for fmt in [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ]:
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz_cn)
                return dt.astimezone(tz_cn).isoformat()
            except ValueError:
                continue
    except Exception:
        pass
    return ts


def _parse_relative_time(time_text: str) -> str:
    """Convert relative time text to ISO timestamp with +08:00 offset.
    Returns '' if unparseable."""
    from datetime import datetime, timezone, timedelta
    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    if not time_text:
        return ""
    try:
        if time_text == "刚刚":
            return now.isoformat()
        if "分钟前" in time_text:
            n = int(re.findall(r"(\d+)", time_text)[0])
            return (now - timedelta(minutes=n)).isoformat()
        if "小时前" in time_text:
            n = int(re.findall(r"(\d+)", time_text)[0])
            return (now - timedelta(hours=n)).isoformat()
        if "天前" in time_text:
            n = int(re.findall(r"(\d+)", time_text)[0])
            return (now - timedelta(days=n)).isoformat()
        if "昨天" in time_text:
            tm = re.findall(r"(\d{2}:\d{2})", time_text)
            t = tm[0] if tm else "00:00"
            d = now - timedelta(days=1)
            dt = datetime(d.year, d.month, d.day, int(t[:2]), int(t[3:]), tzinfo=tz_cn)
            return dt.isoformat()
        if "今天" in time_text:
            tm = re.findall(r"(\d{2}:\d{2})", time_text)
            t = tm[0] if tm else "00:00"
            dt = datetime(now.year, now.month, now.day, int(t[:2]), int(t[3:]), tzinfo=tz_cn)
            return dt.isoformat()
        m = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})\s*(\d{2}):(\d{2})", time_text)
        if m:
            dt = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), tzinfo=tz_cn)
            return dt.isoformat()
        m = re.match(r"(\d{2})[-/](\d{2})\s*(\d{2}):(\d{2})", time_text)
        if m:
            dt = datetime(now.year, int(m[1]), int(m[2]), int(m[3]), int(m[4]), tzinfo=tz_cn)
            return dt.isoformat()
    except Exception:
        pass
    return ""


def _load_replied_fresh() -> tuple[set, set]:
    """Load replied IDs. Per-ID TTL pruning is handled by load_replied_ids()."""
    return load_replied_ids()


def scrape_notifications(page, phone: str, limit: int = 50, run_dir: Path = None) -> list[dict]:
    from enterprise_db import get_last_reply_ts
    from datetime import datetime, timezone, timedelta

    # Prune stale entries (>48h) before loading
    already_replied, _ = _load_replied_fresh()
    last_ts = _normalize_ts(get_last_reply_ts(phone) or "")
    all_items = []
    seen = set()

    # Click tabs — try multiple selectors
    tab_clicked = False
    for target in ["评论和@", "评论", "@", "提到"]:
        try:
            tab = page.locator(f"text={target}").first
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                tab_clicked = True
                human_delay(page, 800, 1500)
                break
        except Exception:
            continue
    if tab_clicked:
        print("[scrape] 已点击通知标签页", flush=True)

    # Click "查看更多" until all history is loaded
    for _ in range(20):
        btn = page.locator('.load-more-button').first
        if btn.count() > 0:
            try:
                if btn.is_visible():
                    btn.click()
                    human_delay(page, 1000, 2000)
                else:
                    break
            except Exception:
                break
        else:
            break

    # Aggressive scroll: keep scrolling until no new items or limit reached
    prev_count = 0
    for scroll_round in range(10):
        try:
            page.evaluate("window.scrollBy(0, 1200)")
            human_delay(page, 800, 1500)
        except Exception:
            break
        try:
            current = page.evaluate("""() => {
                const hints = document.querySelectorAll('[class*="interaction-hint"]');
                let count = 0;
                for (const h of hints) {
                    if (h.offsetParent) count++;
                }
                return count;
            }""")
            print(f"[scrape] 滚动第{scroll_round+1}轮: {current} 条通知", flush=True)
            if current == prev_count and scroll_round >= 3:
                print(f"[scrape] 通知数量稳定在 {current}，停止滚动", flush=True)
                break
            prev_count = current
        except Exception:
            pass

    # Debug: save notification page HTML for diagnosis
    if run_dir:
        _debug_dump(page, run_dir, "scrape_page")

    # JS extraction
    try:
        data = page.evaluate(_JS_EXTRACT, {"limit": limit, "lastTs": last_ts or ""})
        print(f"[scrape] JS提取到 {len(data)} 个候选通知", flush=True)
        OLD_REPLIED_BREAK = 3
        consecutive_old_replied = 0
        for item in data:
            text = item.get("text", "")
            if not text:
                continue
            # Strip timestamps so hash is stable across time
            stable_text = strip_relative_time(text)
            if not stable_text or stable_text in seen:
                continue
            seen.add(stable_text)
            st = "mention" if item.get("hasMention") and not item.get("hasComment") else "comment"
            cid = deterministic_id(stable_text)
            time_text = item.get("timeText", "")
            iso_ts = _parse_relative_time(time_text)
            # Determine if this item is older than last reply timestamp
            is_old = False
            norm_iso = ""
            norm_last = ""
            if last_ts and iso_ts:
                norm_iso = _normalize_ts(iso_ts)
                norm_last = _normalize_ts(last_ts)
                is_old = bool(norm_iso and norm_last and norm_iso <= norm_last)
            # Double safeguard: skip comments >= 2 days old based on page time
            is_too_old = False
            if iso_ts:
                try:
                    comment_dt = datetime.fromisoformat(iso_ts)
                    is_too_old = (datetime.now(TZ_CN) - comment_dt).total_seconds() >= 2 * 86400
                except Exception:
                    pass
            if cid in already_replied:
                if is_old:
                    consecutive_old_replied += 1
                    if consecutive_old_replied >= OLD_REPLIED_BREAK:
                        print(f"[scrape] 连续{consecutive_old_replied}条已处理旧消息，停止扫描", flush=True)
                        break
                else:
                    consecutive_old_replied = 0
                print(f"[scrape] 跳过已回复: {cid[:8]}...", flush=True)
                continue
            consecutive_old_replied = 0
            if is_old or is_too_old:
                print(f"[scrape] 跳过旧消息: {cid[:8]}...", flush=True)
                continue
            all_items.append({
                "comment_id": cid, "sub_type": st,
                "nickname": item.get("nickname", "") or "unknown",
                "content": stable_text[:500],
                "timestamp": iso_ts,
                "time_text": time_text,
            })
    except Exception as e:
        print(f"[scrape] JS failed: {e}", flush=True)

    # Text fallback
    if not all_items:
        try:
            body = page.evaluate("() => document.body.innerText")
            for line in [l.strip() for l in body.split("\n") if l.strip()]:
                if len(line) > 15:
                    stable = strip_relative_time(line)
                    if stable in seen:
                        continue
                    has_c = "评论" in line
                    has_m = "@" in line or "提到" in line
                    if has_c or has_m:
                        seen.add(stable)
                        cid = deterministic_id(stable)
                        if cid in already_replied:
                            continue
                        all_items.append({
                            "comment_id": cid,
                            "sub_type": "comment" if has_c else "mention",
                            "nickname": "unknown",
                            "content": stable[:500],
                            "timestamp": "",
                            "time_text": "",
                        })
        except Exception as e:
            print(f"[scrape] text failed: {e}", flush=True)

    c = sum(1 for i in all_items if i["sub_type"] == "comment")
    m = sum(1 for i in all_items if i["sub_type"] == "mention")
    if last_ts:
        print(f"[scrape] {len(all_items)} 条 ({c} 评论, {m} @) — 过滤早于 {last_ts}", flush=True)
    else:
        print(f"[scrape] {len(all_items)} 条 ({c} 评论, {m} @)", flush=True)
    return all_items[:limit]


# ── Reply generation ───────────────────────────────────────────────────────────

def load_brand_context() -> dict:
    from enterprise_db import get_enterprise_data, get_first_brand
    data = get_enterprise_data()
    if not data:
        return {}
    brand = get_first_brand(data)
    return brand or {}


INTENT_SYSTEM = (
    'You are a Xiaohongshu comment classifier for "{brand_name}" ({industry}). '
    'Classify the comment into exactly one category.\n\n'
    'Categories:\n'
    '- "inquiry": Questions about products, pricing, availability, or how to buy. '
    'Examples: "怎么买？", "多少钱？", "有教程吗？", "在哪里下单？"\n'
    '- "praise": Positive feedback, compliments, appreciation, satisfaction. '
    'Examples: "太好用了！", "种草了", "已下单", "支持", "很棒"\n'
    '- "neutral": General comments without strong positive or negative sentiment. '
    'Examples: "收藏了", "马克", brief reactions, offhand remarks.\n'
    '- "complaint": Dissatisfaction, problem reports, refund requests, criticism, '
    'sarcasm, derogatory remarks. NEEDS human handling. '
    'Examples: "不好用", "被骗了", "质量太差", "退款", "垃圾"\n'
    '- "spam": Irrelevant promotion, ads, gibberish, bot-like patterns. '
    'See spam rules below.\n\n'
    'SPAM RULES — classify as "spam" ONLY when at least ONE indicator is clearly present:\n'
    '1. Promotes unrelated products, services, brands, or accounts\n'
    '2. Contains URLs, QR codes, phone numbers, or "加V/加微信/私信" solicitations\n'
    '3. Gibberish, random characters, copy-paste chain messages, bot-generated text\n'
    '4. Entirely off-topic (viral comments, chain letters, unrelated trending topics)\n'
    '5. Obvious ad templates: "兼职招聘", "日赚", "免费领取", clickbait patterns\n'
    '6. Repeated spam-like short patterns already pre-filtered, confirm borderline cases\n\n'
    'BORDERLINE GUIDANCE: When a comment could be either "neutral" or "spam", '
    'classify as "neutral". Short ambiguous comments like "哈哈", "嗯", "哦", '
    'single emoji/word replies, and brief reactions are "neutral", not spam. '
    'Low-effort engagement ("学到了", "不错", "好看") is also "neutral". '
    'Classify as "spam" ONLY when there are clear, unambiguous spam indicators.\n\n'
    'Respond ONLY with valid JSON: '
    '{{"intent": "<category>", "confidence": 0.0-1.0, "reason": "<brief reason in Chinese>"}}'
)
INTENT_USER = "Classify this comment:\n{text}"

def classify_and_reply(items: list[dict], brand: dict, model: str = None,
                       custom_replies: list[str] | None = None) -> list[dict]:
    b_name = brand.get("name", "默认品牌")
    b_ind = brand.get("industry", "通用")
    reply_pool = custom_replies if custom_replies else FIXED_REPLIES

    results = []
    for item in items:
        text = item.get("content", "")[:300]
        cid = item.get("comment_id", "")

        # Pre-filter: mechanical spam checks on extracted comment body
        body = extract_comment_body(text)
        if is_spam_prefilter(body):
            result = {
                "source_id": cid,
                "sub_type": item.get("sub_type", "comment"),
                "nickname": item.get("nickname", ""),
                "content": text,
                "intent": "spam",
                "confidence": 1.0,
                "needs_human": False,
                "skipped": True,
                "reply": None,
                "timestamp": item.get("timestamp", ""),
                "time_text": item.get("time_text", ""),
            }
            print(f"[gen] {cid}: spam (pre-filter) → skip", flush=True)
            results.append(result)
            continue

        # LLM intent classification (for complaint / sarcasm / derogatory detection)
        try:
            resp = call_llm(
                INTENT_SYSTEM.format(brand_name=b_name, industry=b_ind),
                INTENT_USER.format(text=text),
                model=model, max_tokens=256,
            )
            intent_data = extract_json(resp)
        except Exception:
            intent_data = {"intent": "spam", "confidence": 0.0, "reason": "LLM error"}

        intent = intent_data.get("intent", "spam")

        # Confidence gate: low-confidence spam → reclassify as neutral
        # Conservative: rather auto-reply than wrongly skip a genuine comment
        if intent == "spam" and intent_data.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
            reason = intent_data.get("reason", "")
            print(f"[gen] {cid}: spam (low conf {intent_data.get('confidence', 0):.2f}) → reclassified as neutral ({reason})", flush=True)
            intent = "neutral"

        result = {
            "source_id": cid,
            "sub_type": item.get("sub_type", "comment"),
            "nickname": item.get("nickname", ""),
            "content": text,
            "intent": intent,
            "confidence": intent_data.get("confidence", 0.0),
            "needs_human": intent == "complaint",
            "skipped": intent == "spam",
            "reply": None,
            "timestamp": item.get("timestamp", ""),
            "time_text": item.get("time_text", ""),
        }

        if intent in ("complaint", "spam"):
            print(f"[gen] {cid}: {intent} → skip", flush=True)
        else:
            result["reply"] = random.choice(reply_pool)
            safe = result["reply"].encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
            print(f"[gen] {cid}: {intent} → fixed reply", flush=True)

        results.append(result)

    return results


# ── Send replies ───────────────────────────────────────────────────────────────

def send_one_reply(page, item: dict) -> bool:
    """Find notification by source_id, click its reply button,
    type reply and send."""
    source_id = item["source_id"]
    reply_text = item.get("reply", "")
    content_hint = item.get("content", "")[:80]
    if not reply_text:
        return False

    try:
        page.goto(NOTIFICATION_URL, wait_until="domcontentloaded", timeout=60_000)
        human_delay(page, 2000, 4000)

        # Click the correct notification tab (same logic as scrape)
        for target in ["评论和@", "评论", "@", "提到"]:
            try:
                tab = page.locator(f"text={target}").first
                if tab.count() > 0 and tab.is_visible():
                    tab.click()
                    human_delay(page, 800, 1500)
                    break
            except Exception:
                continue

        # Scroll to load items (match scrape depth: up to 10 rounds)
        for _ in range(10):
            page.evaluate("window.scrollBy(0, 1200)")
            human_delay(page, 500, 800)

        # ── Step A: collect visible candidates with absolute DOM indices ──
        candidates = page.evaluate("""() => {
            const hints = document.querySelectorAll('[class*="interaction-hint"]');
            const result = [];
            for (let i = 0; i < hints.length; i++) {
                const h = hints[i];
                if (!h.offsetParent) continue;
                const parent = h.parentElement;
                if (!parent) continue;
                result.push({
                    domIndex: i,
                    text: parent.innerText.trim().substring(0, 500)
                });
            }
            return result;
        }""")

        # Strip relative timestamps so hash matches scrape-time values
        for cand in candidates:
            cand["text"] = strip_relative_time(cand["text"])

        # ── Step B: match by source_id (deterministic hash) ──
        dom_idx = -1
        for cand in candidates:
            if deterministic_id(cand["text"]) == source_id:
                dom_idx = cand["domIndex"]
                break

        # ── Step C: fallback — substring match by content_hint ──
        if dom_idx < 0:
            for cand in candidates:
                if content_hint in cand["text"]:
                    print(f"[send] source_id miss, substring matched dom_idx={cand['domIndex']}", flush=True)
                    dom_idx = cand["domIndex"]
                    break

        # ── Step C-alt: JS-side last-resort matching ──
        if dom_idx < 0:
            dom_idx = page.evaluate("""(hint) => {
                const hints = document.querySelectorAll('[class*="interaction-hint"]');
                for (let i = 0; i < hints.length; i++) {
                    const h = hints[i];
                    if (!h.offsetParent) continue;
                    const parent = h.parentElement;
                    if (!parent) continue;
                    if (parent.innerText.includes(hint)) {
                        h.scrollIntoView({block: 'center'});
                        return i;
                    }
                }
                return -1;
            }""", content_hint)

        if dom_idx < 0:
            print(f"[send] not found: {source_id}", flush=True)
            return False

        # ── Step D: anchor to the hint element, find reply button within its container ──
        hint_el = page.locator("[class*=\"interaction-hint\"]").nth(dom_idx)
        if hint_el.count() == 0:
            print(f"[send] not found at dom_idx {dom_idx}: {source_id}", flush=True)
            return False

        # Use JS to find the correct .action-reply button for this notification.
        # Key insight: if an ancestor contains >1 .action-reply, we've gone past
        # the individual notification item into the scroll wrapper — use position.
        container_info = hint_el.evaluate("""(el) => {
            let node = el.parentElement;
            while (node && node !== document.body) {
                const btns = node.querySelectorAll('.action-reply');
                if (btns.length === 1 && btns[0].offsetParent !== null) {
                    return {found: true, btnCount: 1};
                }
                if (btns.length > 1) {
                    // Hit scroll wrapper — find closest .action-reply by Y distance
                    let closestIdx = 0, closestDist = Infinity;
                    const hintTop = el.getBoundingClientRect().top;
                    for (let i = 0; i < btns.length; i++) {
                        const d = Math.abs(btns[i].getBoundingClientRect().top - hintTop);
                        if (d < closestDist) { closestDist = d; closestIdx = i; }
                    }
                    return {found: true, btnCount: btns.length, btnIndex: closestIdx};
                }
                node = node.parentElement;
            }
            return {found: false};
        }""")

        container = page.locator("body")
        if container_info.get("found"):
            if container_info.get("btnCount", 1) == 1:
                # Individual notification item — XPATH into the exact container
                container = hint_el.locator("xpath=ancestor::*[descendant::*[contains(@class, 'action-reply')]][1]")
                reply_btn = container.locator(".action-reply").first
            else:
                # Multi-item wrapper — use position-based index
                reply_btn = page.locator(".action-reply").nth(container_info.get("btnIndex", dom_idx))
        else:
            reply_btn = page.locator(".action-reply").nth(dom_idx)

        if reply_btn.count() == 0 or not reply_btn.is_visible():
            # Fallback: click notification itself (navigates to detail page)
            print(f"[send] no action-reply, falling back to notification click", flush=True)
            hint_el.scroll_into_view_if_needed()
            human_delay(page, 300, 500)
            hint_el.click(timeout=10000)
            human_delay(page, 3000, 5000)
            # Container is now stale — reset to body after navigation
            container = page.locator("body")
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            reply_btn.scroll_into_view_if_needed()
            human_delay(page, 300, 500)
            reply_btn.click(timeout=10000)
            print(f"[send] clicked reply btn at dom_idx {dom_idx}", flush=True)

        # Wait for inline reply form
        human_delay(page, 1000, 2000)

        # ── Step E: find reply textarea ──
        # The reply form is rendered outside notification containers (portal/overlay),
        # so container-scoped searches are unreliable. Use global .last to pick the
        # most recently inserted (and thus correct) form.
        reply_el = None
        textarea_selectors = [
            "textarea[placeholder*='回复']",
            "[contenteditable='true']",
            "div[role='textbox']",
            "textarea",
            "input[placeholder*='回复']",
        ]
        for sel in textarea_selectors:
            try:
                el = page.locator(sel).last
                if el.count() > 0 and el.is_visible():
                    reply_el = el
                    break
            except Exception:
                continue

        # Fallback: click "回复" text button to expose the form
        if not reply_el:
            for btn_text in ["回复", "Reply"]:
                try:
                    btn = page.locator(f"text={btn_text}").last
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        human_delay(page, 800, 1500)
                        for sel in ["textarea", "[contenteditable='true']"]:
                            reply_el = page.locator(sel).last
                            if reply_el.count() > 0 and reply_el.is_visible():
                                break
                        if reply_el and reply_el.count() > 0:
                            break
                except Exception:
                    continue

        if not reply_el:
            print(f"[send] no input: {source_id}", flush=True)
            return False

        reply_el.click()
        human_delay(page, 300, 600)
        type_human(page, reply_text)
        human_delay(page, 800, 1500)

        # ── Step F: click send button ──
        # Same reasoning as textarea: the reply form is outside notification
        # containers. Use global .last to pick the most recent form.
        sent = False
        for text in ["确定", "发送", "回复", "发布", "评论"]:
            try:
                btn = page.locator(f"button:has-text('{text}')").last
                if btn.count() == 0 or not btn.is_visible():
                    btn = page.locator(f"[role='button']:has-text('{text}')").last
                if btn.count() == 0 or not btn.is_visible():
                    btn = page.locator(f"text={text}").last
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=2000)
                    sent = True
                    print(f"[send] click '{text}'", flush=True)
                    break
            except Exception as e:
                print(f"[send] text={text}: {e}", flush=True)

        if not sent:
            try:
                btn = page.locator(".foot-btn").last
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=2000)
                    sent = True
                    print("[send] click .foot-btn", flush=True)
            except Exception:
                pass

        if not sent:
            for sel in ["button", "[role='button']", "[type='submit']", ".submit", ".send", ".publish"]:
                try:
                    btn = page.locator(sel).last
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=2000)
                        sent = True
                        print(f"[send] click {sel}", flush=True)
                        break
                except Exception:
                    continue

        if not sent:
            # JS dispatch globally (container is unreliable for scoping)
            try:
                page.evaluate("""() => {
                    const targets = ['确定', '发送', '回复', '发布', '评论'];
                    const all = document.querySelectorAll('*');
                    // Iterate in reverse to find the most recent (lowest) match
                    for (let i = all.length - 1; i >= 0; i--) {
                        const el = all[i];
                        const t = (el.textContent || '').trim();
                        if (targets.includes(t) && el.offsetParent !== null) {
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return;
                        }
                    }
                }""")
                sent = True
                print("[send] JS dispatch", flush=True)
            except Exception:
                pass

        if not sent:
            try:
                page.keyboard.press("Enter")
                sent = True
                print("[send] Enter key", flush=True)
            except Exception:
                pass

        if not sent:
            print(f"[send] 未找到发送按钮: {source_id}", flush=True)
            return False

        human_delay(page, 2000, 3000)

        # ── Step G: verify reply landed in the correct notification ──
        verify_ok = False
        try:
            verify_ok = hint_el.evaluate("""(el, replyText) => {
                let node = el.parentElement;
                for (let i = 0; i < 8 && node && node !== document.body; i++) {
                    if (node.children.length >= 2 && node.children.length <= 10) break;
                    node = node.parentElement;
                }
                const scope = (node && node !== document.body) ? node : document.body;
                const clone = scope.cloneNode(true);
                clone.querySelectorAll('textarea, input, [contenteditable="true"]').forEach(e => e.remove());
                const t = clone.innerText || '';
                return t.includes(replyText) || t.includes('已回复');
            }""", reply_text[:30])
            print(f"[send] {'verified' if verify_ok else 'sent but unverified'}: {source_id}", flush=True)
        except Exception:
            print(f"[send] OK: {source_id}", flush=True)
            verify_ok = True  # exception during verification: assume success

        return verify_ok
    except Exception as e:
        print(f"[send] FAIL {source_id}: {e}", flush=True)
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="小红书评论自动回复")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--headless", type=str, default="false")
    parser.add_argument("--model", default=None, help="LLM 模型")
    parser.add_argument("--code", default=None, help="验证码（可选，跳过文件轮询）")
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    data = get_enterprise_data()
    acc = get_xhs_account(data) if data else None
    if not acc:
        print(json.dumps({"error": "未找到小红书发布账号"}, ensure_ascii=False))
        sys.exit(1)

    account_id = acc.get("id", "default")
    phone = get_xhs_account_name(data) or ""
    if not phone:
        print(json.dumps({"error": "未配置账号手机号", "hint": "accountName 字段为空"}, ensure_ascii=False))
        sys.exit(1)

    run_dir = args.run_dir or create_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        profile_dir = get_profile_dir(account_id)
        print(f"[main] Profile: {profile_dir}", flush=True)

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        context.add_init_script(ANTI_BOT_JS)

        try:
            page = context.new_page()

            try:
                page.goto(NOTIFICATION_URL, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            human_delay(page, 2000, 3000)
            print(f"[main] URL: {page.url}", flush=True)

            # ── Login flow (browser stays open, short timeouts + fast polling) ──
            if is_on_login_page(page):
                print("[main] 需要登录", flush=True)

                # Step 1: Fill phone, agree terms, click send code (timeouts reduced to 3s)
                if not fill_phone_and_send_code(page, phone, run_dir):
                    print(json.dumps({"error": "发送验证码失败"}, ensure_ascii=False))
                    sys.exit(1)

                # Step 2: Get verification code
                code_file = run_dir / "code.txt"
                code = None

                if args.code:
                    code = args.code.strip()
                    print(f"[main] 使用 --code 参数: {code}", flush=True)
                else:
                    print(json.dumps({
                        "status": "need_code",
                        "message": f"验证码已发送到 {phone}，请在会话中输入验证码",
                        "phone": phone,
                        "code_file": str(code_file),
                    }, ensure_ascii=False), flush=True)

                    # Poll code file every 1s (max 300s), browser stays open
                    for _ in range(300):
                        if code_file.exists():
                            content = code_file.read_text(encoding="utf-8").strip()
                            if content and content.isdigit() and len(content) >= 4:
                                code = content
                                break
                        time.sleep(1)

                if not code or not code.isdigit() or len(code) < 4:
                    print(json.dumps({"error": "验证码无效或超时"}, ensure_ascii=False))
                    sys.exit(1)

                # Step 3: Enter code and login (browser still open)
                if not submit_code_and_login(page, code, run_dir):
                    print(json.dumps({"error": "验证码登录失败"}, ensure_ascii=False))
                    sys.exit(1)

                human_delay(page, 3000, 5000)
                try:
                    page.goto(NOTIFICATION_URL, wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                human_delay(page, 2000, 3000)

            if is_on_login_page(page):
                print(json.dumps({"error": "登录未完成，请重试"}, ensure_ascii=False))
                sys.exit(1)

            # ── Scrape ──
            print("[main] 抓取通知...", flush=True)
            items = scrape_notifications(page, phone, args.limit, run_dir)

            if not items:
                summary = {"status": "done", "total": 0, "replied": 0,
                           "message": "没有未回复的通知"}
                print(json.dumps(summary, ensure_ascii=False))
                write_json(summary, run_dir / "run_summary.json")
                return

            # ── Generate ──
            print(f"[main] 生成 {len(items)} 条回复...", flush=True)
            brand = load_brand_context()
            custom_replies = get_enabled_replies(data)
            replies = classify_and_reply(items, brand, model=args.model, custom_replies=custom_replies)
            if custom_replies:
                print(f"[main] 使用账号自定义回复 ({len(custom_replies)}条)", flush=True)
            write_json({"items": replies, "total": len(replies)},
                       run_dir / "replies.json")

            # ── Send ──
            to_send = [r for r in replies
                       if not r.get("needs_human") and not r.get("skipped")
                       and r.get("reply")]
            print(f"[main] 发送 {len(to_send)}/{len(replies)} 条...", flush=True)

            sent = 0
            sent_ids = set()  # track within-session to avoid re-replying
            send_idx = 0
            last_reply_ts = ""
            for dom_idx, item in enumerate(replies):
                if item.get("needs_human") or item.get("skipped") or not item.get("reply"):
                    continue
                sid = item["source_id"]
                if sid in sent_ids:
                    print(f"[main] skip already-sent: {sid}", flush=True)
                    send_idx += 1
                    continue
                if send_one_reply(page, item):
                    append_replied_id("comment", sid)
                    sent_ids.add(sid)
                    sent += 1
                    item_ts = item.get("timestamp", "")
                    if item_ts and (not last_reply_ts or item_ts > last_reply_ts):
                        last_reply_ts = item_ts
                send_idx += 1
                if send_idx < len(to_send):
                    delay = random.randint(5, 10)
                    print(f"[main] wait {delay}s...", flush=True)
                    time.sleep(delay)

            # Save last reply timestamp to SQLite
            if last_reply_ts:
                from enterprise_db import set_last_reply_ts
                set_last_reply_ts(last_reply_ts, phone)
                print(f"[main] 已记录最后回复时间: {last_reply_ts}", flush=True)

            summary = {
                "status": "done",
                "run_id": run_dir.name,
                "total": len(items),
                "replied": sent,
                "needs_human": sum(1 for r in replies if r.get("needs_human")),
                "skipped_spam": sum(1 for r in replies if r.get("skipped")),
                "last_reply_ts": last_reply_ts,
                "completed_at": datetime.now(TZ_CN).isoformat(),
            }
            print(json.dumps(summary, ensure_ascii=False))
            write_json(summary, run_dir / "run_summary.json")

        finally:
            context.close()


if __name__ == "__main__":
    main()
