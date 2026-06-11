#!/usr/bin/env python3
# @author FondaWu
"""
render_carousel.py — 渲染 HTML 样式的小红书轮播图为 PNG

用 Playwright 截图每张卡片为 1080×1440 PNG。
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from enterprise_db import get_enterprise_data, get_first_brand, get_admin_api_url, get_admin_token


def _load_dotenv() -> None:
    """加载项目根目录 .env 文件（不覆盖已有环境变量）。
    dev 运行时：SKILLs/common/../../.. = baiclaw/
    AppData 运行时：SKILLs/common/../../.. = BaiClaw/
    两种情况 parent.parent.parent 都指向正确的根目录。
    """
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def _fetch_carousel_from_api(platform: str) -> tuple:
    """从后端 API 获取当前平台激活的轮播模板 (html, schema_dict)。
    BAICLAW_ADMIN_API_URL 未设置或请求失败时返回 (None, None)。
    """
    admin_url = (os.environ.get('BAICLAW_ADMIN_API_URL', '').rstrip('/') or get_admin_api_url()).rstrip('/')
    token = os.environ.get('BAICLAW_ADMIN_TOKEN', '').strip('"') or get_admin_token() or ''
    print(f"[render][api] BAICLAW_ADMIN_API_URL={'已设置: '+admin_url if admin_url else '未设置'}", flush=True)
    print(f"[render][api] BAICLAW_ADMIN_TOKEN={'已设置('+token[:12]+'...)' if token else '未设置'}", flush=True)
    if not admin_url:
        print("[render][api] admin_url 为空，跳过 API，直接用本地文件", flush=True)
        return None, None
    try:
        import urllib.request as _ur
        url = f"{admin_url}/api/device/slide-templates/active-html?platform={platform}"
        print(f"[render][api] 请求: {url}", flush=True)
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        req = _ur.Request(url, headers=headers)
        with _ur.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        code = body.get('code')
        data = body.get('data')
        print(f"[render][api] 响应 code={code}, data={'有数据' if data else 'null'}", flush=True)
        if code == 200 and data:
            html = data.get('html') or ''
            schema_raw = data.get('schema') or ''
            try:
                schema = json.loads(schema_raw) if isinstance(schema_raw, str) and schema_raw else schema_raw or {}
            except Exception:
                schema = {}
            if html:
                print(f"[render][api] 成功: platform={platform}, slug={data.get('slug')}, html={len(html)}字符", flush=True)
                return html, schema
            print("[render][api] data 有值但 html 为空，回退本地", flush=True)
        else:
            print(f"[render][api] code={code} 或 data=null，回退本地", flush=True)
    except Exception as e:
        print(f"[render][warn] API 获取模板失败，回退本地: {e}", flush=True)
    return None, None



def _extract_content(article: str, title: str, topics: list) -> dict:
    """从文章正文动态提取各卡片所需内容"""
    # 去掉句首 emoji / 符号，保留中文核心内容
    raw = [s.strip() for s in re.split(r'[。！？\n]+', article) if len(s.strip()) > 5]
    sentences = [re.sub(r'^[^一-鿿\w]+', '', s).strip() for s in raw]
    sentences = [s for s in sentences if len(s) > 5]

    # 卡片2：前两句作为 hook
    hook = "。".join(sentences[:2]) + "。" if len(sentences) >= 2 else (sentences[0] if sentences else title)

    # 卡片3：含痛点词的句子，最多3条（扩展关键词覆盖更多行业场景）
    pain_kws = ["焦虑", "加班", "浪费", "憋", "发呆", "手动", "不会", "难", "愁", "慢", "费力",
                "半天", "小时", "大海捞针", "被坑", "踩坑", "亏", "差价", "中间商", "老办法",
                "还在用", "新手", "不知道", "麻烦", "复杂", "烦", "没有", "缺少", "问题"]
    pain_list = [s[:32] for s in sentences if any(k in s for k in pain_kws)][:3]
    if len(pain_list) < 3:
        seen_p = set(pain_list)
        for s in sentences[1:]:
            if len(pain_list) >= 3:
                break
            t = s[:32]
            if t not in seen_p:
                pain_list.append(t)
                seen_p.add(t)

    # 卡片4：优先取方法/核心论点句，避免用含"但"的复合背景句
    insight_priority = ["其实", "关键是", "关键", "正确", "方法是", "方式是", "解决", "推荐"]
    insight = next((s[:55] for s in sentences if any(k in s for k in insight_priority) and len(s) > 10), "")
    if not insight:
        insight_secondary = ["不再", "轻松", "省", "快", "自动", "帮你", "解放", "但"]
        insight = next((s[:55] for s in sentences if any(k in s for k in insight_secondary) and len(s) > 10), "")
    if not insight:
        insight = sentences[len(sentences) // 2][:40] if sentences else title[:40]

    # 卡片5：扩展功能关键词；不足4条时从全文补充，确保4条都有内容
    feat_kws = ["→", "->", "秒", "自动", "一键", "帮你", "可以", "直接", "快速",
                "工具", "通过", "使用", "利用", "点击", "输入", "选择", "匹配", "筛选", "分析"]
    features = [s[:28] for s in sentences if any(k in s for k in feat_kws)][:4]
    if len(features) < 4:
        seen = set(features)
        for s in sentences:
            if len(features) >= 4:
                break
            t = s[:28]
            if t not in seen:
                features.append(t)
                seen.add(t)

    # 卡片6：最后两句作为 CTA
    cta = "。".join(sentences[-2:]) + "。" if len(sentences) >= 2 else (sentences[-1] if sentences else "")

    return { 
        "hook": hook,
        "pain_list": pain_list,
        "insight": insight,
        "features": features[:4],
        "cta": cta,
    }


def _build_cover_prompt(brand: str, industry: str, title: str = "") -> str:
    ctx = f"{industry}行业" if industry else "商业"
    theme = f"，主题关键词：{title[:20]}" if title else ""
    return (
        f"{ctx}高端品牌封面背景{theme}，电影级质感，深邃暗色调，"
        "青色科技光线从右上角斜射入画面，细腻数字粒子漫散，"
        "极简高端风格，无文字，无logo，无人物，"
        "竖版构图，背景留白充足便于叠加文字"
    )


def _generate_cover_bg(run_dir: Path, brand: str, industry: str, title: str = "") -> str | None:
    key = os.environ.get("QWEN_IMAGE_API_KEY", "")
    log_path = run_dir / "_cover_debug.log"

    def _log(msg: str):
        print(msg, flush=True)
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(msg + "\n")

    _log(f"[cover_debug] QWEN_IMAGE_API_KEY={'SET(' + key[:8] + '...)' if key else 'NOT SET'}")
    if not key:
        _log("[cover_debug] 跳过：key 未设置")
        return None

    sys.path.insert(0, str(Path(__file__).parent))
    from cover_generator import generate_cover
    try:
        _log(f"[cover_debug] 开始调用 API, title={title[:20]}, brand={brand}")
        result = generate_cover(
            prompt=_build_cover_prompt(brand, industry, title),
            output_path=str(run_dir / "_bg_cover.png"),
            title=title[:30],
            sub_title=brand,
        )
        _log(f"[cover_debug] 生成结果: {result}")
        return result
    except Exception as e:
        _log(f"[cover_debug] 异常: {e}")
        print(f"[render][warn] AI 封面生成失败，使用纯色背景: {e}", flush=True)
        return None


def build_html(draft: dict, brand: str = "", industry: str = "", cover_bg_path: str | None = None) -> str:
    title = draft.get("titles", [""])[0] or draft.get("title", "")
    article = draft.get("article", "")
    topics = draft.get("topics", [])

    c = _extract_content(article, title, topics)
    platform = draft.get('platform', 'douyin') if isinstance(draft, dict) else 'douyin'

    api_html, api_schema = _fetch_carousel_from_api(platform)
    if not api_html:
        raise RuntimeError(f"后端 API 未返回模板 HTML (platform={platform})，请确认 BAICLAW_ADMIN_API_URL 配置正确且后端已启动")
    html = api_html
    schema = api_schema if isinstance(api_schema, dict) and api_schema else {'pain_count': 3, 'feat_count': 4}

    _label = industry if industry else "内容分享"
    _first = c["hook"].split("。")[0] if c.get("hook") else title
    _hook_short = _first if len(_first) <= 50 else _first[:48] + "…"
    _topic_cta = (_e(topics[0].lstrip("#")) + "<br>") if topics else "喜欢这篇内容吗？<br>"

    cover_css = ""
    if cover_bg_path:
        _uri = Path(cover_bg_path).resolve().as_posix()
        cover_css = (
            f".c1{{background:linear-gradient(rgba(10,10,20,.62),rgba(10,10,20,.62)),"
            f"url('file:///{_uri}') center/cover no-repeat !important;}}"
        )

    pain_count = int(schema.get('pain_count', 3))
    feat_count = int(schema.get('feat_count', 4))
    pain = c["pain_list"][:pain_count]
    feats = c["features"][:feat_count]
    replacements = {
        "{{BRAND}}":       _e(brand, 20),
        "{{INDUSTRY}}":    _e(_label, 12),
        "{{TITLE}}":       _e(title, 40),
        "{{TITLE_SHORT}}": _e(title[:18]),
        "{{HOOK_SHORT}}":  _e(_hook_short),
        "{{HOOK}}":        _e(c["hook"], 120),
        "{{PAIN_1}}":      _e(pain[0] if len(pain) > 0 else title, 32),
        "{{PAIN_2}}":      _e(pain[1] if len(pain) > 1 else "", 32),
        "{{PAIN_3}}":      _e(pain[2] if len(pain) > 2 else "", 32),
        "{{INSIGHT}}":     _e(c["insight"], 55),
        "{{FEAT_1}}":      _e(feats[0] if len(feats) > 0 else "", 28),
        "{{FEAT_2}}":      _e(feats[1] if len(feats) > 1 else "", 28),
        "{{FEAT_3}}":      _e(feats[2] if len(feats) > 2 else "", 28),
        "{{FEAT_4}}":      _e(feats[3] if len(feats) > 3 else "", 28),
        "{{CTA}}":         _e(c["cta"], 40),
        "{{TOPIC_CTA}}":   _topic_cta,
        "{{COVER_BG_CSS}}": cover_css,
    }
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


def _e(text: str, maxlen: int = 80) -> str:
    """Escape and truncate text for HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(text) > maxlen:
        text = text[:maxlen] + "…"
    return text


def _run(args) -> list[str]:
    # handle output_dir default
    if args.output_dir is None:
        args.output_dir = str(Path(args.draft_json).parent)

    print(f"[render] output: {args.output_dir}", flush=True)

    data = get_enterprise_data()
    brand_info = get_first_brand(data) if data else {}
    brand = brand_info.get("name", "")
    industry = brand_info.get("industry", "")
    if brand:
        print(f"[render] 品牌: {brand} / 行业: {industry}", flush=True)

    with open(args.draft_json, "r", encoding="utf-8") as f:
        draft = json.load(f)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    html = build_html(draft, brand, industry)

    html_path = out / "_deck.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[render] HTML saved: {html_path}")

    headless = args.headless.lower() == "true"
    from playwright.sync_api import sync_playwright

    images = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1080, "height": 1440}, device_scale_factor=2)
        page.goto(f"file:///{html_path.resolve().as_posix()}", wait_until="load", timeout=30_000)
        page.wait_for_timeout(1000)

        for i in range(1, 7):
            card = page.locator(f".card:nth-child({i})")
            card.wait_for(timeout=10_000)
            png_path = str(out / f"0{i}_{['cover','hook','pain','quote','solution','cta'][i-1]}.png")
            card.screenshot(path=png_path)
            images.append(png_path)
            print(f"[render] {i}/6 {png_path}", flush=True)

        browser.close()

    result = {"images": images, "count": len(images)}
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 写回 draft.json 的 images 字段
    draft["images"] = images
    with open(args.draft_json, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    print(f"[render] draft.json updated: {args.draft_json}", flush=True)

    try:
        os.startfile(str(out))
    except Exception:
        pass

    return images


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json", required=True)
    parser.add_argument("--output-dir", default=None, help="图片输出目录（默认与 draft.json 同级目录）")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    args = parser.parse_args()
    _run(args)


def render_draft(draft_path: str, headless: bool = True) -> list[str]:
    """渲染 draft.json 对应的轮播图，返回图片路径列表，并更新 draft.json 的 images 字段。"""
    import argparse as _ap
    _args = _ap.Namespace(draft_json=draft_path, output_dir=None, headless=str(headless).lower())
    return _run(_args)


if __name__ == "__main__":
    main()
