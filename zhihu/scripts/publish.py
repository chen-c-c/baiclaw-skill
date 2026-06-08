#!/usr/bin/env python3
# @author FondaWu
"""
知乎文章自动发布：Playwright 操作 zhuanlan.zhihu.com 完成文章发布。

调用方式：
  python publish.py --draft-json <draft.json 路径> [--account-id <id>] [--headless false]

draft.json 必须包含字段：
  title   — 标题（≤100字）
  article — 正文（1000~3000字）
  images  — 封面图绝对路径列表（取 images[0]，可为空）

输出（最后一行 JSON）：
  {"success": true, "publishedUrl": "https://zhuanlan.zhihu.com/p/..."}
  {"success": false, "error": "...", "cookieExpired": true}
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHECK_URL         = "https://www.zhihu.com"
WRITE_URL         = "https://zhuanlan.zhihu.com/write"
LOGIN_TIMEOUT_MS  = 120_000
PUBLISH_TIMEOUT_MS = 60_000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

_LOGIN_CHECK_JS = """() => {
    const url = window.location.href;
    if (url.includes('signin') || url.includes('passport.zhihu.com')) return false;
    return document.querySelector('.AppHeader-profileEntry') !== null
        || document.querySelector('[class*="userAvatar"]') !== null
        || document.querySelector('.Avatar') !== null;
}"""


# ── Cookie / Profile 路径管理 ──────────────────────────────────────────────────


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "browser-profiles" / f"zhihu-{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cookie_cache_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"zhihu-{account_id}.json"


def save_cookies_safe(context, account_id: str):
    try:
        cookies = context.cookies()
        path = get_cookie_cache_path(account_id)
        path.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        print(f"[info] Cookie 已缓存（{len(cookies)} 条）: {path}", flush=True)
    except Exception as e:
        print(f"[warn] Cookie 缓存失败: {e}", file=sys.stderr)


def load_cookies_safe(account_id: str) -> list[dict] | None:
    path = get_cookie_cache_path(account_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) and data else None
    except Exception:
        return None


def parse_cookie_string(cookie_str: str) -> list[dict]:
    """支持 Playwright JSON 数组格式和 key=value; 字符串格式"""
    stripped = cookie_str.strip()
    if stripped.startswith("["):
        raw_list = json.loads(stripped)
        result = []
        for c in raw_list:
            cookie = {
                "name":     c.get("name", ""),
                "value":    c.get("value", ""),
                "domain":   c.get("domain", ".zhihu.com"),
                "path":     c.get("path", "/"),
                "secure":   bool(c.get("secure", False)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            exp = c.get("expires") or c.get("expirationDate")
            if exp and float(exp) > 0:
                cookie["expires"] = float(exp)
            same_site = c.get("sameSite", "")
            cookie["sameSite"] = same_site if same_site in ("Strict", "Lax", "None") else "Lax"
            if cookie["name"]:
                result.append(cookie)
        return result
    cookies = []
    for part in stripped.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            cookies.append({
                "name": name.strip(), "value": value.strip(),
                "domain": ".zhihu.com", "path": "/",
                "secure": True, "httpOnly": False, "sameSite": "Lax",
            })
    return cookies


# ── 内部工具函数 ───────────────────────────────────────────────────────────────


def _human_delay(page, min_ms: int = 500, max_ms: int = 1500):
    page.wait_for_timeout(min_ms + random.randint(0, max_ms - min_ms))


def _persistent_context(p, account_id: str, headless: bool):
    profile_dir = get_profile_dir(account_id)
    print(f"[info] 浏览器 Profile: {profile_dir}", flush=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=_UA,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )


def _is_logged_in(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => document.querySelector('.AppHeader-profileEntry, [class*=\"userAvatar\"], .Avatar') !== null"
        ))
    except Exception:
        return False


# ── DraftJS 正文注入 ───────────────────────────────────────────────────────────


def _paste_to_draftjs(page, text: str):
    """向 Draft.js 编辑器注入正文，必须用 ClipboardEvent 才能触发 React state 更新。"""
    page.wait_for_selector(".public-DraftEditor-content[contenteditable='true']", timeout=15_000)
    page.locator(".public-DraftEditor-content[contenteditable='true']").first.click()

    content_written = page.evaluate("""(content) => {
        const el = document.querySelector('.public-DraftEditor-content[contenteditable]');
        if (!el) return false;
        el.focus();
        const dt = new DataTransfer();
        dt.setData('text/plain', content);
        el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
        return true;
    }""", text)

    if not content_written:
        print("[warn] paste 事件注入失败，降级为键盘输入", file=sys.stderr)
        page.locator(".public-DraftEditor-content[contenteditable='true']").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(text)

    page.wait_for_timeout(1000)
    print(f"[info] 正文已填写（{len(text)}字）", flush=True)


# ── 核心发布流程 ───────────────────────────────────────────────────────────────


def _run_publish_steps(page, draft: dict) -> dict:
    try:
        title   = draft.get("title", "")[:100]
        article = draft.get("article", "")
        images  = draft.get("images") or []
        cover   = images[0] if images else ""

        # ── 步骤1: 导航到知乎首页验证登录 ──────────────────────────────
        page.goto(CHECK_URL, wait_until="load", timeout=60_000)
        print(f"[info] 当前 URL: {page.url}", flush=True)

        if not _is_logged_in(page):
            if "signin" in page.url or "passport.zhihu.com" in page.url:
                return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}
            return {"success": False, "error": "Session 已失效，需要重新登录", "cookieExpired": True}
        print("[info] 登录验证通过", flush=True)

        # ── 步骤2: 导航到写文章页面 ────────────────────────────────────
        _human_delay(page, 800, 1500)
        page.goto(WRITE_URL, wait_until="networkidle", timeout=30_000)
        _human_delay(page, 1500, 2500)
        print(f"[info] 编辑页 URL: {page.url}", flush=True)

        # ── 步骤3: 填写标题（fill 触发 React 更新）────────────────────
        page.wait_for_selector("textarea[placeholder*='请输入标题']", timeout=15_000)
        page.locator("textarea[placeholder*='请输入标题']").first.fill(title)
        print(f"[info] 标题已填写：{title}", flush=True)

        # ── 步骤4: 填写正文（DraftJS ClipboardEvent）──────────────────
        _human_delay(page, 800, 1200)
        _paste_to_draftjs(page, article)

        # 等待发布按钮从 disabled 变为可点击（Draft.js 渲染完成的标志）
        try:
            page.wait_for_selector(
                "button.Button--primary.Button--blue:not([disabled])",
                timeout=10_000,
            )
            print("[info] 发布按钮已就绪", flush=True)
        except Exception:
            print("[warn] 等待发布按钮就绪超时，继续尝试", file=sys.stderr)

        # ── 步骤5: 上传封面图 ─────────────────────────────────────────
        if cover and Path(cover).exists():
            _human_delay(page, 800, 1200)
            try:
                file_input = page.locator("label.UploadPicture-wrapper input[type='file']").first
                file_input.set_input_files(cover)
                print(f"[info] 封面文件已设置: {cover}", flush=True)
                # 等待缩略图出现（确认上传成功）
                page.wait_for_selector("label.UploadPicture-wrapper img", timeout=15_000)
                print("[info] 封面图上传完成", flush=True)
            except Exception as e:
                print(f"[warn] 封面图上传失败，继续发布: {e}", file=sys.stderr)
        else:
            print("[info] 未提供封面图，跳过上传", flush=True)

        # ── 步骤6: 点击「发布」按钮 ──────────────────────────────────
        _human_delay(page, 800, 1500)
        publish_btn = page.locator("button.Button--primary.Button--blue").filter(has_text="发布")
        publish_btn.first.wait_for(state="visible", timeout=15_000)
        publish_btn.first.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        publish_btn.first.click()
        print("[info] 已点击「发布」按钮", flush=True)

        # ── 步骤7: 等待发布完成 ──────────────────────────────────────
        published_url = ""
        success = False

        # 检测发布成功提示（.css-t5fqv4）
        try:
            page.wait_for_selector(".css-t5fqv4", timeout=10_000)
            success = True
            print("[info] 检测到发布成功提示", flush=True)
        except Exception:
            pass

        # 兜底：URL 变为文章详情页
        if not success:
            try:
                page.wait_for_url(
                    lambda url: "zhuanlan.zhihu.com/p/" in url,
                    timeout=PUBLISH_TIMEOUT_MS,
                )
                success = True
                print(f"[info] 页面已跳转到文章页: {page.url}", flush=True)
            except Exception:
                pass

        if success:
            published_url = page.url
            return {"success": True, "publishedUrl": published_url}

        return {"success": False, "error": "发布超时，请在管理后台手动确认"}

    except Exception as e:
        return {"success": False, "error": str(e), "cookieExpired": False}


# ── 浏览器启动策略 ─────────────────────────────────────────────────────────────


def _publish_with_cookies(p, cookies: list[dict], draft: dict, headless: bool) -> dict:
    browser = p.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=_UA,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    context.add_cookies(cookies)
    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        return _run_publish_steps(page, draft)
    finally:
        browser.close()


def _login_then_publish(p, account_id: str, draft: dict) -> dict:
    """弹出浏览器等待手动登录，登录后保存 Cookie 并继续发布"""
    context = _persistent_context(p, account_id, headless=False)
    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        page.goto(CHECK_URL, wait_until="load", timeout=30_000)

        if _is_logged_in(page):
            print("[info] 已是登录状态", flush=True)
        else:
            print("[info] 请在浏览器中完成知乎登录（账号密码或扫码，最长 2 分钟）...", flush=True)
            try:
                page.wait_for_function(_LOGIN_CHECK_JS, timeout=LOGIN_TIMEOUT_MS)
            except Exception:
                return {"success": False, "error": "登录超时", "cookieExpired": True}

        print("[info] 登录成功，保存 Cookie 缓存...", flush=True)
        save_cookies_safe(context, account_id)
        return _run_publish_steps(page, draft)
    finally:
        context.close()


def publish(draft: dict, account_id: str, headless: bool = True) -> dict:
    with sync_playwright() as p:
        # 优先：从 SQLite 读取 cookie 直接注入登录
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_zhihu_cookie
            data = get_enterprise_data()
            cookie_str = get_zhihu_cookie(data) if data else None
            if cookie_str:
                cookies = parse_cookie_string(cookie_str)
                print(f"[info] 使用 SQLite cookie 登录（{len(cookies)} 条）", flush=True)
                result = _publish_with_cookies(p, cookies, draft, headless=headless)
                if not result.get("cookieExpired"):
                    return result
                print("[warn] SQLite cookie 已失效，回退到本地缓存", file=sys.stderr)
        except ImportError:
            pass

        # 次选：本地缓存 cookie
        cached = load_cookies_safe(account_id)
        if cached:
            print(f"[info] 使用本地缓存 cookie（{len(cached)} 条）", flush=True)
            result = _publish_with_cookies(p, cached, draft, headless=headless)
            if not result.get("cookieExpired"):
                return result
            print("[warn] 本地缓存 cookie 已失效，回退到手动登录", file=sys.stderr)

        # 兜底：弹出浏览器手动登录
        return _login_then_publish(p, account_id, draft)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  default="",    help="知乎账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"], help="是否无头浏览器")
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    if not args.account_id:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_zhihu_account
            data = get_enterprise_data()
            acc  = get_zhihu_account(data) if data else None
            args.account_id = acc.get("id", "") if acc else ""
            if args.account_id:
                print(f"[info] 自动获取知乎账号: {args.account_id}", flush=True)
        except ImportError:
            pass

    if not args.account_id:
        print("[error] 未指定 --account-id 且未找到企业知乎账号", file=sys.stderr)
        sys.exit(1)

    raw = open(args.draft_json, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            draft = json.loads(raw.decode(enc))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    else:
        raise ValueError(f"无法解析 draft JSON 文件: {args.draft_json}")

    result = publish(draft, args.account_id, headless=headless)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
