#!/usr/bin/env python3
# @author FondaWu
"""
toutiao/render_cover.py — 渲染今日头条图文封面图（1920×1080 × 3 张）

仿照 zhihu/render_cover.py 的 HTML+Playwright 截图方式：
  01_cover.png       — 标题 + 品牌/行业标签
  02_content_1.png   — 文章开头节选
  03_content_2.png   — 文章后段/观点

调用方式：
  python render_cover.py --draft-json <draft.json 路径>

成功时最后一行输出：
  {"success": true, "images": ["...01_cover.png", "...02_content_1.png", "...03_content_2.png"]}
"""
import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
from enterprise_db import get_enterprise_data, get_first_brand

# 1920×1080 CSS 像素，1x DPR → 输出 1920×1080 实际像素，JPEG quality 控制体积
W = 1920
H = 1080
FONT = "'PingFang SC','Source Han Sans SC','Noto Sans SC',sans-serif"
CYAN = "#16C0FE"
BLUE = "#0078E9"


def _e(text: str, max_len: int = 0) -> str:
    s = html_lib.escape(str(text or ""))
    if max_len and len(str(text)) > max_len:
        s = html_lib.escape(str(text)[:max_len]) + "…"
    return s


def _build_cover_html(title: str, brand: str, industry: str) -> str:
    """封面：深色渐变背景 + 标题 + 品牌标签"""
    title_size = "36px" if len(title) > 24 else "44px"
    label = f"{brand} · {industry}" if industry else brand
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; font-family:{FONT}; }}
.wrap {{
  width:{W}px; height:{H}px;
  background:linear-gradient(135deg,#0A0F1E 0%,#0D1B4B 45%,#0A1628 100%);
  position:relative; overflow:hidden; padding:60px 75px; color:white;
  display:flex; flex-direction:column; justify-content:center;
}}
.wrap::before {{
  content:''; position:absolute; top:-75px; right:-75px;
  width:315px; height:315px; border-radius:50%;
  background:radial-gradient(circle,rgba(0,132,255,0.30) 0%,transparent 70%);
}}
.wrap::after {{
  content:''; position:absolute; bottom:-90px; left:75px;
  width:225px; height:225px; border-radius:50%;
  background:radial-gradient(circle,rgba(0,132,255,0.15) 0%,transparent 70%);
}}
.eyebrow {{ font-size:18px; color:rgba(255,255,255,0.60); letter-spacing:1px; margin-bottom:24px; position:relative; z-index:1; }}
.title {{ font-size:{title_size}; font-weight:900; color:#FFFFFF; line-height:1.35; letter-spacing:-0.5px; max-width:700px; position:relative; z-index:1; }}
.deco {{ position:absolute; top:60px; right:60px; width:3px; height:90px; background:rgba(0,132,255,0.60); border-radius:2px; z-index:1; }}
.wm {{ position:absolute; bottom:24px; right:45px; font-size:16px; color:rgba(255,255,255,0.35); letter-spacing:1px; z-index:1; }}
</style></head><body>
<div class="wrap">
  <div class="deco"></div>
  <div class="eyebrow">{_e(label)}</div>
  <div class="title">{_e(title, 60)}</div>
  <div class="wm">{_e(brand)}</div>
</div></body></html>"""


def _build_content_html(text: str, index: int, brand: str, industry: str) -> str:
    """内容配图：不同背景色，展示正文节选"""
    colors = [
        ("#0F1A2E", "#1A2D4A", "#0F1A2E"),  # 深蓝
        ("#1A120A", "#2D1A0A", "#1A120A"),  # 暖棕
    ]
    c1, c2, c3 = colors[index % 2]
    label = f"{brand} · {industry}" if industry else brand
    subtitle = "正文配图 · ①" if index == 0 else "正文配图 · ②"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; font-family:{FONT}; }}
.wrap {{
  width:{W}px; height:{H}px;
  background:linear-gradient(135deg,{c1} 0%,{c2} 45%,{c3} 100%);
  position:relative; overflow:hidden; padding:45px 60px; color:white;
  display:flex; flex-direction:column; justify-content:center;
}}
.wrap::before {{
  content:''; position:absolute; top:-60px; left:-45px;
  width:210px; height:210px; border-radius:50%;
  background:radial-gradient(circle,rgba(22,192,254,0.18) 0%,transparent 65%);
}}
.eyebrow {{ font-size:16px; color:{CYAN}; letter-spacing:1px; margin-bottom:16px; position:relative; z-index:1; }}
.text {{ font-size:24px; font-weight:700; line-height:1.6; color:rgba(255,255,255,0.92);
         max-width:750px; position:relative; z-index:1; }}
.dot {{ color:{CYAN}; }}
.wm {{ position:absolute; bottom:24px; right:45px; font-size:14px; color:rgba(255,255,255,0.30); z-index:1; }}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">{_e(label)} — {subtitle}</div>
  <div class="text">{_e(text, 180)}<span class="dot">。</span></div>
  <div class="wm">{_e(brand)}</div>
</div></body></html>"""


def render_draft(draft_path: str, headless: bool = True) -> list[str]:
    """渲染头条 3 张配图，返回图片路径列表，并更新 draft.json 的 images 字段。"""
    _path = Path(draft_path)
    with open(_path, "r", encoding="utf-8") as f:
        draft = json.load(f)

    title = draft.get("title", "")
    article = draft.get("article", "")
    if not title:
        raise ValueError("draft.json 缺少 title 字段")

    ent = get_enterprise_data() or {}
    brand_obj = get_first_brand(ent)
    brand = brand_obj.get("name", "") if brand_obj else ""
    industry = brand_obj.get("industry", "") if brand_obj else ""

    out_dir = _path.parent

    # 从正文切分两段内容
    sentences = [s.strip() for s in re.split(r'[。！？\n]+', article) if len(s.strip()) > 10]
    mid = len(sentences) // 3
    if mid < 1:
        mid = 1
    part1 = "。".join(sentences[:mid])[:200]
    part2 = "。".join(sentences[-mid:])[:200]

    # 生成 3 个 HTML
    specs = [
        ("01_cover", _build_cover_html(title, brand, industry)),
        ("02_content_1", _build_content_html(part1, 0, brand, industry)),
        ("03_content_2", _build_content_html(part2, 1, brand, industry)),
    ]

    from playwright.sync_api import sync_playwright

    images = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": W, "height": H})

        for name, html_str in specs:
            html_path = out_dir / f"_{name}.html"
            html_path.write_text(html_str, encoding="utf-8")
            page.goto(f"file:///{html_path.resolve().as_posix()}", wait_until="load", timeout=20_000)
            page.wait_for_timeout(500)
            jpg_path = str(out_dir / f"{name}.jpg")
            page.screenshot(path=jpg_path, clip={"x": 0, "y": 0, "width": W, "height": H}, type="jpeg", quality=80)
            images.append(jpg_path)
            print(f"[render_cover] 图片已生成: {jpg_path}", flush=True)

        browser.close()

    draft["images"] = images
    with open(_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)

    print(f"[render_cover] 共生成 {len(images)} 张图片", flush=True)
    return images


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-json", required=True)
    parser.add_argument("--headless", default="true")
    args = parser.parse_args()

    draft_path = Path(args.draft_json)
    if not draft_path.exists():
        print(json.dumps({"success": False, "error": f"draft.json 不存在: {args.draft_json}"}), flush=True)
        sys.exit(1)

    headless = args.headless.lower() == "true"
    try:
        images = render_draft(str(draft_path), headless=headless)
        print(json.dumps({"success": True, "images": images}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, flush=True))
        sys.exit(1)


if __name__ == "__main__":
    main()
