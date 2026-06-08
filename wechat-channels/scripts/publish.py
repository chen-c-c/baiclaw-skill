#!/usr/bin/env python3
# @author FondaWu
"""
微信视频号图文自动发布：Playwright 操作 channels.weixin.qq.com 完成图文发布。

调用方式：
  python publish.py --draft-json <draft.json 路径> [--account-id <id>] [--headless false]

draft.json 必须包含字段：
  title   — 标题（≤22字）
  article — 描述文案（≤1000字）
  topics  — 话题标签列表（["#标签1", "#标签2"]）
  images  — 图片绝对路径列表（最多18张）

输出（最后一行 JSON）：
  {"success": true, "publishedUrl": "https://channels.weixin.qq.com/..."}
  {"success": false, "error": "...", "cookieExpired": true}
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HOME_URL          = "https://channels.weixin.qq.com/platform"
CREATE_URL        = "https://channels.weixin.qq.com/platform/post/finderNewLifeCreate"
LOGIN_TIMEOUT_MS  = 120_000
PUBLISH_TIMEOUT_MS = 60_000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

# 登录状态检测：URL 包含 platform 且不在登录页
_LOGIN_CHECK_JS = """() => {
    const url = window.location.href;
    if (!url.includes('channels.weixin.qq.com')) return false;
    if (url.includes('/login') || url.includes('passport.weixin.qq.com')) return false;
    return document.querySelector('.finder-ui-desktop-menu') !== null
        || document.querySelector('.weui-desktop-btn') !== null;
}"""


# ── Cookie / Profile 路径管理 ──────────────────────────────────────────────────


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "browser-profiles" / f"wechat-channels-{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cookie_cache_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"wechat-channels-{account_id}.json"


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
                "domain":   c.get("domain", ".weixin.qq.com"),
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
                "domain": ".weixin.qq.com", "path": "/",
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
        title   = draft.get("title", "")[:22]
        article = draft.get("article", "")[:1000]
        topics  = draft.get("topics", [])

        # 描述 = 正文 + 话题标签
        topics_str = " ".join(t if t.startswith("#") else f"#{t}" for t in topics)
        full_desc = article
        if topics_str and topics_str not in article:
            full_desc = (article + "\n" + topics_str)[:1000]

        # ── 步骤1: 导航到视频号首页验证登录 ───────────────────────────
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        print(f"[info] 当前 URL: {page.url}", flush=True)

        if "passport.weixin.qq.com" in page.url or "/login" in page.url:
            return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}

        # ── 步骤2: 验证登录状态 ────────────────────────────────────────
        logged_in = False
        try:
            page.locator(".finder-ui-desktop-menu, .weui-desktop-btn").first.wait_for(
                state="visible", timeout=15_000
            )
            logged_in = True
        except Exception:
            pass

        if not logged_in:
            return {"success": False, "error": "Session 已失效，需要重新登录", "cookieExpired": True}
        print("[info] 登录验证通过", flush=True)

        # ── 步骤3: 点击「图文」菜单进入图文列表页 ──────────────────────
        _human_delay(page, 800, 1500)
        try:
            menu_item = page.locator(".finder-ui-desktop-sub-menu__item").filter(has_text="图文").first
            menu_item.wait_for(state="visible", timeout=10_000)
            menu_item.click()
            print("[info] 已点击「图文」菜单", flush=True)
            _human_delay(page, 1000, 2000)
        except Exception as e:
            print(f"[warn] 图文菜单点击失败，直接导航: {e}", file=sys.stderr)
            page.goto("https://channels.weixin.qq.com/platform/post/finderNewLifePostList",
                      wait_until="domcontentloaded", timeout=30_000)
            _human_delay(page, 1000, 1500)

        # ── 步骤4: 点击「发表图文」按钮 ────────────────────────────────
        _human_delay(page, 500, 1000)
        try:
            pub_btn = page.locator("button.weui-desktop-btn.weui-desktop-btn_primary").filter(
                has_text="发表图文"
            ).first
            pub_btn.wait_for(state="visible", timeout=10_000)
            pub_btn.click()
            print("[info] 已点击「发表图文」", flush=True)
        except Exception as e:
            print(f"[warn] 发表图文按钮失败，直接导航: {e}", file=sys.stderr)
            page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=30_000)

        _human_delay(page, 1500, 2500)
        print(f"[info] 编辑页 URL: {page.url}", flush=True)

        # ── 步骤5: 上传图片 ──────────────────────────────────────────
        images = draft.get("images", [])
        valid_images = [img for img in images if Path(img).exists()]

        if not valid_images:
            print("[warn] 未找到有效图片，跳过上传步骤", file=sys.stderr)
        else:
            _human_delay(page, 500, 1000)
            file_input = page.locator("input[type='file']").first
            if file_input.count() > 0:
                try:
                    file_input.set_input_files(valid_images)
                    _human_delay(page, 5000 + len(valid_images) * 1500, 8000 + len(valid_images) * 1500)
                    print(f"[info] 已上传 {len(valid_images)} 张图片", flush=True)
                except Exception as e:
                    print(f"[warn] file input 上传失败，尝试点击方式: {e}", file=sys.stderr)
                    _upload_via_click(page, valid_images)
            else:
                _upload_via_click(page, valid_images)

            _human_delay(page, 2000, 3000)

        # ── 步骤6: 填写标题 ──────────────────────────────────────────
        _human_delay(page, 500, 1000)
        title_input = page.locator("input[placeholder='填写标题, 22个字符内']")
        title_input.wait_for(state="visible", timeout=15_000)
        title_input.click()
        _human_delay(page, 300, 600)
        _type_human(page, title)
        print(f"[info] 标题已填写：{title}", flush=True)

        # ── 步骤7: 填写描述（contenteditable）───────────────────────
        _human_delay(page, 500, 1000)
        desc_el = page.locator("div[contenteditable][data-placeholder='添加描述, 1000个字符内']")
        desc_el.wait_for(state="visible", timeout=15_000)
        desc_el.click()
        _human_delay(page, 300, 600)
        _type_human(page, full_desc)
        print(f"[info] 描述已填写（{len(full_desc)}字）", flush=True)

        # 描述区失焦
        _human_delay(page, 800, 1200)
        title_input.click()
        _human_delay(page, 500, 800)

        # ── 步骤8: 点击「发表」按钮 ──────────────────────────────────
        _human_delay(page, 1000, 2000)
        publish_btn = page.locator("button.weui-desktop-btn.weui-desktop-btn_primary").filter(
            has_text="发表"
        ).last
        if publish_btn.count() == 0:
            publish_btn = page.get_by_role("button", name="发表").last

        publish_btn.wait_for(state="visible", timeout=15_000)
        publish_btn.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        publish_btn.click()
        print("[info] 已点击「发表」按钮", flush=True)

        # ── 步骤9: 等待发布完成 ──────────────────────────────────────
        published_url = ""
        success = False

        try:
            page.locator("text=发布成功, text=发表成功").wait_for(
                state="visible", timeout=PUBLISH_TIMEOUT_MS
            )
            success = True
            print("[info] 检测到发布成功提示", flush=True)
        except Exception:
            pass

        if not success:
            try:
                page.wait_for_url(
                    lambda url: "finderNewLifeCreate" not in url,
                    timeout=PUBLISH_TIMEOUT_MS,
                )
                success = True
                print(f"[info] 页面已离开编辑页，URL: {page.url}", flush=True)
            except Exception:
                pass

        if success:
            published_url = page.url
            return {"success": True, "publishedUrl": published_url}

        return {"success": False, "error": "发布超时，请在管理后台手动确认"}

    except Exception as e:
        return {"success": False, "error": str(e), "cookieExpired": False}


def _upload_via_click(page, valid_images: list):
    try:
        with page.expect_file_chooser() as fc_info:
            upload_area = page.locator(".upload-content, [class*='upload']").first
            upload_area.click(timeout=5_000)
        fc_info.value.set_files(valid_images)
        page.wait_for_timeout(5000 + len(valid_images) * 1500)
        print(f"[info] 已上传 {len(valid_images)} 张图片（file chooser）", flush=True)
    except Exception as e:
        print(f"[warn] 文件选择器上传失败: {e}", file=sys.stderr)


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
    """弹出浏览器等待手动扫码登录，登录后保存 Cookie 并继续发布"""
    context = _persistent_context(p, account_id, headless=False)
    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        already = False
        try:
            page.wait_for_function(_LOGIN_CHECK_JS, timeout=8_000)
            already = True
        except Exception:
            pass

        if not already:
            print("[info] 请在浏览器中完成微信扫码登录（最长 2 分钟）...", flush=True)
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
            from enterprise_db import get_enterprise_data, get_wechat_channels_cookie
            data = get_enterprise_data()
            cookie_str = get_wechat_channels_cookie(data) if data else None
            if cookie_str:
                cookies = parse_cookie_string(cookie_str)
                print(f"[info] 使用 SQLite cookie 登录（{len(cookies)} 条）", flush=True)
                result = _publish_with_cookies(p, cookies, draft, headless=headless)
                if not result.get("cookieExpired"):
                    return result
                print("[warn] SQLite cookie 已失效，回退到手动登录", file=sys.stderr)
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

        # 兜底：弹出浏览器手动扫码登录
        return _login_then_publish(p, account_id, draft)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  default="",    help="视频号账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"], help="是否无头浏览器")
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    if not args.account_id:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_wechat_channels_account
            data = get_enterprise_data()
            acc  = get_wechat_channels_account(data) if data else None
            args.account_id = acc.get("id", "") if acc else ""
            if args.account_id:
                print(f"[info] 自动获取视频号账号: {args.account_id}", flush=True)
        except ImportError:
            pass

    if not args.account_id:
        print("[error] 未指定 --account-id 且未找到企业视频号账号", file=sys.stderr)
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
