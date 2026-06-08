#!/usr/bin/env python3
# @author FondaWu
"""
抖音视频自动发布：Playwright 操作 creator.douyin.com 完成短视频发布。

调用方式：
  python publish.py --draft-json <draft.json 路径> [--account-id <id>] [--headless false]

draft.json 必须包含字段：
  title   — 标题（≤30字）
  article — 描述文案
  topics  — 话题标签列表（["#标签1", "#标签2"]）
  video   — 视频文件绝对路径（mp4）

输出（最后一行 JSON）：
  {"success": true, "publishedUrl": "https://www.douyin.com/video/..."}
  {"success": false, "error": "...", "cookieExpired": true}
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHECK_URL          = "https://creator.douyin.com/"
HOME_URL           = "https://creator.douyin.com/creator-micro/home"
LOGIN_TIMEOUT_MS   = 120_000
PUBLISH_TIMEOUT_MS = 60_000
VIDEO_UPLOAD_TIMEOUT_MS = 180_000  # 视频上传+处理最长 3 分钟

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

# 登录状态检测：页面有 div.title-HvY9Az 且文本为"发布视频"
_LOGIN_CHECK_JS = """() => {
    const url = window.location.href;
    if (!url.includes('creator.douyin.com')) return false;
    if (url.includes('/login')) return false;
    const els = document.querySelectorAll('div.title-HvY9Az');
    for (const el of els) {
        if (el.textContent.trim() === '发布视频') return true;
    }
    return false;
}"""


# ── Cookie / Profile 路径管理 ──────────────────────────────────────────────────


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "browser-profiles" / f"douyin-{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cookie_cache_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"douyin-{account_id}.json"


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
    """支持 Playwright JSON 数组格式（含 expirationDate）和 key=value; 字符串格式"""
    stripped = cookie_str.strip()
    if stripped.startswith("["):
        raw_list = json.loads(stripped)
        result = []
        for c in raw_list:
            cookie = {
                "name":     c.get("name", ""),
                "value":    c.get("value", ""),
                "domain":   c.get("domain", ".douyin.com"),
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
                "domain": ".douyin.com", "path": "/",
                "secure": True, "httpOnly": False, "sameSite": "Lax",
            })
    return cookies


# ── 内部工具函数 ───────────────────────────────────────────────────────────────


def _human_delay(page, min_ms: int = 500, max_ms: int = 1500):
    page.wait_for_timeout(min_ms + random.randint(0, max_ms - min_ms))


def _type_human(page, text: str):
    if not text:
        return
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(30 + random.randint(0, 60))


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


# ── 核心发布流程 ───────────────────────────────────────────────────────────────


def _run_publish_steps(page, draft: dict) -> dict:
    try:
        title   = draft.get("title", "")[:30]
        article = draft.get("article", "")
        topics  = draft.get("topics", [])
        video   = draft.get("video", "")

        topics_str = " ".join(t if t.startswith("#") else f"#{t}" for t in topics)
        full_desc = article
        if topics_str and topics_str not in article:
            full_desc = article + "\n" + topics_str

        # ── 步骤1: 导航到创作者主页并验证登录 ─────────────────────────
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        print(f"[info] 当前 URL: {page.url}", flush=True)

        if "/login" in page.url:
            return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}

        # ── 步骤2: 检测登录状态（等待"发布视频"按钮出现）─────────────
        logged_in = False
        try:
            page.locator("div.title-HvY9Az").filter(has_text="发布视频").wait_for(
                state="visible", timeout=20_000
            )
            logged_in = True
        except Exception:
            pass

        if not logged_in:
            try:
                page.locator("[class*='avatar'], [class*='user-info']").first.wait_for(timeout=5_000)
                logged_in = True
            except Exception:
                pass

        if not logged_in:
            return {"success": False, "error": "Session 已失效，需要重新登录", "cookieExpired": True}
        print("[info] 登录验证通过", flush=True)

        # ── 步骤3: 点击"发布视频" ────────────────────────────────────
        _human_delay(page, 800, 1500)
        page.locator("div.title-HvY9Az").filter(has_text="发布视频").click()
        print("[info] 已点击「发布视频」", flush=True)

        # ── 步骤4: 等待上传页面出现 ──────────────────────────────────
        _human_delay(page, 1000, 2000)
        print(f"[info] 上传页 URL: {page.url}", flush=True)

        # ── 步骤5: 上传视频文件 ──────────────────────────────────────
        if not video or not Path(video).exists():
            return {"success": False, "error": f"视频文件不存在: {video}"}

        # 优先尝试直接 file input 注入
        uploaded = False
        try:
            file_input = page.locator("input[type='file']").first
            if file_input.count() > 0:
                file_input.set_input_files(str(video))
                uploaded = True
                print(f"[info] 已注入视频文件（file input）: {video}", flush=True)
        except Exception as e:
            print(f"[warn] file input 注入失败: {e}", file=sys.stderr)

        if not uploaded:
            # 回退：点击"上传视频"按钮，通过 file chooser 上传
            try:
                upload_btn = page.locator("button.container-drag-btn-k6XmB4").or_(
                    page.locator("button:has-text('上传视频')")
                ).first
                with page.expect_file_chooser(timeout=10_000) as fc_info:
                    upload_btn.wait_for(state="visible", timeout=15_000)
                    upload_btn.click()
                fc_info.value.set_files(str(video))
                uploaded = True
                print(f"[info] 已上传视频（file chooser）: {video}", flush=True)
            except Exception as e:
                return {"success": False, "error": f"视频上传失败: {e}"}

        # ── 步骤6: 等待视频处理完成（标题输入框出现为信号）───────────
        print("[info] 等待视频处理完成...", flush=True)
        title_input = page.locator("input[placeholder='填写作品标题，为作品获得更多流量']")
        try:
            title_input.wait_for(state="visible", timeout=VIDEO_UPLOAD_TIMEOUT_MS)
            print("[info] 视频处理完成", flush=True)
        except Exception:
            return {"success": False, "error": "视频上传/处理超时，请检查视频格式"}

        # ── 步骤7: 填写标题 ──────────────────────────────────────────
        _human_delay(page, 500, 1000)
        title_input.click()
        _human_delay(page, 300, 600)
        _type_human(page, title)
        print(f"[info] 标题已填写：{title}", flush=True)

        # ── 步骤8: 填写简介（contenteditable）───────────────────────
        _human_delay(page, 500, 1000)
        desc_el = page.locator("div[data-placeholder='添加作品简介']")
        desc_el.wait_for(state="visible", timeout=10_000)
        desc_el.click()
        _human_delay(page, 300, 600)
        _type_human(page, full_desc)
        print(f"[info] 简介已填写（{len(full_desc)}字）", flush=True)

        # 失焦，等待话题标签解析
        _human_delay(page, 800, 1200)
        title_input.click()
        _human_delay(page, 500, 800)

        # ── 步骤9: 点击发布 ──────────────────────────────────────────
        _human_delay(page, 1000, 2000)
        publish_btn = page.locator("button.button-dhlUZE.primary-cECiOJ:has-text('发布')")
        if publish_btn.count() == 0:
            publish_btn = page.get_by_role("button", name="发布").last

        publish_btn.wait_for(state="visible", timeout=15_000)
        publish_btn.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        publish_btn.click()
        print("[info] 已点击「发布」按钮", flush=True)

        # ── 步骤10: 等待发布完成 ─────────────────────────────────────
        published_url = ""
        success = False

        try:
            page.locator("text=发布成功").wait_for(state="visible", timeout=PUBLISH_TIMEOUT_MS)
            success = True
            print("[info] 检测到「发布成功」提示", flush=True)
        except Exception:
            pass

        if not success:
            try:
                page.wait_for_url(
                    lambda url: "/upload" not in url and "/home" not in url,
                    timeout=PUBLISH_TIMEOUT_MS,
                )
                success = True
                print(f"[info] 页面已跳转: {page.url}", flush=True)
            except Exception:
                pass

        if success:
            published_url = page.url
            try:
                link = page.locator("a[href*='douyin.com/video'], a[href*='douyin.com/user']").first
                if link.count() > 0:
                    published_url = link.get_attribute("href") or published_url
            except Exception:
                pass
            return {"success": True, "publishedUrl": published_url}

        return {"success": False, "error": "发布超时，请在管理后台手动确认"}

    except Exception as e:
        return {"success": False, "error": str(e), "cookieExpired": False}


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


def _login_then_publish(p, account_id: str, draft: dict, headless: bool = True) -> dict:
    """持久 Profile 发布。headless=True 时检测 session，无 session 返回 sessionMissing；
    headless=False 时弹窗等待扫码登录。"""
    context = _persistent_context(p, account_id, headless=headless)
    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        already = False
        try:
            page.wait_for_function(_LOGIN_CHECK_JS, timeout=8_000)
            already = True
        except Exception:
            pass

        if not already:
            if headless:
                print("[info] 持久 Profile 无有效 session，跳过", flush=True)
                return {"sessionMissing": True}
            print("[info] 请在浏览器中完成扫码登录（最长 2 分钟）...", flush=True)
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
        # 步骤1：持久 Profile headless 检测 session，有则直接发布
        result = _login_then_publish(p, account_id, draft, headless=True)
        if not result.get("sessionMissing"):
            return result
        print("[info] Profile 无 session，尝试 Cookie 方式", flush=True)

        # 步骤2：SQLite cookie
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_douyin_cookie
            data = get_enterprise_data()
            cookie_str = get_douyin_cookie(data) if data else None
            if cookie_str:
                cookies = parse_cookie_string(cookie_str)
                print(f"[info] 使用 SQLite cookie 登录（{len(cookies)} 条）", flush=True)
                result = _publish_with_cookies(p, cookies, draft, headless=headless)
                if not result.get("cookieExpired"):
                    return result
                print("[warn] SQLite cookie 已失效，继续尝试缓存", file=sys.stderr)
        except ImportError:
            pass

        # 步骤3：本地缓存 cookie
        cached = load_cookies_safe(account_id)
        if cached:
            print(f"[info] 使用本地缓存 cookie（{len(cached)} 条）", flush=True)
            result = _publish_with_cookies(p, cached, draft, headless=headless)
            if not result.get("cookieExpired"):
                return result
            print("[warn] 本地缓存 cookie 已失效，回退到手动登录", file=sys.stderr)

        # 步骤4：弹窗扫码登录
        return _login_then_publish(p, account_id, draft, headless=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  default="",    help="抖音账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"])
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    if not args.account_id:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_douyin_account
            data = get_enterprise_data()
            acc  = get_douyin_account(data) if data else None
            args.account_id = acc.get("id", "") if acc else ""
            if args.account_id:
                print(f"[info] 自动获取抖音账号: {args.account_id}", flush=True)
        except ImportError:
            pass

    if not args.account_id:
        print("[error] 未指定 --account-id 且未找到企业抖音账号", file=sys.stderr)
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
