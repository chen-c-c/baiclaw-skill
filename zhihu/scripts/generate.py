#!/usr/bin/env python3
# @author FondaWu
"""
zhihu/generate.py — 生成知乎专栏长文内容

从 collect.py 输出的 topics JSON 中选取 TOP 话题，构造知乎风格长文 Prompt，
调用 Claude/DeepSeek API 生成标题和正文，输出 draft.json 供后续渲染和发布使用。
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_SKILLS_ROOT = Path(__file__).parent.parent.parent  # SKILLs/
sys.path.insert(0, str(_SKILLS_ROOT / "common"))

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
    tone = brand_info.get("tone", "专业深度")
    audience = brand_info.get("targetAudience", "知乎用户")
    selling_points = ",".join(brand_info.get("sellingPoints") or [])
    forbidden_words = ",".join(brand_info.get("forbiddenWords") or [])

    topics_text = ""
    for i, t in enumerate(top3, 1):
        topics_text += f"\n  [{i}] {t.get('title', '')}"
        if t.get("summary"):
            topics_text += f"\n      摘要：{t['summary'][:300]}"
        if t.get("source"):
            topics_text += f"\n      来源：{t['source']}"

    forbidden = f"\n禁止出现以下词汇：{forbidden_words}。" if forbidden_words else ""
    sp = f"\n核心卖点：{selling_points}。" if selling_points else ""

    return (
        f"你是一位资深知乎专栏作者。请为品牌【{brand_name}】（{industry}行业，目标读者：{audience}）"
        f"撰写一篇知乎专栏文章。{sp}{forbidden}\n\n"
        f"写作风格：{tone}\n\n"
        f"可供参考的今日热点话题（优先使用 TOP1）：{topics_text}\n\n"
        "请严格按照以下要求生成：\n\n"
        "1. 【标题】1个，≤100字，有思考深度，知乎风格（可以是观点型、问题型或干货型）\n"
        "2. 【正文】1000~3000字，结构：\n"
        "   - 引言：提出核心观点或问题，勾起读者好奇心（100~200字）\n"
        "   - 主体：2~3个论证段落，每段有小节标题，逻辑严密，数据/案例支撑，"
        f"自然融入品牌（{brand_name}）的价值，语气{tone}\n"
        "   - 结语：总结观点，自然植入品牌，引导读者关注或互动\n"
        "   - 段落之间使用空行分隔，小节标题用【## 标题】格式\n"
        "3. 【标签】7-10 个话题标签（#开头），采用【金字塔结构】分层，核心原则：\n"
        "   【平台热度 × 内容贴合】双权重——不堆砌热门泛标签，精准长尾优先于无关热词\n"
        f"   • 大流量泛标签 1-2个：覆盖【{industry}】赛道，承接平台推荐流量\n"
        "   • 精准垂类标签 2-3个：直接对应文章核心主题和功能关键词\n"
        f"   • 人群定位标签 1个：锁定【{audience}】，帮助系统精准分发\n"
        "   • 场景标签 1个：对应用户具体使用场景，提升点击和收藏\n"
        "   • 痛点/需求标签 1个：匹配用户主动搜索意图，承接搜索流量\n"
        f"   • 品牌沉淀标签 1个：固定使用【#{brand_name}】积累账号资产\n\n"
        "输出格式（纯 JSON，不要其他文字，不要 markdown 代码块，严禁在 JSON 中添加任何注释）：\n"
        "{\"title\": \"标题（≤100字）\", \"article\": \"正文（1000~3000字）\", \"topics\": [\"#标签1\", \"#标签2\"]}"
    )


def call_llm(instruction: str) -> dict:
    from llm import call_llm_json
    draft = call_llm_json(instruction, max_tokens=4096)
    if "description" in draft and "article" not in draft:
        draft["article"] = draft.pop("description")
    article = draft.get("article", "")
    if len(article) < 800:
        raise RuntimeError(f"LLM 生成文章过短（{len(article)}字，要求≥800字），请重试")
    draft["platform"] = "zhihu"
    draft.setdefault("images", [])
    return draft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics-json", default=None, help="collect 输出的 topics JSON（省略时自动采集）")
    args, _ = parser.parse_known_args()

    if args.topics_json:
        run_dir = Path(args.topics_json).parent
    else:
        from datetime import datetime as _dt
        run_dir = _TEMP_BASE / "runs" / _dt.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        print("[generate] 未传入 topics-json，自动采集...", flush=True)
        sys.path.insert(0, str(_SKILLS_ROOT / "common"))
        from collect import collect_from_db
        args.topics_json = collect_from_db(str(run_dir))

    output_path = str(run_dir / "draft.json")
    run_dir.mkdir(parents=True, exist_ok=True)
    brief_path = str(run_dir / "content-brief.json")

    print(f"[generate] draft output: {output_path}", flush=True)

    data = get_enterprise_data()
    brand_info = get_first_brand(data) if data else {}
    if brand_info.get("name"):
        print(f"[generate] 品牌: {brand_info['name']}", flush=True)
    else:
        print("[generate][warn] 未找到企业品牌信息，使用默认值", flush=True)

    topics = load_topics(args.topics_json)
    instruction = build_content_brief(brand_info=brand_info, topics=topics)

    with open(brief_path, "w", encoding="utf-8") as f:
        json.dump({"instruction": instruction}, f, ensure_ascii=False, indent=2)
    print(f"[generate] brief written: {brief_path}", flush=True)

    draft = call_llm(instruction)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    print(f"[generate] draft written: {output_path}", flush=True)

    # 渲染封面图
    print("[generate] 渲染封面图...", flush=True)
    sys.path.insert(0, str(Path(__file__).parent))
    from render_cover import render_draft
    render_draft(output_path)

    # 提交审核
    print("[generate] 提交审核...", flush=True)
    import submit_for_review as _sfr
    review_id = _sfr.submit_draft("zhihu", output_path)

    print(f"[generate] 完成，审核ID: {review_id}", flush=True)
    print(json.dumps({
        "draft_path": output_path,
        "brief_path": brief_path,
        "review_id":  review_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
