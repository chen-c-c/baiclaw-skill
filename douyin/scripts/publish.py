#!/usr/bin/env python3
# @author FondaWu
"""
抖音图集自动发布：Playwright 操作 creator.douyin.com 完成图集发布。

调用方式：
  python publish.py --draft-json <draft.json 路径> [--account-id <id>] [--headless false]

draft.json 必须包含字段：
  title   — 标题（≤30字）
  article — 描述文案
  topics  — 话题标签列表（["#标签1", "#标签2"]）
  images  — 图片绝对路径列表

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

CHECK_URL         = "https://creator.douyin.com/"
UPLOAD_URL        = "https://creator.douyin.com/creator-micro/content/upload"
LOGIN_TIMEOUT_MS  = 120_000
PUBLISH_TIMEOUT_MS = 60_000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

# 登录状态检测：页面上有 div.title-HvY9Az 且文本为"发布图文"
_LOGIN_CHECK_JS = """() => {
    const url = window.location.href;
    if (!url.includes('creator.douyin.com')) return false;
    if (url.includes('/login')) return false;
    const els = document.querySelectorAll('div.title-HvY9Az');
    for (const el of els) {
        if (el.textContent.trim() === '发布图文') return true;
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
    # 兜底：semicolon key=value 格式
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

        # 描述 = 正文 + 话题标签（若话题未嵌入正文则追加）
        topics_str = " ".join(t if t.startswith("#") else f"#{t}" for t in topics)
        full_desc = article
        if topics_str and topics_str not in article:
            full_desc = article + "\n" + topics_str

        # ── 步骤1: 导航到创作者服务台并验证登录 ───────────────────────
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        print(f"[info] 当前 URL: {page.url}", flush=True)

        # Cookie 失效：跳转到登录页
        if "/login" in page.url:
            return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}

        # ── 步骤2: 检测登录状态（等待"发布图文"按钮出现）─────────────
        logged_in = False
        try:
            page.locator("div.title-HvY9Az").filter(has_text="发布图文").wait_for(
                state="visible", timeout=20_000
            )
            logged_in = True
        except Exception:
            pass

        if not logged_in:
            # 兜底：等待任意用户信息元素
            try:
                page.locator("[class*='avatar'], [class*='user-info']").first.wait_for(timeout=5_000)
                logged_in = True
            except Exception:
                pass

        if not logged_in:
            return {"success": False, "error": "Session 已失效，需要重新登录", "cookieExpired": True}
        print("[info] 登录验证通过", flush=True)

        # ── 步骤3: 点击"发布图文" ────────────────────────────────────
        _human_delay(page, 800, 1500)
        page.locator("div.title-HvY9Az").filter(has_text="发布图文").click()
        print("[info] 已点击「发布图文」", flush=True)

        # ── 步骤4: 等待上传页加载 ────────────────────────────────────
        try:
            page.wait_for_url("**/upload**", timeout=20_000)
        except Exception:
            pass
        print(f"[info] 上传页 URL: {page.url}", flush=True)

        # ── 步骤5: 上传图片 ──────────────────────────────────────────
        _human_delay(page, 800, 1500)
        images = draft.get("images", [])
        valid_images = [img for img in images if Path(img).exists()]

        if not valid_images:
            print("[warn] 未找到有效图片，跳过上传步骤", file=sys.stderr)
        else:
            # 优先通过 file input 批量上传
            file_input = page.locator("input[type='file']").first
            if file_input.count() > 0:
                try:
                    file_input.set_input_files(valid_images)
                    _human_delay(page, 5000 + len(valid_images) * 1000, 8000 + len(valid_images) * 1000)
                    print(f"[info] 已上传 {len(valid_images)} 张图片", flush=True)
                except Exception as e:
                    print(f"[warn] file input 上传失败，尝试按钮方式: {e}", file=sys.stderr)
                    _upload_via_button(page, valid_images)
            else:
                _upload_via_button(page, valid_images)

            # 等待缩略图渲染完成
            _human_delay(page, 2000, 3000)

            # 封面弹窗：如果出现封面选择，自动选第一张
            try:
                cover_btn = page.locator("button:has-text('设置封面'), button:has-text('选择封面')").first
                if cover_btn.is_visible(timeout=3000):
                    cover_btn.click()
                    _human_delay(page, 1000, 2000)
                    first_cover = page.locator("[class*='cover-item'], [class*='thumbnail']").first
                    if first_cover.is_visible(timeout=3000):
                        first_cover.click()
                        _human_delay(page, 500, 1000)
                    confirm = page.locator("button:has-text('确认'), button:has-text('完成')").first
                    if confirm.is_visible(timeout=3000):
                        confirm.click()
                        _human_delay(page, 500, 1000)
                    print("[info] 已选择封面", flush=True)
            except Exception:
                pass

        # ── 步骤6: 填写标题 ──────────────────────────────────────────
        _human_delay(page, 500, 1000)
        title_input = page.locator("input[placeholder='添加作品标题']")
        title_input.wait_for(state="visible", timeout=15_000)
        title_input.click()
        _human_delay(page, 300, 600)
        _type_human(page, title)
        print(f"[info] 标题已填写：{title}", flush=True)

        # ── 步骤7: 填写描述（contenteditable）───────────────────────
        _human_delay(page, 500, 1000)
        desc_el = page.locator("div[data-placeholder='添加作品描述...']")
        desc_el.wait_for(state="visible", timeout=15_000)
        desc_el.click()
        _human_delay(page, 300, 600)
        _type_human(page, full_desc)
        print(f"[info] 描述已填写（{len(full_desc)}字）", flush=True)

        # 描述区失焦，等待话题标签解析
        _human_delay(page, 800, 1200)
        title_input.click()
        _human_delay(page, 500, 800)

        # ── 步骤8: 点击发布 ──────────────────────────────────────────
        _human_delay(page, 1000, 2000)

        # 尝试精确类名选择器，回退到文本匹配
        publish_btn = page.locator("button.button-dhlUZE.primary-cECiOJ:has-text('发布')")
        if publish_btn.count() == 0:
            publish_btn = page.get_by_role("button", name="发布").last

        publish_btn.wait_for(state="visible", timeout=15_000)
        publish_btn.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        publish_btn.click()
        print("[info] 已点击「发布」按钮", flush=True)

        # ── 步骤9: 等待发布完成 ──────────────────────────────────────
        published_url = ""
        success = False

        # 方式1：检测"发布成功"文字
        try:
            page.locator("text=发布成功").wait_for(state="visible", timeout=PUBLISH_TIMEOUT_MS)
            success = True
            print("[info] 检测到「发布成功」提示", flush=True)
        except Exception:
            pass

        # 方式2：URL 离开 /upload 页
        if not success:
            try:
                page.wait_for_url(
                    lambda url: "/upload" not in url,
                    timeout=PUBLISH_TIMEOUT_MS,
                )
                success = True
                print(f"[info] 页面已离开上传页，URL: {page.url}", flush=True)
            except Exception:
                pass

        if success:
            published_url = page.url
            # 尝试从页面提取作品链接
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


def _upload_via_button(page, valid_images: list):
    """通过文件选择器按钮上传"""
    try:
        with page.expect_file_chooser() as fc_info:
            upload_btn = page.locator("button:has-text('上传图文')").first
            if upload_btn.count() > 0:
                upload_btn.click(timeout=5_000)
            else:
                page.locator("[class*='upload']").first.click(timeout=5_000)
        fc_info.value.set_files(valid_images)
        page.wait_for_timeout(5000 + len(valid_images) * 1000)
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
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        # 快速检测 Profile 是否已有 session（8s）
        already = False
        try:
            page.wait_for_function(_LOGIN_CHECK_JS, timeout=8_000)
            already = True
        except Exception:
            pass

        if not already:
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
        # 优先：从 SQLite 读取 cookie 直接注入登录
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

        # 兜底：弹出浏览器手动扫码登录（持久 Profile）
        return _login_then_publish(p, account_id, draft)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  default="",    help="抖音账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"], help="是否无头浏览器")
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
