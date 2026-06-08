#!/usr/bin/env python3
# @author FondaWu / OpenClaw Agent
"""
xiaohongshu/generate.py — 生成小红书内容指令并直接调用 LLM 生成 draft.json

从 collect.py 输出的 topics JSON 中选取 TOP 话题，构造内容生成 Prompt，
调用 Claude API 生成标题/正文/标签，输出 draft.json 供后续 render_carousel.py 使用。
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
from enterprise_db import get_enterprise_data, get_first_brand

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)




def load_topics(topics_json_path: str) -> dict:
    with open(topics_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_content_brief(brand_info: dict, topics: dict) -> str:
    top_items = topics.get("topics", [])
    top3 = top_items[:3] if top_items else []

    brand_name = brand_info.get("name", "未知品牌")
    industry = brand_info.get("industry", "未知行业")
    tone = brand_info.get("tone", "活泼亲切")
    audience = brand_info.get("targetAudience", "年轻用户")
    selling_points = ",".join(brand_info.get("sellingPoints") or [])
    forbidden_words = ",".join(brand_info.get("forbiddenWords") or [])

    topics_text = ""
    for i, t in enumerate(top3, 1):
        topics_text += f"\n  [{i}] {t.get('title', '')}"
        if t.get('summary'):
            topics_text += f"\n      摘要：{t['summary'][:200]}"
        if t.get('source'):
            topics_text += f"\n      来源：{t['source']}"

    forbidden = f"\n禁止出现以下词汇：{forbidden_words}。" if forbidden_words else ""
    sp = f"\n核心卖点：{selling_points}。" if selling_points else ""

    return (
        f"你是一位资深小红书运营专家。请为品牌【{brand_name}】（{industry}行业，目标用户：{audience}）"
        f"生成一条小红书图文笔记内容。{sp}{forbidden}\n\n"
        f"内容风格：{tone}\n\n"
        f"可供参考的今日热点话题（优先使用 TOP1）：{topics_text}\n\n"
        "请严格按照以下要求生成：\n\n"
        "1. 【标题】3 个备选标题，每个不超过 20 字，分别用不同的情绪钩子（好奇/焦虑/共鸣/反常识）\n"
        "2. 【正文】500-800 字，结构：\n"
        "   - 开头：用热点或痛点引发共鸣（1-2句）\n"
        f"   - 中间：讲故事或列干货，自然融入品牌（{brand_name}）的功能和价值\n"
        "   - 结尾：引导互动（提问或鼓励评论）\n"
        "3. 【标签】7-10 个话题标签（#开头），采用【金字塔结构】分层，核心原则：\n”
        "   【平台热度 × 内容贴合】双权重——不堆砌热门泛标签，精准长尾优先于无关热词\n"
        f"   • 大流量泛标签 1-2个：覆盖【{industry}】赛道，承接平台推荐流量\n"
        "   • 精准垂类标签 2-3个：直接对应文章核心主题和功能关键词\n"
        f"   • 人群定位标签 1个：锁定【{audience}】，帮助系统精准分发\n"
        "   • 场景标签 1个：对应用户具体使用场景，提升点击和收藏\n"
        "   • 痛点/需求标签 1个：匹配用户主动搜索意图，承接搜索流量\n"
        f"   • 品牌沉淀标签 1个：固定使用【#{brand_name}】积累账号资产\n\n"
        "输出格式（纯 JSON，不要其他文字，不要 markdown 代码块，严禁在 JSON 中添加任何注释）：\n"
        "{\"titles\": [\"标题1（首选）\", \"标题2\", \"标题3\"], \"article\": \"正文内容...\", \"topics\": [\"#标签1\", \"#标签2\"]}"
    )


def call_llm(instruction: str) -> dict:
    from llm import call_llm_json
    draft = call_llm_json(instruction, max_tokens=2048)
    if "description" in draft and "article" not in draft:
        draft["article"] = draft.pop("description")
    draft.setdefault("images", [])
    if isinstance(draft.get("topics"), list) and len(draft["topics"]) > 10:
        draft["topics"] = draft["topics"][:10]
    return draft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics-json", default=None, help="collect 输出的 topics JSON（省略时自动采集）")
    # parse_known_args 静默忽略 Agent 可能传入的多余参数（如 --output）
    args, _ = parser.parse_known_args()

    if args.topics_json:
        run_dir = Path(args.topics_json).parent
    else:
        from datetime import datetime as _dt
        run_dir = _TEMP_BASE / "runs" / _dt.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        print("[generate] 未传入 topics-json，自动采集...", flush=True)
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
        from collect import collect_from_db
        args.topics_json = collect_from_db(str(run_dir))

    args.output = str(run_dir / "draft.json")
    run_dir = Path(args.output).parent
    run_dir.mkdir(parents=True, exist_ok=True)
    default_brief = str(run_dir / "content-brief.json")

    print(f"[generate] draft output: {args.output}", flush=True)

    # 从 SQLite 读取企业品牌信息
    data = get_enterprise_data()
    brand_info = get_first_brand(data) if data else {}
    if brand_info.get("name"):
        print(f"[generate] 品牌: {brand_info['name']}", flush=True)
    else:
        print("[generate][warn] 未找到企业品牌信息，使用默认值", flush=True)

    topics = load_topics(args.topics_json)
    instruction = build_content_brief(brand_info=brand_info, topics=topics)

    # 保存 instruction 供调试
    with open(default_brief, "w", encoding="utf-8") as f:
        json.dump({"instruction": instruction}, f, ensure_ascii=False, indent=2)
    print(f"[generate] brief written: {default_brief}", flush=True)

    # 调用 LLM 生成草稿
    draft = call_llm(instruction)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    print(f"[generate] draft written: {args.output}", flush=True)

    # 渲染轮播图
    print("[generate] 渲染图片...", flush=True)
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
    from render_carousel import render_draft
    render_draft(args.output)

    # 提交审核
    print("[generate] 提交审核...", flush=True)
    import submit_for_review as _sfr
    review_id = _sfr.submit_draft("xiaohongshu", args.output)

    print(json.dumps({
        "draft_path": args.output,
        "brief_path": default_brief,
        "review_id":  review_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
