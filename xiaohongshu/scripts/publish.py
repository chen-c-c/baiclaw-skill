#!/usr/bin/env python3
# @author FondaWu
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path


CHECK_URL         = "https://creator.xiaohongshu.com"
LOGIN_TIMEOUT_MS  = 120_000
UPLOAD_TIMEOUT_MS = 30_000
PUBLISH_TIMEOUT_MS = 30_000


def get_cookie_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    base = Path(appdata) / "BaiClaw" / "cookies"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{account_id}.json"


def get_cookie_from_skill_api(account_id: str) -> list[dict] | None:
    port = os.environ.get("BAICLAW_SKILL_API_PORT")
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
        cookie_str = data.get("cookie", "")
        return parse_cookie_string(cookie_str)
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[warn] skillApiServer 调用失败，回退到本地 Cookie: {e}", file=sys.stderr)
        return None


def parse_cookie_string(cookie_str: str) -> list[dict]:
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".xiaohongshu.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
            })
    return cookies


def login_and_save_cookies(p, account_id: str, cookie_path: Path) -> list[dict]:
    print("[info] 首次登录：正在打开浏览器，请手动完成小红书创作者平台登录", flush=True)
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    try:
        page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        pass  # SPA 路由拦截是正常行为

    # 检查是否已在创作平台且已登录（侧边栏出现即表示登录成功）
    try:
        page.wait_for_selector(".menu-container", timeout=5_000)
    except Exception:
        print("[info] 请在浏览器中完成登录，登录后将自动继续（最长等待 2 分钟）", flush=True)
        page.wait_for_url(
            lambda url: "creator.xiaohongshu.com" in url and "login" not in url,
            timeout=LOGIN_TIMEOUT_MS,
        )
        page.wait_for_selector(".menu-container", timeout=30_000)

    print("[info] 检测到登录成功，正在保存 Cookie...", flush=True)
    cookies = context.cookies()
    cookie_path.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    browser.close()
    print(f"[info] Cookie 已保存：{cookie_path}", flush=True)
    return cookies


# ── 内部工具函数 ───────────────────────────────────────────────────────────────


def _human_delay(page, min_ms: int, max_ms: int):
    page.wait_for_timeout(min_ms + random.randint(0, max_ms - min_ms))


def _type_human(page, text: str):
    if not text:
        return
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(40 + random.randint(0, 80))


def _click_publish_button(page):
    # 发布按钮：bg-red 但不含 upload-button（上传图片按钮也是 bg-red）
    btn = page.locator(".d-button.bg-red:not(.upload-button)")
    try:
        btn.wait_for(timeout=30_000)
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        btn.click(timeout=5_000)
        print("[info] 发布按钮已点击", flush=True)
    except Exception as e:
        print(f"[warn] 正常点击失败（{e}），降级为 JS click", file=sys.stderr)
        page.evaluate(
            "document.querySelector('.d-button.bg-red:not(.upload-button)').click()"
        )
        print("[info] 发布按钮已点击（JS 降级）", flush=True)


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


# ── 核心发布流程 ───────────────────────────────────────────────────────────────

def do_publish(p, cookies: list[dict], draft: dict, cookie_path: Path, headless: bool = True) -> dict:
    browser = p.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    context.add_cookies(cookies)
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    try:
        # ── 步骤1: 导航到创作者中心并验证登录 ───────────────────────
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass  # SPA 路由拦截正常
        print(f"[info] 当前 URL: {page.url}", flush=True)

        # 等待创作平台侧边栏渲染（仅登录后才有），最多 20s
        try:
            page.wait_for_selector(".menu-container", timeout=20_000)
        except Exception:
            # 超时：检查是否被重定向到登录页
            if "login" in page.url or "creator.xiaohongshu.com" not in page.url:
                cookie_path.unlink(missing_ok=True)
                return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}
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
        valid_images = [p for p in images if Path(p).exists()]

        # 先试试直接通过 file input 一次上传全部
        file_input = page.locator("input[type='file']").first
        if file_input.count() > 0:
            try:
                file_input.set_input_files(valid_images)
                _human_delay(page, 5000, 7000)
                print(f"[info] 已上传 {len(valid_images)} 张图片（通过 file input）", flush=True)
            except Exception as e:
                print(f"[warn] file input 批量上传失败: {e}", file=sys.stderr)
                # 逐一上传
                for img_path in valid_images:
                    _human_delay(page, 1000, 2000)
                    try:
                        file_input.set_input_files(img_path)
                        _human_delay(page, 3000, 4000)
                        print(f"[info] 已上传: {Path(img_path).name}", flush=True)
                    except Exception as e2:
                        print(f"[warn] 上传失败: {e2}", file=sys.stderr)
        else:
            # 通过文件选择器逐张上传
            for img_path in valid_images:
                _human_delay(page, 1500, 2500)
                try:
                    with page.expect_file_chooser() as fc_info:
                        btn = page.get_by_role("button", name="上传图片").first
                        if btn.count() > 0:
                            btn.click(timeout=5000)
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
        print(f"[info] 图片上传完成", flush=True)

        # ── 步骤5: 填写标题（逐字符模拟人工输入） ───────────────────
        _human_delay(page, 500, 1000)
        titles = draft.get("titles", [""])
        title = titles[0][:20] if titles else ""
        page.get_by_placeholder("填写标题会有更多赞哦").click()
        _human_delay(page, 300, 600)
        _type_human(page, title)
        print(f"[info] 标题：{title}", flush=True)

        # ── 步骤6: 填写正文（逐字符模拟人工输入） ───────────────────
        _human_delay(page, 500, 1000)
        page.get_by_role("textbox").nth(1).click()
        _human_delay(page, 300, 600)
        article = draft.get("article", "")
        _type_human(page, article)
        print(f"[info] 正文已填写（{len(article)}字）", flush=True)

        # ── 步骤7: 关闭话题弹窗（正文含 # 时会自动弹出） ────────────
        _human_delay(page, 500, 1000)
        _dismiss_topic_popup(page)

        # ── 编辑器失焦：点击标题区，发布按钮才会出现 ─────────────────
        page.get_by_placeholder("填写标题会有更多赞哦").click()
        _human_delay(page, 500, 800)
        page.keyboard.press("Escape")
        _human_delay(page, 300, 500)

        # ── 步骤8: 暂存离开 ──────────────────────────────────────────
        _human_delay(page, 2000, 4000)
        # _click_publish_button(page)
        btn_save = page.locator("button.d-button-default:has-text('暂存离开')")
        btn_save.wait_for(timeout=10_000)
        btn_save.scroll_into_view_if_needed()
        _human_delay(page, 300, 600)
        btn_save.click(timeout=5_000)
        print("[info] 已点击「暂存离开」", flush=True)

        # 等待发布后处理
        _human_delay(page, 1000, 2000)

        # 尝试关闭可能出现的确认弹窗
        try:
            # 页面上的确认按钮：如「确认发布」「确定」「发布」等
            confirm_btns = page.locator("button:has-text('确认发布'), button:has-text('确定'), button:has-text('确认'), .d-button.bg-red:has-text('发布')")
            if confirm_btns.count() > 0:
                _human_delay(page, 300, 600)
                confirm_btns.first.click()
                print("[info] 发布了确认弹窗", flush=True)
                _human_delay(page, 2000, 3000)
        except Exception:
            pass

        # 尝试关闭可能出现的隐私/内容提示弹窗
        try:
            dialog = page.locator("[class*='dialog'], [class*='modal'], [class*='popup']")
            if dialog.count() > 0:
                # 查找弹窗中的确认按钮
                dlg_btn = dialog.locator("button:has-text('确认'), button:has-text('发布'), button:has-text('继续')")
                if dlg_btn.count() > 0:
                    dlg_btn.first.click()
                    print("[info] 关闭了弹窗确认", flush=True)
                    _human_delay(page, 2000, 3000)
        except Exception:
            pass

        # 等待发布成功跳转（账号封禁/审核拒绝时不会跳转）
        redirected = False
        try:
            page.wait_for_url(
                lambda url: "success" in url or "published=true" in url or "/explore/" in url or "/note/" in url or "/user/" in url,
                timeout=PUBLISH_TIMEOUT_MS,
            )
            redirected = True
        except Exception:
            pass
        published_url = page.url

        # 判断是否真正发布成功：URL 必须离开编辑器页面
        # 注意：/publish/success 是成功页，不是编辑器
        success_keywords = ["success", "published=true", "/explore/", "/note/", "/user/"]
        editor_keywords = ["/creator/", "editor", "publishNote"]
        is_success_page = any(kw in published_url for kw in success_keywords)
        still_in_editor = any(kw in published_url for kw in editor_keywords)
        if (not redirected and not is_success_page) or (still_in_editor and not is_success_page):
            # 尝试读取页面错误提示
            error_text = ""
            try:
                # 检查各种可能的错误提示
                err_selectors = ".error-message, .toast-error, [class*='error'], [class*='ban'], .toast, [class*='message'], [class*='alert']"
                err_el = page.locator(err_selectors).first
                if err_el.count() > 0 and err_el.is_visible():
                    error_text = err_el.inner_text().strip()
            except Exception:
                pass

            # 检查页面是否有表单验证提示
            if not error_text:
                try:
                    inputs_with_error = page.locator("[class*='error'], [class*='invalid'], [aria-invalid='true']")
                    if inputs_with_error.count() > 0:
                        error_text = f"表单验证错误（{inputs_with_error.count()}处）"
                except Exception:
                    pass

            msg = error_text or "发布后未跳转到成功页面，账号可能被封禁或内容被拒"
            print(f"[warn] 发布可能未成功: {msg}，当前 URL: {published_url}", file=sys.stderr)
            return {"success": False, "error": msg, "publishedUrl": published_url, "cookieExpired": False}

        print(f"[info] 发布成功，URL: {published_url}", flush=True)
        _human_delay(page, 4000, 10000)
        return {"success": True, "publishedUrl": published_url}

    except Exception as e:
        return {"success": False, "error": str(e), "cookieExpired": False}

    finally:
        browser.close()


def publish(draft: dict, account_id: str, headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    cookie_path = get_cookie_path(account_id)

    # 生产模式：从 skillApiServer 获取 Cookie
    try:
        api_cookies = get_cookie_from_skill_api(account_id)
        if api_cookies:
            with sync_playwright() as p:
                return do_publish(p, api_cookies, draft, cookie_path)
    except RuntimeError as e:
        if "COOKIE_EXPIRED" in str(e):
            return {"success": False, "error": "Cookie 已失效，请在管理后台更新", "cookieExpired": True}
        raise

    # MVP 模式：使用本地保存的 Cookie
    with sync_playwright() as p:
        if not cookie_path.exists():
            cookies = login_and_save_cookies(p, account_id, cookie_path)
        else:
            cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
            print(f"[info] 加载本地 Cookie：{cookie_path}", flush=True)

        result = do_publish(p, cookies, draft, cookie_path, headless=headless)

        if result.get("cookieExpired"):
            cookie_path.unlink(missing_ok=True)

        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  required=True, help="draft JSON 文件路径")
    parser.add_argument("--account-id",  required=True, help="发布账号 ID")
    parser.add_argument("--headless",    default="true", choices=["true","false"], help="是否无头浏览器")
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

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
