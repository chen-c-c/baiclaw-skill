#!/usr/bin/env python3
# @author FondaWu / OpenClaw Agent
"""
xiaohongshu/generate.py — 生成小红书内容指令

从 collect.py 输出的 topics JSON 中选取 TOP 话题，
输出内容生成指令，Agent 按指令生成 draft JSON。
图片由后续 render_carousel.py 生成，此处不涉及。
"""
import argparse
import datetime
import json
import os
import tempfile
from pathlib import Path

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def _make_run_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def load_topics(topics_json_path: str) -> dict:
    with open(topics_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_content_brief(
    brand_name: str,
    industry: str,
    tone: str,
    selling_points: str,
    forbidden_words: str,
    topics: dict,
) -> str:
    top_items = topics.get("topics", [])
    brand_info = topics.get("brand", {})

    top3 = top_items[:3] if top_items else []
    brand_name_final = brand_name or brand_info.get("name", "未知品牌")
    industry_final = industry or brand_info.get("industry", "未知行业")

    topics_text = ""
    for i, t in enumerate(top3, 1):
        topics_text += f"\n  [{i}] {t.get('title', '')}"
        if t.get('summary'):
            topics_text += f"\n      摘要：{t['summary'][:200]}"
        if t.get('source'):
            topics_text += f"\n      来源：{t['source']}"

    forbidden = f"\n禁止出现以下词汇：{forbidden_words}。" if forbidden_words else ""
    sp = f"\n核心卖点：{selling_points}。" if selling_points else ""
    audience = brand_info.get("target_audience", "年轻用户")

    return (
        f"你是一位资深小红书运营专家。请为品牌「{brand_name_final}」（{industry_final}行业，目标用户：{audience}）"
        f"生成一条小红书图文笔记内容。{sp}{forbidden}\n\n"
        f"内容风格：{tone or '活泼亲切'}\n\n"
        f"可供参考的今日热点话题（优先使用 TOP1）：{topics_text}\n\n"
        "请严格按照以下要求生成：\n\n"
        "1. 【标题】3 个备选标题，每个不超过 20 字，分别用不同的情绪钩子（好奇/焦虑/共鸣/反常识）\n"
        "2. 【正文】500-800 字，结构：\n"
        "   - 开头：用热点或痛点引发共鸣（1-2句）\n"
        f"   - 中间：讲故事或列干货，自然融入品牌（{brand_name_final}）的功能和价值\n"
        "   - 结尾：引导互动（提问或鼓励评论）\n"
        "3. 【标签】5-8 个话题标签（#开头）\n\n"
        "输出格式（纯 JSON，不要其他文字）：\n"
        '{"titles": ["标题1（首选）", "标题2", "标题3"], "article": "正文内容...", "topics": ["#标签1", "#标签2"]}'
    )


def main():
    run_id = _make_run_id()
    run_dir = _TEMP_BASE / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    default_output = str(run_dir / "content-brief.json")

    parser = argparse.ArgumentParser()
    parser.add_argument("--topics-json", required=True, help="collect.py 输出的 topics JSON 路径")
    parser.add_argument("--brand-name", default="", help="品牌名称（覆盖 topics JSON 中的值）")
    parser.add_argument("--industry", default="", help="行业（覆盖）")
    parser.add_argument("--tone", default="活泼亲切", help="内容风格")
    parser.add_argument("--selling-points", default="", help="核心卖点（逗号分隔）")
    parser.add_argument("--forbidden-words", default="", help="违禁词列表")
    parser.add_argument("--output", "-o", default=default_output, help="输出路径")
    args = parser.parse_args()

    print(f"[generate] run_id: {run_id}", flush=True)
    print(f"[generate] output: {args.output}", flush=True)

    topics = load_topics(args.topics_json)
    instruction = build_content_brief(
        brand_name=args.brand_name,
        industry=args.industry,
        tone=args.tone,
        selling_points=args.selling_points,
        forbidden_words=args.forbidden_words,
        topics=topics,
    )

    output = {"instruction": instruction}

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[info] output written: {args.output}", flush=True)


if __name__ == "__main__":
    main()
