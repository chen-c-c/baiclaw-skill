#!/usr/bin/env python3
# @author FondaWu
import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHECK_URL          = "https://creator.xiaohongshu.com"
LOGIN_TIMEOUT_MS   = 120_000
UPLOAD_TIMEOUT_MS  = 30_000
PUBLISH_TIMEOUT_MS = 30_000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

_LOGIN_CHECK_JS = """() => {
    const url = window.location.href;
    if (!url.includes('creator.xiaohongshu.com')) return false;
    if (url.includes('/login')) return false;
    const els = document.querySelectorAll('*');
    for (const el of els) {
        if (el.childElementCount === 0 && el.textContent.trim() === '发布笔记') return true;
    }
    return false;
}"""


# ── Cookie / Profile 路径管理 ──────────────────────────────────────────────────


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "browser-profiles" / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cookie_cache_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{account_id}.json"


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
                "domain":   c.get("domain", ".xiaohongshu.com"),
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
                "domain": ".xiaohongshu.com", "path": "/",
                "secure": True, "httpOnly": False, "sameSite": "Lax",
            })
    return cookies


def get_cookie_from_skill_api(account_id: str) -> list[dict] | None:
    port  = os.environ.get("BAICLAW_SKILL_API_PORT")
    token = os.environ.get("BAICLAW_SKILL_API_TOKEN")
    if not port or not token:
        return None
    try:
        import requests
        resp = requests.get(
            f"http://127.0.0.1:{port}/skill-api/account/{account_id}/cookie",
            headers={"x-skill-token": token},
            timeout=10,
        )
        if resp.status_code == 401:
            raise RuntimeError("COOKIE_EXPIRED")
        resp.raise_for_status()
        data = resp.json()
        return parse_cookie_string(data.get("cookie", ""))
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[warn] skillApiServer 调用失败，回退到 Profile: {e}", file=sys.stderr)
        return None


# ── 内部工具函数 ───────────────────────────────────────────────────────────────


def _human_delay(page, min_ms: int, max_ms: int):
    page.wait_for_timeout(min_ms + random.randint(0, max_ms - min_ms))


def _type_human(page, text: str):
    if not text:
        return
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(40 + random.randint(0, 80))



def _dismiss_topic_popup(page):
    try:
        popup = page.locator("#creator-editor-topic-container")
        if popup.count() > 0 and popup.is_visible():
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            if popup.count() > 0 and popup.is_visible():
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
    except Exception:
        pass


def _input_topics(page, topics: list):
    """逐字输入话题标签，自动点击联想菜单第一项以关联真实话题 ID。"""
    if not topics:
        return
    editor = page.get_by_role("textbox").nth(1)
    # 光标移到末尾，换行
    for _ in range(10):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(10)
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)

    for raw_tag in topics:
        tag = raw_tag.lstrip("#").strip()
        if not tag:
            continue
        editor.type("#")
        page.wait_for_timeout(200)
        for ch in tag:
            editor.type(ch)
            page.wait_for_timeout(50)
        page.wait_for_timeout(1000)

        # 联想菜单出现则点第一项，否则空格结束
        try:
            first_item = page.locator("#creator-editor-topic-container .item").first
            first_item.wait_for(timeout=1500)
            first_item.click()
            print(f"[info] 话题已关联: #{tag}", flush=True)
        except Exception:
            editor.type(" ")
            print(f"[info] 话题无联想，直接输入: #{tag}", flush=True)
        page.wait_for_timeout(500)


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
        # ── 步骤1: 导航到创作者中心并验证登录 ───────────────────────
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        print(f"[info] 当前 URL: {page.url}", flush=True)

        # 登录检测：img.user_avatar 或 .user-info（Java 参考实现），兜底检测"发布笔记"叶节点
        logged_in = False
        try:
            page.locator("img.user_avatar, .user-info").first.wait_for(timeout=20_000)
            logged_in = True
        except Exception:
            pass

        if not logged_in:
            try:
                page.locator("div").filter(has_text=re.compile("^发布笔记$")).first.wait_for(timeout=5_000)
                logged_in = True
            except Exception:
                pass

        if not logged_in:
            return {"success": False, "error": "Session 已失效，需要重新登录", "cookieExpired": True}
        print("[info] 登录验证通过", flush=True)

        # ── 步骤2: 点击"发布笔记" ────────────────────────────────────
        _human_delay(page, 800, 1500)
        page.locator("div").filter(has_text=re.compile("^发布笔记$")).nth(1).click()
        print("[info] 已点击「发布笔记」", flush=True)

        # ── 步骤3: 点击"上传图文" ────────────────────────────────────
        _human_delay(page, 600, 1200)
        page.locator("div").filter(has_text=re.compile("^上传图文$")).nth(2).click()
        print("[info] 已点击「上传图文」", flush=True)

        # ── 步骤4: 上传图片 ──────────────────────────────────────────
        images = draft.get("images", [])
        valid_images = [img for img in images if Path(img).exists()]

        file_input = page.locator("input[type='file']").first
        if file_input.count() > 0:
            try:
                file_input.set_input_files(valid_images)
                _human_delay(page, 5000, 7000)
                print(f"[info] 已上传 {len(valid_images)} 张图片（通过 file input）", flush=True)
            except Exception as e:
                print(f"[warn] file input 批量上传失败: {e}", file=sys.stderr)
                for img_path in valid_images:
                    _human_delay(page, 1000, 2000)
                    try:
                        file_input.set_input_files(img_path)
                        _human_delay(page, 3000, 4000)
                        print(f"[info] 已上传: {Path(img_path).name}", flush=True)
                    except Exception as e2:
                        print(f"[warn] 上传失败: {e2}", file=sys.stderr)
        else:
            for img_path in valid_images:
                _human_delay(page, 1500, 2500)
                try:
                    with page.expect_file_chooser() as fc_info:
                        btn_up = page.get_by_role("button", name="上传图片").first
                        if btn_up.count() > 0:
                            btn_up.click(timeout=5000)
                        else:
                            upload_el = page.locator("[class*='upload']").first
                            if upload_el.count() > 0:
                                upload_el.click(timeout=5000)
                            else:
                                page.mouse.click(270, 360)
                    fc_info.value.set_files(img_path)
                    _human_delay(page, 3000, 4000)
                    print(f"[info] 已上传: {Path(img_path).name}", flush=True)
                except Exception as e:
                    print(f"[warn] 文件选择器上传失败: {e}", file=sys.stderr)
        print("[info] 图片上传完成", flush=True)

        # ── 步骤5: 填写标题 ──────────────────────────────────────────
        _human_delay(page, 500, 1000)
        titles = draft.get("titles", [""])
        title = titles[0][:20] if titles else ""
        page.get_by_placeholder("填写标题会有更多赞哦").click()
        _human_delay(page, 300, 600)
        _type_human(page, title)
        print(f"[info] 标题：{title}", flush=True)

        # ── 步骤6: 填写正文 ──────────────────────────────────────────
        _human_delay(page, 500, 1000)
        page.get_by_role("textbox").nth(1).click()
        _human_delay(page, 300, 600)
        article = draft.get("article", "")
        _type_human(page, article)
        print(f"[info] 正文已填写（{len(article)}字）", flush=True)

        # ── 步骤7: 输入话题标签（逐字输入 + 点击联想菜单关联话题 ID）────
        _human_delay(page, 500, 1000)
        topics = draft.get("topics", [])
        if topics:
            _input_topics(page, topics[:10])
            print(f"[info] 话题标签已输入（{len(topics[:10])}个）", flush=True)
        _dismiss_topic_popup(page)

        # 编辑器失焦：点击标题区，发布按钮才会从 disabled 变为可用
        page.get_by_placeholder("填写标题会有更多赞哦").click()
        _human_delay(page, 500, 800)
        page.keyboard.press("Escape")
        _human_delay(page, 300, 500)

        # ── 步骤8: 暂存离开 ──────────────────────────────────────────
        _human_delay(page, 2000, 4000)
        btn_save = page.locator("button.d-button-default:has-text('暂存离开')")
        btn_save.wait_for(timeout=10_000)
        btn_save.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        btn_save.click(timeout=5_000)
        print("[info] 已点击「暂存离开」", flush=True)

        # ── 步骤9: 处理"确定离开"确认弹窗（可能出现）────────────────
        _human_delay(page, 800, 1500)
        try:
            confirm_btns = page.locator(
                "button:has-text('暂存离开'), button:has-text('确认'), button:has-text('确定')"
            )
            if confirm_btns.count() > 0:
                confirm_btns.first.click()
                print("[info] 已确认离开弹窗", flush=True)
                _human_delay(page, 1000, 2000)
        except Exception:
            pass  # 页面可能已导航，忽略

        # ── 步骤10: 确认已离开编辑器（暂存成功） ────────────────────
        editor_keywords = ["publish/publish", "publishNote", "editor"]
        try:
            current_url = page.url
            if not any(kw in current_url for kw in editor_keywords):
                print(f"[info] 草稿已暂存，已离开编辑器，URL: {current_url}", flush=True)
                return {"success": True, "publishedUrl": current_url}
            # 仍在编辑器内，等待跳转
            page.wait_for_url(
                lambda url: not any(kw in url for kw in editor_keywords),
                timeout=10_000,
            )
            final_url = page.url
            print(f"[info] 草稿已暂存，URL: {final_url}", flush=True)
            return {"success": True, "publishedUrl": final_url}
        except Exception:
            # 页面已关闭/导航，"暂存离开"已执行视为成功
            print("[info] 草稿已暂存（页面已关闭）", flush=True)
            return {"success": True, "publishedUrl": ""}

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


def _login_then_publish(p, account_id: str, draft: dict) -> dict:
    """弹出浏览器等待手动登录，登录后保存 Cookie 并继续发布"""
    try:
        context = _persistent_context(p, account_id, headless=False)
    except Exception as e:
        return {"success": False, "error": f"浏览器启动失败: {e}", "cookieExpired": True}

    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        # 先清除持久 Profile 中对小红书域名的 cookie，确保用户一定看到登录页
        context.clear_cookies()
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        # 检查是否已在登录页
        already = False
        try:
            page.wait_for_function(_LOGIN_CHECK_JS, timeout=8_000)
            already = True
        except Exception:
            pass

        if not already:
            print("\n============================================", flush=True)
            print("[info] 小红书 Cookie 已失效，正在打开浏览器窗口", flush=True)
            print("[info] 请在弹出的浏览器窗口中手动完成登录", flush=True)
            print("[info] 登录完成后脚本将自动继续发布", flush=True)
            print("============================================\n", flush=True)
            try:
                page.wait_for_function(_LOGIN_CHECK_JS, timeout=LOGIN_TIMEOUT_MS)
            except Exception:
                return {"success": False, "error": "登录超时，请重新尝试发布", "cookieExpired": True}

        print("[info] 登录成功，保存 Cookie...", flush=True)
        save_cookies_safe(context, account_id)
        return _run_publish_steps(page, draft)
    finally:
        context.close()


def publish(draft: dict, account_id: str, headless: bool = True) -> dict:
    with sync_playwright() as p:
        # 优先：从 SQLite 读取 cookie，用 headless 模式验证是否有效
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_xhs_cookie
            data = get_enterprise_data()
            cookie_str = get_xhs_cookie(data) if data else None
            if cookie_str:
                cookies = parse_cookie_string(cookie_str)
                print(f"[info] 验证 SQLite cookie（{len(cookies)} 条）...", flush=True)
                result = _publish_with_cookies(p, cookies, draft, headless=True)
                if not result.get("cookieExpired"):
                    return result
                print("[warn] SQLite cookie 已失效，将打开浏览器让您手动登录", file=sys.stderr)
        except ImportError:
            pass

        # 弹出浏览器手工登录（持久 Profile，显示窗口）
        return _login_then_publish(p, account_id, draft)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  default="",    help="发布账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"], help="是否无头浏览器")
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    if not args.account_id:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_xhs_account
            data = get_enterprise_data()
            acc  = get_xhs_account(data) if data else None
            args.account_id = acc.get("id", "") if acc else ""
            if args.account_id:
                print(f"[info] 自动获取小红书账号: {args.account_id}", flush=True)
        except ImportError:
            pass

    if not args.account_id:
        print("[error] 未指定 --account-id 且未找到企业小红书账号", file=sys.stderr)
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
