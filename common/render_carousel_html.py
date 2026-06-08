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

from enterprise_db import get_enterprise_data, get_first_brand

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


FONT_FAMILY = "'PingFang SC','Source Han Sans SC','Noto Sans SC',sans-serif"

# ── 品牌色 ──
DARK   = "#0A0A14"
LIGHT  = "#F8F8F5"
CYAN   = "#16C0FE"
BLUE   = "#0437B2"
BLUE2  = "#0078E9"
WHITE  = "#FFFFFF"

# ── 品牌 SVG Logo（BAI Z）──
LOGO_SVG = """<svg viewBox="58 25 189 189" width="16" height="16" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M58 29.9808C58 27.2273 60.1691 25 62.8527 25H179.12C181.798 25 183.973 27.2273 183.973 29.9808V83.0212C183.973 85.7688 181.804 88.002 179.12 88.002H62.8527C60.175 88.002 58 85.7746 58 83.0212V29.9808Z" fill="#16C0FE"/>
  <path d="M188.311 214H125.172C117.671 214.111 114.151 204.833 119.554 199.432L199.875 119.12C203.226 115.77 208.569 115.671 211.809 118.91L244.644 151.74C247.877 154.973 247.783 160.316 244.433 163.672L200.448 207.651C197.209 210.878 192.801 213.93 188.311 214Z" fill="#0078E9"/>
  <path d="M224.25 63.1039L192.146 31.0037C191.848 30.7056 191.532 30.4484 191.205 30.2087L191.217 30.2028C188.013 26.9642 183.651 25.1402 179.091 25.1285L160.533 25.0759L163.492 25.5378C171.449 26.7829 174.723 36.4522 169.163 42.2748L62.5953 148.817C59.3562 152.05 59.3562 157.3 62.5953 160.533L94.6995 192.633C97.9385 195.866 103.183 195.866 106.416 192.633L224.25 74.8192C227.484 71.5864 227.484 66.3426 224.25 63.1039Z" fill="currentColor"/>
</svg>"""


def _extract_content(article: str, title: str, topics: list) -> dict:
    """从文章正文动态提取各卡片所需内容"""
    sentences = [s.strip() for s in re.split(r'[。！？\n]+', article) if len(s.strip()) > 5]

    # 卡片2：前两句作为 hook
    hook = "。".join(sentences[:2]) + "。" if len(sentences) >= 2 else (sentences[0] if sentences else title)

    # 卡片3：含痛点词的句子，最多3条
    pain_kws = ["焦虑", "加班", "浪费", "憋", "发呆", "手动", "不会", "难", "愁", "慢", "费力", "半天", "小时"]
    pain_list = [s[:32] for s in sentences if any(k in s for k in pain_kws)][:3]
    if not pain_list:
        pain_list = [s[:32] for s in sentences[1:4]]

    # 卡片4：含转折/收益词的关键句
    insight_kws = ["但", "其实", "关键", "不再", "轻松", "省", "快", "自动", "帮你", "解放"]
    insight = next((s[:55] for s in sentences if any(k in s for k in insight_kws) and len(s) > 10), "")
    if not insight:
        insight = sentences[len(sentences) // 2][:40] if sentences else title[:40]

    # 卡片5：含功能描述词的句子，最多4条
    feat_kws = ["→", "->", "秒", "自动", "一键", "帮你", "可以", "直接", "快速"]
    features = [s[:28] for s in sentences if any(k in s for k in feat_kws)][:4]
    if not features:
        mid = max(1, len(sentences) // 3)
        features = [s[:28] for s in sentences[mid:mid + 4]]

    # 卡片6：最后两句作为 CTA
    cta = "。".join(sentences[-2:]) + "。" if len(sentences) >= 2 else (sentences[-1] if sentences else "")

    return { 
        "hook": hook,
        "pain_list": pain_list,
        "insight": insight,
        "features": features[:4],
        "cta": cta,
    }


def build_html(draft: dict, brand: str = "", industry: str = "") -> str:
    title = draft.get("titles", [""])[0] or draft.get("title", "")
    article = draft.get("article", "")
    topics = draft.get("topics", [])

    c = _extract_content(article, title, topics)

    # 动态标签（行业 → 内容分享）
    _label = industry if industry else "内容分享"
    # 封面副标题：hook 第一句，不超过 50 字
    _hook_short = c["hook"].split("。")[0][:50] if c.get("hook") else title[:40]
    # Card5 底部：用 insight 替代硬编码的"prompt技巧"描述
    _bottom = c["insight"][:55] if c.get("insight") else c["cta"][:55]
    # Card6 CTA 问句：用首个话题标签，无则通用文案
    _topic_cta = (_e(topics[0].lstrip("#")) + "<br>") if topics else "喜欢这篇内容吗？<br>"

    # 预生成动态 HTML 片段
    scenes_html = "\n".join(
        f'  <div class="scene-card"><div class="scene-fail">{_e(s)}</div></div>'
        for s in c["pain_list"]
    ) or f'  <div class="scene-card"><div class="scene-fail">{_e(title)}</div></div>'

    step_labels = ["01", "02", "03", "04"]
    steps_html = "\n".join(
        f'    <div class="c5-step"><div class="c5-step-num">{step_labels[i]}</div>'
        f'<div class="c5-step-name">{_e(f[:14])}</div></div>'
        for i, f in enumerate(c["features"][:4])
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #ECEEEA; font-family: {FONT_FAMILY}; }}
.card {{ width: 540px; height: 720px; position: relative; overflow: hidden; }}
.watermark {{
  position: absolute; bottom: 24px; left: 24px; z-index: 10;
  display: flex; align-items: center; gap: 8px;
  font-size: 10px; letter-spacing: 2px; font-weight: 700;
}}

/* C1 - Cover */
.c1 {{
  background: {DARK}; padding: 56px 48px 88px; color: {WHITE};
}}
.c1::before {{
  content: ''; position: absolute;
  top: -100px; right: -100px;
  width: 320px; height: 320px; border-radius: 50%;
  background: radial-gradient(circle, rgba(22,192,254,0.22) 0%, transparent 65%);
}}
.c1::after {{
  content: ''; position: absolute;
  bottom: -80px; left: -60px;
  width: 280px; height: 280px; border-radius: 50%;
  background: radial-gradient(circle, rgba(4,55,178,0.40) 0%, transparent 65%);
}}
.c1-eyebrow {{
  display: inline-block;
  font-size: 11px; color: {CYAN}; letter-spacing: 3px; font-weight: 700; margin-bottom: 36px;
  background: rgba(22,192,254,0.12); border: 1px solid rgba(22,192,254,0.40);
  padding: 5px 14px; border-radius: 999px;
}}
.c1-title {{ font-size: 56px; font-weight: 900; line-height: 1.2; letter-spacing: -1px; text-shadow: 0 0 40px rgba(22,192,254,0.25); }}
.c1-title .accent {{ color: {CYAN}; }}
.c1-title .underline {{ border-bottom: 4px solid {CYAN}; padding-bottom: 2px; display: inline-block; }}
.c1-sub {{
  font-size: 18px; color: rgba(255,255,255,0.65); line-height: 1.7; margin-top: 32px;
  border-left: 3px solid {CYAN}; padding-left: 16px;
}}
.c1-sub strong {{ color: {WHITE}; }}
.c1-dots {{
  position: absolute; bottom: 28px; right: 48px; z-index: 10;
  display: flex; gap: 6px; align-items: center;
}}
.c1-dot {{ width: 6px; height: 6px; border-radius: 50%; background: rgba(255,255,255,0.25); }}
.c1-dot.active {{ width: 20px; border-radius: 3px; background: {CYAN}; }}
.c1 .watermark {{ color: rgba(255,255,255,0.55); }}

/* C2 - Intro */
.c2 {{ background: {LIGHT}; padding: 60px 48px; color: #0A0A0A; overflow: hidden; }}
.c2::after {{
  content: ''; position: absolute;
  top: -60px; right: -60px;
  width: 200px; height: 200px; border-radius: 50%;
  background: radial-gradient(circle, rgba(22,192,254,0.12) 0%, transparent 70%);
}}
.c2-num {{
  display: inline-block;
  font-size: 11px; font-weight: 800; color: {BLUE}; letter-spacing: 3px; margin-bottom: 24px;
  border: 1px solid rgba(4,55,178,0.30); border-radius: 999px; padding: 4px 12px;
}}
.c2-headline {{ font-size: 38px; font-weight: 800; line-height: 1.35; margin-bottom: 28px; letter-spacing: -0.3px; }}
.c2-headline .accent {{ color: {BLUE}; }}
.c2-headline .strike {{ text-decoration: line-through; text-decoration-color: rgba(4,55,178,0.55); text-decoration-thickness: 3px; color: #888; }}
.c2-divider {{ width: 48px; height: 3px; background: {CYAN}; margin: 24px 0; }}
.c2-text {{ font-size: 17px; color: #2A2A2A; line-height: 1.95; letter-spacing: 0.3px; }}
.c2-text strong {{ color: {BLUE}; }}
.c2 .watermark {{ color: rgba(0,0,0,0.45); }}

/* C3 - Pain */
.c3 {{ background: {LIGHT}; padding: 60px 48px; color: #0A0A0A; }}
.c3-num {{
  display: inline-block;
  font-size: 11px; font-weight: 800; color: {BLUE}; letter-spacing: 3px; margin-bottom: 24px;
  border: 1px solid rgba(4,55,178,0.30); border-radius: 999px; padding: 4px 12px;
}}
.c3-headline {{ font-size: 32px; font-weight: 800; line-height: 1.4; margin-bottom: 28px; }}
.c3-headline .quote {{ color: {BLUE}; }}
.scene-card {{
  background: {WHITE}; border-radius: 4px; padding: 16px 20px; margin-bottom: 12px;
  border-left: 3px solid {CYAN}; box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}}
.scene-name {{ font-size: 15px; font-weight: 700; color: #0A0A0A; margin-bottom: 2px; }}
.scene-fail {{ font-size: 14px; color: #555; }}
.c3-end {{ margin-top: 20px; font-size: 17px; font-weight: 700; line-height: 1.7; }}
.c3-end .accent {{ color: {BLUE}; }}
.c3 .watermark {{ color: rgba(0,0,0,0.45); }}

/* C4 - Quote */
.c4 {{ background: {DARK}; display: flex; align-items: center; justify-content: center; }}
.c4::before {{
  content: ''; position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  width: 460px; height: 460px; border-radius: 50%;
  background: radial-gradient(circle, rgba(22,192,254,0.2) 0%, transparent 60%);
}}
.c4-content {{ position: relative; z-index: 2; text-align: center; padding: 48px; color: {WHITE}; }}
.c4-mark {{
  font-size: 88px; color: {CYAN}; opacity: 0.55; line-height: 0.8;
  margin-bottom: 24px; font-family: Georgia, serif; font-weight: 900;
}}
.c4-text {{ font-size: 30px; font-weight: 800; line-height: 1.5; letter-spacing: 0.5px;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 5; overflow: hidden; }}
.c4-text .accent {{ color: {CYAN}; }}
.c4-divider {{ width: 32px; height: 2px; background: rgba(22,192,254,0.5); margin: 20px auto 16px; }}
.c4-aside {{ font-size: 13px; color: rgba(255,255,255,0.45); letter-spacing: 1px; }}
.c4 .watermark {{ color: rgba(255,255,255,0.55); }}

/* C5 - Solution */
.c5 {{ background: {LIGHT}; padding: 60px 48px; color: #0A0A0A; }}
.c5-num {{
  display: inline-block;
  font-size: 11px; font-weight: 800; color: {BLUE}; letter-spacing: 3px; margin-bottom: 24px;
  border: 1px solid rgba(4,55,178,0.30); border-radius: 999px; padding: 4px 12px;
}}
.c5-headline {{ font-size: 36px; font-weight: 800; line-height: 1.3; margin-bottom: 24px; }}
.c5-headline .product {{ color: {BLUE}; }}
.c5-steps {{ display: flex; gap: 8px; margin-bottom: 24px; }}
.c5-step {{
  flex: 1; padding: 16px 8px;
  background: {WHITE}; border-radius: 6px; text-align: center;
  border-top: 3px solid {CYAN}; box-shadow: 0 2px 8px rgba(4,55,178,0.08);
}}
.c5-step-num {{
  font-size: 13px; font-weight: 900; color: {BLUE}; letter-spacing: 1px;
  margin-bottom: 6px;
}}
.c5-step-name {{ font-size: 13px; font-weight: 700; color: #1A1A1A; word-break: break-all; }}
.c5-time {{ margin-bottom: 24px; }}
.c5-time-row {{ display: inline-flex; align-items: baseline; gap: 14px; padding: 12px 20px; background: rgba(4,55,178,0.06); border-radius: 999px; }}
.c5-time-num {{ font-size: 32px; font-weight: 900; color: {BLUE}; letter-spacing: -1px; }}
.c5-time-label {{ font-size: 14px; color: #2A2A2A; font-weight: 600; }}
.c5-bottom {{ font-size: 16px; color: #2A2A2A; line-height: 1.85; }}
.c5-bottom strong {{ color: {BLUE}; }}
.c5 .watermark {{ color: rgba(0,0,0,0.45); }}

/* C6 - CTA */
.c6 {{
  background: radial-gradient(circle at 30% 20%, rgba(4,55,178,0.3) 0%, transparent 55%),
              radial-gradient(circle at 80% 80%, rgba(22,192,254,0.18) 0%, transparent 55%),
              {DARK};
  padding: 56px 48px 32px; color: {WHITE}; display: flex; flex-direction: column; min-height: 720px;
}}
.c6-eyebrow {{
  display: inline-block;
  font-size: 11px; color: {CYAN}; letter-spacing: 3px; font-weight: 700; margin-bottom: 32px;
  background: rgba(22,192,254,0.10); border: 1px solid rgba(22,192,254,0.35);
  padding: 5px 14px; border-radius: 999px;
}}
.c6-text {{ font-size: 32px; font-weight: 800; line-height: 1.45; }}
.c6-text-2 {{ font-size: 32px; font-weight: 800; line-height: 1.45; color: {CYAN}; margin-bottom: 32px; }}
.c6-divider {{ width: 60px; height: 3px; background: {CYAN}; margin: 0 0 28px; }}
.c6-sign {{ font-size: 14px; color: rgba(255,255,255,0.65); line-height: 1.85; margin-bottom: auto; }}
.c6-sign strong {{ color: {WHITE}; }}
.c6-cta {{ margin-top: 40px; padding: 16px 20px; border: 1px solid rgba(22,192,254,0.4); border-radius: 4px; background: rgba(22,192,254,0.06); text-align: center; }}
.c6-cta-line {{ font-size: 13px; color: {WHITE}; line-height: 1.7; font-weight: 600; }}
.c6-cta-line .accent {{ color: {CYAN}; }}
.c6 .watermark {{ color: rgba(255,255,255,0.55); }}
</style>
</head>
<body>

<!-- CARD 1 -->
<div class="card c1">
  <div class="c1-eyebrow">{_e(brand)} · {_e(_label)}</div>
  <div class="c1-title">{_e(title, 40)}</div>
  <div class="c1-sub">{_e(_hook_short)}</div>
  <div class="c1-dots">
    <div class="c1-dot active"></div>
    <div class="c1-dot"></div>
    <div class="c1-dot"></div>
    <div class="c1-dot"></div>
    <div class="c1-dot"></div>
    <div class="c1-dot"></div>
  </div>
  <div class="watermark">{LOGO_SVG} {brand}</div>
</div>

<!-- CARD 2 -->
<div class="card c2">
  <div class="c2-num">01 / 06</div>
  <div class="c2-headline"><span class="accent">{_e(title[:18])}</span></div>
  <div class="c2-divider"></div>
  <div class="c2-text">{_e(c['hook'], 120)}</div>
  <div class="watermark" style="color:rgba(0,0,0,0.45)">{LOGO_SVG} {brand}</div>
</div>

<!-- CARD 3 -->
<div class="card c3">
  <div class="c3-num">02 / 06</div>
  <div class="c3-headline">这些情况，<br><span class="quote">你有没有遇到过？</span></div>
  {scenes_html}
  <div class="c3-end">是时候<span class="accent">换个方式了。</span></div>
  <div class="watermark" style="color:rgba(0,0,0,0.45)">{LOGO_SVG} {brand}</div>
</div>

<!-- CARD 4 -->
<div class="card c4">
  <div class="c4-content">
    <div class="c4-mark">"</div>
    <div class="c4-text">{_e(c['insight'], 45)}</div>
    <div class="c4-divider"></div>
    <div class="c4-aside">来自真实用户的感受</div>
  </div>
  <div class="watermark">{LOGO_SVG} {brand}</div>
</div>

<!-- CARD 5 -->
<div class="card c5">
  <div class="c5-num">03 / 06</div>
  <div class="c5-headline">我推荐<span class="product">{brand}。</span></div>
  <div class="c5-steps">
    {steps_html}
  </div>
  <div class="c5-bottom">{_e(_bottom)}</div>
  <div class="watermark" style="color:rgba(0,0,0,0.45)">{LOGO_SVG} {brand}</div>
</div>

<!-- CARD 6 -->
<div class="card c6">
  <div class="c6-eyebrow">END · {_e(brand)} · {_e(_label)}</div>
  <div class="c6-text-2">{_e(c['cta'][:40])}</div>
  <div class="c6-divider"></div>
  <div class="c6-sign"><strong>{_e(brand)}</strong> · {_e(_label)}</div>
  <div class="c6-cta">
    <div class="c6-cta-line">{_topic_cta}关注 <span class="accent">@{_e(brand)}</span> · 评论区聊聊👇</div>
  </div>
  <div class="watermark">{LOGO_SVG} {brand}</div>
</div>

</body></html>"""


def _e(text: str, maxlen: int = 80) -> str:
    """Escape and truncate text for HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(text) > maxlen:
        text = text[:maxlen]
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

    html = build_html(draft, brand, industry)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

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
