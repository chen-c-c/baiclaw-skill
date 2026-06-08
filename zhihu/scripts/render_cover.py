#!/usr/bin/env python3
# @author FondaWu
"""
zhihu/render_cover.py — 渲染知乎文章横版封面图（1200×675）

调用方式：
  python render_cover.py --draft-json <draft.json 路径>

成功时最后一行输出：
  {"success": true, "image_path": "...cover.png"}
失败时：
  {"success": false, "error": "..."}
"""
import argparse
import html as html_lib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
from enterprise_db import get_enterprise_data, get_first_brand


def _e(text: str, max_len: int = 0) -> str:
    s = html_lib.escape(str(text or ""))
    if max_len and len(str(text)) > max_len:
        s = html_lib.escape(str(text)[:max_len]) + "…"
    return s


def build_html(title: str, brand: str = "", industry: str = "") -> str:
    label = f"{brand} · {industry}" if industry else brand
    title_size = "46px" if len(title) > 24 else "58px"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{
  width: 1200px;
  height: 675px;
  overflow: hidden;
  font-family: 'PingFang SC','Source Han Sans SC','Noto Sans SC',sans-serif;
}}
.cover {{
  width: 1200px;
  height: 675px;
  background: linear-gradient(135deg, #0A0F1E 0%, #0D1B4B 45%, #0A1628 100%);
  position: relative;
  overflow: hidden;
  padding: 60px 80px;
  color: white;
  display: flex;
  flex-direction: column;
  justify-content: center;
}}
.cover::before {{
  content: '';
  position: absolute;
  top: -100px;
  right: -100px;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(0,132,255,0.30) 0%, transparent 70%);
  pointer-events: none;
}}
.cover::after {{
  content: '';
  position: absolute;
  bottom: -120px;
  left: 100px;
  width: 300px;
  height: 300px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(0,132,255,0.15) 0%, transparent 70%);
  pointer-events: none;
}}
.eyebrow {{
  font-size: 20px;
  color: rgba(255,255,255,0.60);
  letter-spacing: 1px;
  margin-bottom: 36px;
  position: relative;
  z-index: 1;
}}
.title {{
  font-size: {title_size};
  font-weight: 900;
  color: #FFFFFF;
  line-height: 1.35;
  letter-spacing: -0.5px;
  max-width: 950px;
  position: relative;
  z-index: 1;
}}
.deco-line {{
  position: absolute;
  top: 80px;
  right: 80px;
  width: 3px;
  height: 120px;
  background: rgba(0,132,255,0.60);
  border-radius: 2px;
  z-index: 1;
}}
.watermark {{
  position: absolute;
  bottom: 36px;
  right: 60px;
  font-size: 18px;
  color: rgba(255,255,255,0.35);
  letter-spacing: 1px;
  z-index: 1;
}}
</style>
</head>
<body>
<div class="cover">
  <div class="deco-line"></div>
  <div class="eyebrow">{_e(label)}</div>
  <div class="title">{_e(title, 60)}</div>
  <div class="watermark">{_e(brand)}</div>
</div>
</body>
</html>"""


def render_draft(draft_path: str, headless: bool = True) -> str:
    """渲染知乎封面图，返回 cover.png 路径，并更新 draft.json 的 images 字段。"""
    _path = Path(draft_path)
    with open(_path, "r", encoding="utf-8") as f:
        draft = json.load(f)

    title = draft.get("title", "")
    if not title:
        raise ValueError("draft.json 缺少 title 字段")

    ent = get_enterprise_data() or {}
    brand_obj = get_first_brand(ent)
    brand = brand_obj.get("name", "") if brand_obj else ""
    industry = brand_obj.get("industry", "") if brand_obj else ""

    out_dir = _path.parent
    html_path = out_dir / "_cover.html"
    cover_path = out_dir / "cover.png"

    html_path.write_text(build_html(title, brand, industry), encoding="utf-8")
    print(f"[render_cover] HTML 已生成: {html_path}", flush=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
        page.goto(f"file:///{html_path.resolve().as_posix()}", wait_until="load", timeout=20_000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(cover_path), clip={"x": 0, "y": 0, "width": 1200, "height": 675})
        browser.close()

    draft["images"] = [str(cover_path)]
    with open(_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)

    print(f"[render_cover] 封面已生成: {cover_path}", flush=True)
    return str(cover_path)


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
        image_path = render_draft(str(draft_path), headless=headless)
        print(json.dumps({"success": True, "image_path": image_path}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
