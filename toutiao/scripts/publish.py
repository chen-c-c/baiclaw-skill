#!/usr/bin/env python3
# @author FondaWu
"""
头条号图文自动发布：Playwright 操作 mp.toutiao.com 完成图文发布。

调用方式：
  python publish.py --draft-json <draft.json 路径> [--account-id <id>] [--headless false]

draft.json 必须包含字段：
  title   — 标题（10-30字）
  article — 正文（300-800字）
  topics  — 话题标签列表（["#标签1", "#标签2"]）
  images  — 图片绝对路径列表

输出（最后一行 JSON）：
  {"success": true, "publishedUrl": "https://www.toutiao.com/article/..."}
  {"success": false, "error": "...", "cookieExpired": true}
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHECK_URL         = "https://mp.toutiao.com/"
CREATE_URL        = "https://mp.toutiao.com/profile_v4/graphic/publish?from=toutiao_pc"
LOGIN_TIMEOUT_MS  = 120_000
PUBLISH_TIMEOUT_MS = 60_000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""


def load_headers_from_file(headers_json_path: str) -> dict:
    """从 JSON 文件加载额外 HTTP 请求头"""
    with open(headers_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Cookie / Profile 路径管理 ──────────────────────────────────────────────────


def get_profile_dir(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "browser-profiles" / f"toutiao-{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cookie_cache_path(account_id: str) -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    d = Path(appdata) / "BaiClaw" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"toutiao-{account_id}.json"


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


def _normalize_toutiao_domain(domain: str) -> str:
    """将所有头条子域名统一为 .toutiao.com，确保 cookie 跨子域名生效"""
    d = domain.strip().lower()
    if d == "toutiao.com" or d.endswith(".toutiao.com"):
        return ".toutiao.com"
    return domain


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
                "domain":   _normalize_toutiao_domain(c.get("domain", ".toutiao.com")),
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
                "domain": ".toutiao.com", "path": "/",
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


def _paste_to_editor(page, text: str):
    """向 contenteditable 编辑器注入正文，使用 ClipboardEvent 触发 React state 更新。"""
    selectors = "[contenteditable='true'], .ProseMirror, div[class*='editor'], div[data-placeholder*='内容'], div[data-placeholder*='正文']"
    try:
        page.wait_for_selector(selectors, timeout=10_000)
    except Exception:
        return False

    editor = page.locator(selectors).first
    editor.click()
    page.wait_for_timeout(300)

    content_written = page.evaluate("""(content) => {
        const el = document.querySelector('[contenteditable], .ProseMirror');
        if (!el) return false;
        el.focus();
        const dt = new DataTransfer();
        dt.setData('text/plain', content);
        el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
        return true;
    }""", text)

    if content_written:
        print(f"[info] 正文已粘贴（{len(text)}字）", flush=True)
        page.wait_for_timeout(1000)
        return True
    return False


def _debug_page_state(page, label: str):
    """打印当前页面状态用于调试"""
    print(f"[debug] {label} — URL: {page.url}", flush=True)


def _dismiss_drawer(page):
    """关闭任何打开的 byte-drawer 遮罩（草稿抽屉、上传抽屉等）"""
    try:
        mask = page.locator(".byte-drawer-mask").first
        if mask.count() > 0 and mask.is_visible(timeout=2000):
            print("[info] 检测到打开的遮罩，正在关闭...", flush=True)
            page.keyboard.press("Escape")
            _human_delay(page, 1000, 2000)
            # 验证是否关闭
            still_open = page.evaluate("""() => {
                const masks = document.querySelectorAll('.byte-drawer-mask');
                for (const m of masks) {
                    if (m.offsetParent !== null) return true;
                }
                return false;
            }""")
            if still_open:
                print("[warn] Escape 未能关闭，强制隐藏遮罩", flush=True)
                page.evaluate("""() => {
                    document.querySelectorAll('.byte-drawer-mask')
                        .forEach(el => el.remove());
                }""")
                _human_delay(page, 500, 1000)
    except Exception:
        pass


def _click_publish_button(page) -> dict:
    """点击发布按钮并处理结果，带重试逻辑。

    流程：
    1. 找页面上的"预览并发布"或"发布"按钮
    2. 点击按钮
    3. 检查是否有弹窗 → 如果有，在弹窗中点击"发布""
    4. 检查发布结果（URL 是否变换/成功提示）
    5. 未成功则重试
    """
    publish_texts = ['预览并发布', '发布']

    for retry in range(3):
        _human_delay(page, 1000, 2000)

        # 找按钮
        clicked = False
        for text in publish_texts:
            btn = page.locator(f"button:has-text('{text}')").last
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                try:
                    btn.click(force=True, timeout=5000)
                    print(f"[info] 点击发布按钮: {text}（第{retry+1}次）", flush=True)
                    clicked = True
                    break
                except Exception:
                    pass
            # JS 兜底
            try:
                page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button');
                    for (let i = all.length - 1; i >= 0; i--) {{
                        const b = all[i];
                        if (b.offsetParent !== null && b.textContent.includes('{text}') && !b.disabled) {{
                            b.click(); return true;
                        }}
                    }}
                    return false;
                }}""")
                print(f"[info] JS 点击发布按钮: {text}", flush=True)
                clicked = True
                break
            except Exception:
                pass

        if not clicked:
            print("[warn] 未找到发布按钮", file=sys.stderr)
            # 截图保存现场
            try:
                page.screenshot(path=r"C:\Users\EDY\AppData\Local\Temp\baiclaw_debug_no_btn.png")
            except Exception:
                pass
            continue

        _human_delay(page, 3000, 5000)

        # 截图保存点击后状态
        try:
            page.screenshot(path=r"C:\Users\EDY\AppData\Local\Temp\baiclaw_debug_after_click.png")
        except Exception:
            pass

        # 查找并点击确认发布弹窗中的按钮
        # 头条发布确认弹窗可能是 byte-drawer、普通弹窗或全页面覆盖层
        # 策略：在整个页面范围搜索可点击的"确认发布"/"确定"/"确认"按钮
        confirm_texts = ['确认发布', '确定发布', '确定', '确认', '发布']
        confirmed = False
        for attempt in range(6):  # 最多重试 6 次，每次间隔 1s
            confirmed = page.evaluate(f"""() => {{
                const texts = {json.dumps(confirm_texts)};
                // 搜索全部可见的按钮、span、div
                const allElements = document.querySelectorAll('button, span, div, a, label');
                const candidates = Array.from(allElements).filter(el => {{
                    if (el.offsetParent === null || el.disabled) return false;
                    const t = el.textContent.trim();
                    return texts.some(ct => t === ct || t.startsWith(ct));
                }});
                if (candidates.length > 0) {{
                    // 优先选择不在隐藏区域的（靠近底部/居中的按钮更可能是确认按钮）
                    const target = candidates[candidates.length - 1];
                    target.click();
                    return 'clicked:' + target.textContent.trim();
                }}
                return '';
            }}""")
            if confirmed:
                print(f"[info] 已点击确认发布按钮: {confirmed}", flush=True)
                _human_delay(page, 2000, 4000)
                break
            _human_delay(page, 800, 1200)
            if attempt == 3:
                # 尝试在 byte-drawer 内查找
                confirmed = page.evaluate("""() => {
                    const dw = document.querySelector('[class*="byte-drawer-wrapper"]');
                    if (!dw || dw.offsetParent === null) return false;
                    const all = dw.querySelectorAll('button, span, div, a');
                    for (let i = all.length - 1; i >= 0; i--) {
                        const el = all[i];
                        if (el.offsetParent === null || el.disabled) continue;
                        const t = el.textContent.trim();
                        if (t === '确定' || t === '确认' || t === '确认发布') {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""")
                if confirmed:
                    print("[info] byte-drawer 中点击了确认按钮", flush=True)
                    _human_delay(page, 2000, 4000)
                    break

        if not confirmed:
            print("[warn] 未找到确认发布按钮", file=sys.stderr)

        # 检查结果：URL 是否脱离了发布页，或页面是否有成功提示
        current_url = page.url
        if "/publish" not in current_url:
            print(f"[info] 发布成功，URL 已离开发布页: {current_url}", flush=True)
            return {"success": True, "publishedUrl": current_url}

        success_text = page.evaluate("""() => {
            const t = document.body.innerText;
            return t.includes('发布成功') || t.includes('提交成功') || t.includes('审核中');
        }""")
        if success_text:
            print("[info] 检测到发布成功提示", flush=True)
            return {"success": True, "publishedUrl": current_url}

        print(f"[info] 发布未完成，准备重试（第{retry+1}次）", flush=True)

    return {"success": False, "error": "发布超时，请在管理后台手动确认"}


def _remove_existing_cover_images(page):
    """移除页面上已有的封面图片（来自草稿），通过点击每张图片的删除按钮"""
    try:
        removed = page.evaluate("""() => {
            // 查找封面图片上的删除/移除按钮（X 图标）
            const dels = document.querySelectorAll(
                '[class*="del"], [class*="delete"], [class*="remove"], [class*="close"], [class*="icon-close"]'
            );
            let count = 0;
            for (const d of dels) {
                if (d.offsetParent !== null && d.closest('.article-cover-area, [class*="cover"]')) {
                    d.click();
                    count++;
                }
            }
            return count;
        }""")
        if removed:
            print(f"[info] 已移除 {removed} 张现有封面图片", flush=True)
            _human_delay(page, 1000, 2000)
    except Exception:
        pass


def _execute_publish_actions(page, draft: dict) -> dict:
    """执行发布步骤，假设已处于登录状态。"""
    try:
        title   = (draft.get("title") or "")[:30]
        article = draft.get("article") or ""
        topics  = draft.get("topics") or []

        # 正文 + 话题标签
        topics_str = " ".join(t if t.startswith("#") else f"#{t}" for t in topics)
        full_article = article
        if topics_str and topics_str not in article:
            full_article = article + "\n\n" + topics_str

        # ── 步骤2: 导航到发布页 ─────────────────────────────────────────
        _human_delay(page, 800, 1500)
        try:
            page.goto(CREATE_URL, wait_until="networkidle", timeout=30_000)
        except Exception:
            pass
        print(f"[info] 发布页 URL: {page.url}", flush=True)
        _human_delay(page, 1500, 2500)
        _debug_page_state(page, "发布页")

        if "/publish" not in page.url:
            try:
                publish_link = page.locator("a[href*='publish'], [class*='publish']").first
                if publish_link.count() > 0:
                    publish_link.click()
                    _human_delay(page, 2000, 3000)
            except Exception:
                pass

        # ── 步骤2b: 草稿检测与处理 ────────────────────────────────────────
        # byte-drawer 草稿弹窗：需要点击"继续编辑"加载草稿，不能直接关闭
        _human_delay(page, 1500, 2500)  # 等待抽屉动画完成
        draft_handled = False
        for draft_text in ['继续编辑', '编辑草稿', '恢复草稿', '编辑', '续写']:
            try:
                el = page.locator(f"text={draft_text}").last
                if el.count() > 0 and el.is_visible(timeout=3000):
                    el.click(force=True, timeout=5000)
                    print(f"[info] 已点击「{draft_text}」加载草稿", flush=True)
                    draft_handled = True
                    _human_delay(page, 5000, 6000)  # 给草稿加载留足时间
                    break
            except Exception:
                pass
            # JS 兜底
            try:
                clicked = page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button, span, div, a');
                    for (const el of all) {{
                        if (el.textContent.includes('{draft_text}') && el.offsetParent !== null) {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {{
                                el.click(); return true;
                            }}
                        }}
                    }}
                    return false;
                }}""")
                if clicked:
                    print(f"[info] JS 点击「{draft_text}」", flush=True)
                    draft_handled = True
                    _human_delay(page, 5000, 6000)
                    break
            except Exception:
                pass

        if not draft_handled:
            _dismiss_drawer(page)
            print("[info] 未检测到草稿弹窗", flush=True)

        # ── 步骤3: 选择封面类型 ────────────────────────────────────────────
        images = draft.get("images") or []
        valid_images = [img for img in images if Path(img).exists()]
        image_count = len(valid_images)
        _select_cover_type(page, image_count)

        # ── 步骤4: 上传图片 ───────────────────────────────────────────────
        _human_delay(page, 500, 1000)
        if not valid_images:
            print("[warn] 未找到有效图片，跳过上传步骤", file=sys.stderr)
        else:
            # 先移除页面中已有的封面图片（来自草稿残留）
            _remove_existing_cover_images(page)

            upload_ok = False

            # 尝试1: 直接找 input[type='file'] 用 set_input_files
            try:
                file_input = page.locator("input[type='file']").first
                if file_input.count() > 0:
                    file_input.set_input_files(valid_images)
                    _human_delay(page, 8000 + image_count * 2000, 12000 + image_count * 2000)
                    print(f"[info] 已上传 {image_count} 张图片", flush=True)
                    upload_ok = True
            except Exception:
                pass

            # 尝试2: 点击上传虚线框 → 侧边抽屉 → 本地上传 → 文件选择器
            if not upload_ok:
                try:
                    # 2a: 点击上传虚线框
                    upload_zone = page.locator(".article-cover-add").first
                    if upload_zone.count() == 0:
                        upload_zone = page.locator(".byte-icon-plus").first.locator("..")
                    if upload_zone.count() > 0:
                        upload_zone.click(force=True, timeout=5000)
                        print("[info] 已点击上传虚线框", flush=True)
                        _human_delay(page, 1500, 3000)
                    else:
                        page.evaluate("""() => {
                            const svg = document.querySelector('.byte-icon-plus');
                            if (svg && svg.parentElement) { svg.parentElement.click(); return true; }
                            const all = document.querySelectorAll('div');
                            for (const d of all) {
                                if (d.style.border && d.style.border.includes('dashed') && d.offsetParent !== null) {
                                    d.click(); return true;
                                }
                            }
                            return false;
                        }""")
                        print("[info] 已通过 JS 点击上传区", flush=True)
                        _human_delay(page, 1500, 3000)

                    # 2b: 等待侧边抽屉打开
                    try:
                        page.wait_for_selector(".byte-drawer-wrapper, [class*='drawer-wrapper'], [class*='byte-drawer']", timeout=8000)
                        print("[info] 上传抽屉已打开", flush=True)
                        _human_delay(page, 1000, 2000)
                    except Exception:
                        print("[warn] 上传抽屉未出现", file=sys.stderr)

                    # 2c: 点击"本地上传" tab
                    local_tab = page.locator(
                        "text=本地上传, "
                        "button:has-text('本地上传'), "
                        "span:has-text('本地上传'), "
                        "div:has-text('本地上传'), "
                        "[class*='tab']:has-text('本地上传')"
                    ).first
                    if local_tab.count() > 0:
                        local_tab.click(force=True, timeout=5000)
                        print("[info] 已点击「本地上传」选项卡", flush=True)
                    else:
                        page.evaluate("""() => {
                            const all = document.querySelectorAll('*');
                            for (const el of all) {
                                const t = el.textContent.trim();
                                if (t.includes('本地上传') && el.offsetParent !== null) {
                                    el.click(); return true;
                                }
                            }
                            return false;
                        }""")
                        print("[info] 已通过 JS 点击「本地上传」选项卡", flush=True)
                    _human_delay(page, 1000, 2000)

                    # 2d: 上传文件
                    fi = page.locator("input[type='file']").first
                    if fi.count() > 0:
                        fi.set_input_files(valid_images)
                        print(f"[info] 正在上传 {image_count} 张图片...", flush=True)
                        _human_delay(page, 8000 + image_count * 2000, 12000 + image_count * 2000)
                        print(f"[info] 已上传 {image_count} 张图片", flush=True)
                        upload_ok = True
                    else:
                        with page.expect_file_chooser(timeout=20_000) as fc_info:
                            page.evaluate("""() => {
                                const dw = document.querySelector('.byte-drawer-wrapper, [class*="drawer-wrapper"]');
                                if (!dw) return;
                                const all = dw.querySelectorAll('div');
                                for (const d of all) {
                                    if (d.offsetParent === null) continue;
                                    const s = getComputedStyle(d);
                                    if (s.borderStyle === 'dashed') { d.click(); return; }
                                }
                            }""")
                            _human_delay(page, 1000, 2000)
                        if fc_info and fc_info.value:
                            fc_info.value.set_files(valid_images)
                            print(f"[info] 正在上传 {image_count} 张图片...", flush=True)
                            _human_delay(page, 8000 + image_count * 2000, 12000 + image_count * 2000)
                            print(f"[info] 已上传 {image_count} 张图片", flush=True)
                            upload_ok = True
                        else:
                            print("[warn] 文件选择器未触发", file=sys.stderr)

                    # 2e: 在抽屉中点击"确定"按钮完成图片上传
                    if upload_ok:
                        _human_delay(page, 1500, 2500)
                        confirmed = page.evaluate("""() => {
                            const dw = document.querySelector('.byte-drawer-wrapper, [class*="drawer-wrapper"]');
                            if (!dw) return false;
                            const btns = dw.querySelectorAll('button');
                            // 从后往前找"确定"或最后一个可用按钮
                            for (let i = btns.length - 1; i >= 0; i--) {
                                const b = btns[i];
                                if (b.offsetParent === null || b.disabled) continue;
                                const t = b.textContent.trim();
                                if (t === '确定' || t === '确认' || t === '完成') {
                                    b.click(); return true;
                                }
                            }
                            // 兜底：最后一个非空按钮
                            for (let i = btns.length - 1; i >= 0; i--) {
                                const b = btns[i];
                                if (b.offsetParent !== null && !b.disabled && b.textContent.trim()) {
                                    b.click(); return true;
                                }
                            }
                            return false;
                        }""")
                        print(f"[info] 确定按钮点击: {'成功' if confirmed else '未找到'}", flush=True)
                        _human_delay(page, 1000, 2000)

                    # 关闭上传抽屉
                    _dismiss_drawer(page)

                except Exception as e:
                    print(f"[warn] 图片上传失败: {e}", file=sys.stderr)

            if not upload_ok:
                print("[warn] 所有上传方法均失败", file=sys.stderr)

            _human_delay(page, 3000, 5000)

        # ── 步骤5: 填写标题 ───────────────────────────────────────────────
        _dismiss_drawer(page)
        _human_delay(page, 500, 1000)
        title_input = page.locator("textarea[placeholder*='标题'], textarea[placeholder*='title']").first
        if title_input.count() == 0:
            title_input = page.locator("textarea").first

        if title_input.count() > 0:
            title_input.click(force=True)
            _human_delay(page, 300, 600)
            title_input.fill("")
            _human_delay(page, 100, 200)
            title_input.fill(title)
            # 验证标题是否写入成功
            title_val = page.evaluate("""() => {
                const ta = document.querySelector('textarea');
                return ta ? ta.value : '';
            }""")
            if title_val and len(title_val) > 0:
                print(f"[info] 标题已填写（验证通过）: {title}", flush=True)
            else:
                print(f"[warn] 标题 fill 后验证为空，尝试 JS 写入", flush=True)
                page.evaluate("""(val) => {
                    const ta = document.querySelector('textarea');
                    if (ta) { ta.value = val; ta.dispatchEvent(new Event('input', { bubbles: true })); }
                }""", title)
        else:
            print("[warn] 未找到标题输入框", file=sys.stderr)

        # ── 步骤6: 填写正文 ────────────────────────────────────────────
        _human_delay(page, 500, 1000)
        # 找 contenteditable 编辑器（不要用 textarea，会匹配到标题）
        article_written = False
        editor = page.locator(
            "[contenteditable='true'], .ProseMirror, "
            "div[data-placeholder*='内容'], div[data-placeholder*='正文']"
        ).first
        if editor.count() > 0:
            try:
                editor.click(force=True)
                _human_delay(page, 300, 600)
                editor.fill("")
                _human_delay(page, 200, 400)
                editor.fill(full_article)
                _human_delay(page, 500, 1000)
                # 验证内容是否写入成功
                written_len = page.evaluate("""() => {
                    const el = document.querySelector('[contenteditable], .ProseMirror');
                    return el ? (el.textContent || '').length : 0;
                }""")
                if written_len >= len(full_article) * 0.5:
                    print(f"[info] 正文已填写（{len(full_article)}字, 验证={written_len}字）", flush=True)
                    article_written = True
                else:
                    print(f"[warn] fill 写入后内容不足({written_len}/{len(full_article)})", flush=True)
            except Exception as e:
                print(f"[warn] editor fill 异常: {e}", flush=True)
        else:
            print("[warn] 未找到 contenteditable 编辑器", file=sys.stderr)

        if not article_written:
            # 兜底1: ClipboardEvent paste
            print("[info] 尝试 ClipboardEvent paste 写入正文", flush=True)
            try:
                _paste_to_editor(page, full_article)
                _human_delay(page, 500, 1000)
                written_len = page.evaluate("""() => {
                    const el = document.querySelector('[contenteditable], .ProseMirror');
                    return el ? (el.textContent || '').length : 0;
                }""")
                if written_len >= len(full_article) * 0.5:
                    print(f"[info] paste 正文验证通过（{written_len}字）", flush=True)
                    article_written = True
            except Exception:
                pass

        if not article_written:
            # 兜底2: 直接 JS 设置 textContent
            print("[info] 尝试 JS textContent 写入正文", flush=True)
            page.evaluate("""(content) => {
                const el = document.querySelector('[contenteditable], .ProseMirror');
                if (!el) return false;
                el.focus();
                el.textContent = content;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""", full_article)
            _human_delay(page, 500, 1000)
            written_len = page.evaluate("""() => {
                const el = document.querySelector('[contenteditable], .ProseMirror');
                return el ? (el.textContent || '').length : 0;
            }""")
            print(f"[info] JS textContent 写入后验证: {written_len}字", flush=True)
            if written_len >= len(full_article) * 0.3:
                article_written = True

        _human_delay(page, 1000, 2000)

        # ── 步骤7: 作品声明 ─────────────────────────────────────────────
        _set_work_declaration(page)

        # ── 步骤7b: 开启投放广告赚收益 ───────────────────────────────────
        _enable_ad_revenue(page)

        # ── 步骤8: 预览并发布（带重试）──────────────────────────────────
        _human_delay(page, 1000, 2000)
        _debug_page_state(page, "发布前")
        _dismiss_drawer(page)

        result = _click_publish_button(page)
        return result

    except Exception as e:
        import traceback
        return {"success": False, "error": f"{e}\n{traceback.format_exc()}", "cookieExpired": False}


def _run_publish_steps(page, draft: dict) -> dict:
    """验证登录状态，然后执行发布步骤。用于 cookie 注入路径。"""
    try:
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
        except Exception:
            pass
        print(f"[info] 当前 URL: {page.url}", flush=True)
        _debug_page_state(page, "首页")

        if "/login" in page.url:
            return {"success": False, "error": "Cookie 已失效，请重新登录", "cookieExpired": True}
        print(f"[info] 登录验证通过（URL: {page.url}）", flush=True)

        return _execute_publish_actions(page, draft)
    except Exception as e:
        return {"success": False, "error": str(e), "cookieExpired": False}


def _select_cover_type(page, image_count: int):
    """根据图片数量选择封面类型：单图/三图/无封面"""
    if image_count <= 0:
        cover_text = "无封面"
    elif image_count == 1:
        cover_text = "单图"
    else:
        cover_text = "三图"

    _human_delay(page, 500, 1000)
    try:
        el = page.locator(f"text={cover_text}").first
        if el.count() > 0 and el.is_visible(timeout=1000):
            el.click()
            print(f"[info] 已选择封面类型：{cover_text}", flush=True)
            _human_delay(page, 500, 1000)
            return
    except Exception:
        pass

    # JS 兜底
    try:
        page.evaluate(f"""() => {{
            const all = document.querySelectorAll('label, span, div, button');
            for (const el of all) {{
                if (el.textContent.trim() === '{cover_text}' && el.offsetParent !== null && el.children.length === 0) {{
                    el.click();
                    return;
                }}
            }}
        }}""")
        print(f"[info] 已通过 JS 选择封面：{cover_text}", flush=True)
    except Exception:
        print(f"[info] 跳过封面选择", flush=True)
    _human_delay(page, 500, 1000)


def _set_work_declaration(page):
    """勾选作品声明：取材网络 + 所有含'仅供参考'的选项"""
    _human_delay(page, 500, 1000)
    targets = ["取材网络", "仅供参考"]
    for target in targets:
        try:
            el = page.locator(f"text={target}").first
            if el.count() > 0 and el.is_visible(timeout=1000):
                el.click()
                print(f"[info] 已勾选作品声明：{target}", flush=True)
                _human_delay(page, 300, 600)
                continue
        except Exception:
            pass
        # JS 兜底
        try:
            page.evaluate(f"""() => {{
                const all = document.querySelectorAll('label, span, div');
                for (const el of all) {{
                    if (el.textContent.includes('{target}') && el.offsetParent !== null) {{
                        const chk = el.querySelector('input[type="checkbox"]');
                        if (chk) {{ chk.click(); return true; }}
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }}""")
            print(f"[info] 已通过 JS 勾选作品声明：{target}", flush=True)
            _human_delay(page, 300, 600)
        except Exception:
            print(f"[warn] 未找到作品声明选项：{target}", file=sys.stderr)


def _enable_ad_revenue(page):
    """开启投放广告赚收益选项"""
    _human_delay(page, 500, 1000)
    targets = ["投放广告赚收益", "投放广告", "广告投放", "广告分成", "收益"]
    for target in targets:
        try:
            el = page.locator(f"text={target}").first
            if el.count() > 0 and el.is_visible(timeout=2000):
                el.click()
                print(f"[info] 已开启广告收益：{target}", flush=True)
                _human_delay(page, 300, 600)
                return True
        except Exception:
            pass
        # JS 兜底：找包含目标文本的 label/span，先检查复选框是否已勾选
        try:
            clicked = page.evaluate(f"""() => {{
                const all = document.querySelectorAll('label, span, div, button');
                for (const el of all) {{
                    if (el.textContent.includes('{target}') && el.offsetParent !== null) {{
                        const chk = el.querySelector('input[type="checkbox"], input[type="radio"]');
                        if (chk) {{ if (!chk.checked) {{ chk.click(); }} return 'checkbox'; }}
                        // 查找附近是否有 switch/checkbox，优先点击未被勾选的
                        const parent = el.closest('label, div[class*="switch"], div[class*="toggle"]');
                        if (parent) {{
                            const innerChk = parent.querySelector('input');
                            if (innerChk && !innerChk.checked) {{ innerChk.click(); return 'switch'; }}
                            if (!innerChk) {{ el.click(); return 'clicked'; }}
                        }} else {{
                            el.click();
                            return 'clicked';
                        }}
                    }}
                }}
                return '';
            }}""")
            if clicked:
                print(f"[info] 已通过 JS 开启广告收益：{target} ({clicked})", flush=True)
                _human_delay(page, 300, 600)
                return True
        except Exception:
            pass
    print("[info] 未找到广告收益选项，跳过", flush=True)
    return False


def _save_cookies_to_admin(account_id: str, cookies_list: list):
    """保存 cookie 到管理后台 PATCH /api/device/publish-accounts/{id}/cookie"""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
        from enterprise_db import get_admin_api_url, get_admin_token, get_enterprise_data, get_toutiao_account

        admin_url = os.environ.get("BAICLAW_ADMIN_API_URL", "").rstrip("/") or get_admin_api_url()
        admin_token = os.environ.get("BAICLAW_ADMIN_TOKEN", "").strip('"') or get_admin_token()
        if not admin_url or not admin_token:
            print("[warn] 缺少管理后台配置，跳过 Cookie 回写", file=sys.stderr)
            return

        data = get_enterprise_data()
        account = get_toutiao_account(data) if data else {}
        import requests
        cookie_json = json.dumps(cookies_list, ensure_ascii=False)
        url = f"{admin_url}/api/device/publish-accounts/{account_id}/cookie"
        resp = requests.patch(
            url,
            json={
                "platform": "toutiao",
                "accountName": account.get("accountName", ""),
                "brandId": account.get("brandId", ""),
                "cookie": cookie_json,
                "remark": "auto-updated by publish.py",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[info] Cookie 已同步到管理后台（{len(cookies_list)} 条）", flush=True)
    except Exception as e:
        print(f"[warn] Cookie 同步到管理后台失败: {e}", file=sys.stderr)


def _publish_with_context(p, cookies: list[dict], draft: dict, headless: bool, extra_headers: dict | None = None) -> dict:
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
    if extra_headers:
        context.set_extra_http_headers(extra_headers)
    page = context.new_page()
    page.add_init_script(_ANTI_BOT_JS)
    try:
        return _run_publish_steps(page, draft)
    finally:
        browser.close()


def test_login(cookies: list[dict], extra_headers: dict | None = None, headless: bool = True) -> dict:
    """仅测试用给定 cookies 和 headers 能否访问 mp.toutiao.com，不执行发布"""
    with sync_playwright() as p:
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
        if extra_headers:
            context.set_extra_http_headers(extra_headers)
        page = context.new_page()
        page.add_init_script(_ANTI_BOT_JS)
        try:
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            print(f"[test_login] 页面 URL: {page.url}", flush=True)
            logged_in = "/login" not in page.url
            return {"success": logged_in, "url": page.url, "cookieExpired": not logged_in}
        except Exception as e:
            return {"success": False, "error": str(e), "cookieExpired": True}
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
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
        except Exception:
            pass

        already = False
        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=8_000)
            already = True
        except Exception:
            pass

        if not already:
            print("[info] 请在浏览器中完成登录（最长 2 分钟）...", flush=True)
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=LOGIN_TIMEOUT_MS)
            except Exception:
                return {"success": False, "error": "登录超时", "cookieExpired": True}

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        print(f"[info] 登录成功，页面 URL: {page.url}", flush=True)

        print("[info] 保存 Cookie 缓存...", flush=True)
        save_cookies_safe(context, account_id)
        try:
            cookies_backup = context.cookies()
            _save_cookies_to_admin(account_id, cookies_backup)
        except Exception:
            pass
        return _execute_publish_actions(page, draft)
    finally:
        context.close()




def publish(draft: dict, account_id: str, headless: bool = True, extra_headers: dict | None = None, test_mode: bool = False) -> dict:
    if test_mode:
        # 测试模式：只试 cookie 不执行发布
        cookies = None
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_toutiao_cookie
            data = get_enterprise_data()
            cookie_str = get_toutiao_cookie(data) if data else None
            if cookie_str:
                cookies = parse_cookie_string(cookie_str)
        except ImportError:
            pass
        if not cookies:
            cookies = load_cookies_safe(account_id)
        if cookies:
            with sync_playwright() as p:
                return test_login(cookies, extra_headers=extra_headers, headless=headless)
        return {"success": False, "error": "无可用 cookie", "cookieExpired": True}

    # ── 生产流程 ─────────────────────────────────────────────────────
    # 1. 尝试 SQLite cookie
    sqlite_cookies = None
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
        from enterprise_db import get_enterprise_data, get_toutiao_cookie
        data = get_enterprise_data()
        cookie_str = get_toutiao_cookie(data) if data else None
        if cookie_str:
            sqlite_cookies = parse_cookie_string(cookie_str)
            print(f"[info] 使用 SQLite cookie（{len(sqlite_cookies)} 条）", flush=True)
    except ImportError:
        pass

    if sqlite_cookies:
        with sync_playwright() as p:
            result = _publish_with_context(p, sqlite_cookies, draft, headless=headless, extra_headers=extra_headers)
        if not result.get("cookieExpired"):
            return result
        print("[warn] SQLite cookie 已失效", file=sys.stderr)

    # 2. 尝试本地缓存 cookie（有上次手动登录的最新 cookie）
    cached = load_cookies_safe(account_id)
    if cached:
        print(f"[info] 使用本地缓存 cookie（{len(cached)} 条）", flush=True)
        with sync_playwright() as p:
            result = _publish_with_context(p, cached, draft, headless=headless, extra_headers=extra_headers)
        if not result.get("cookieExpired"):
            print("[info] 本地缓存 cookie 有效，同步到后台...", flush=True)
            # 同步到管理后台（失败忽略）
            try:
                _save_cookies_to_admin(account_id, cached)
            except Exception:
                pass
            return result
        print("[warn] 本地缓存 cookie 也已失效", file=sys.stderr)

    # 3. 兜底：手动登录
    print("[warn] 所有 cookie 失效，回退到手动登录", file=sys.stderr)
    with sync_playwright() as p:
        result = _login_then_publish(p, account_id, draft)
    # _login_then_publish 内部已调用 save_cookies_safe + _save_cookies_to_admin
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json",  help="draft JSON 文件路径（--test-login 时可不传）")
    parser.add_argument("--account-id",  default="",    help="头条账号 ID（可选，自动从企业信息获取）")
    parser.add_argument("--headless",    default="true", choices=["true", "false"], help="是否无头浏览器")
    parser.add_argument("--headers-json", default="",   help="额外 HTTP 请求头 JSON 文件路径（用于测试或增强登录）")
    parser.add_argument("--test-login",  action="store_true", help="仅测试登录/页面访问，不执行发布")
    args = parser.parse_args()
    headless = args.headless.lower() == "true"

    extra_headers = load_headers_from_file(args.headers_json) if args.headers_json else None

    if not args.account_id:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
            from enterprise_db import get_enterprise_data, get_toutiao_account
            data = get_enterprise_data()
            acc  = get_toutiao_account(data) if data else None
            args.account_id = acc.get("id", "") if acc else ""
            if args.account_id:
                print(f"[info] 自动获取头条账号: {args.account_id}", flush=True)
        except ImportError:
            pass

    if not args.account_id:
        print("[error] 未指定 --account-id 且未找到企业头条账号", file=sys.stderr)
        sys.exit(1)

    if args.test_login:
        result = publish({}, args.account_id, headless=headless, extra_headers=extra_headers, test_mode=True)
    else:
        raw = open(args.draft_json, "rb").read()
        for enc in ("utf-8-sig", "utf-16", "utf-8"):
            try:
                draft = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError(f"无法解析 draft JSON 文件: {args.draft_json}")
        result = publish(draft, args.account_id, headless=headless, extra_headers=extra_headers)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
