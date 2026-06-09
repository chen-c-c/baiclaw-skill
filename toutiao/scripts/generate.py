#!/usr/bin/env python3
# @author FondaWu
"""
toutiao/generate.py — 今日头条图文端到端生成（AGENT 唯一入口）

!!! AGENT 警告 — 不要查看或调用内部函数 !!!
本脚本是 toutiao SKILL 的 唯 一 入口。
不要：
  - import 或调用本文件或 render_carousel.py 的任何内部函数
  - 查看、分析或 grep 本文件及其导入的模块代码
  - 直接调用 render_carousel.py 或 submit_for_review.py
只需运行：python generate.py（无需传参）

=== 内部流程（Agent 无需关心） ===
1. 自动采集今日热点
2. 从 SQLite 读取品牌信息，构造 Prompt
3. 调用 Claude/DeepSeek 生成标题/正文/标签
4. 自动 AI 生成图片（封面根据标题，其余根据正文）
5. 自动提交审核（submit_for_review）
6. 输出 {"draft_path","brief_path","review_id"} 到 stdout
"""
import argparse
import json
import os
import re
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
    tone = brand_info.get("tone", "专业客观")
    audience = brand_info.get("targetAudience", "头条用户")
    selling_points = ",".join(brand_info.get("sellingPoints") or [])
    forbidden_words = ",".join(brand_info.get("forbiddenWords") or [])

    topics_text = ""
    for i, t in enumerate(top3, 1):
        topics_text += f"\n  [{i}] {t.get('title', '')}"
        if t.get("summary"):
            topics_text += f"\n      摘要：{t['summary'][:200]}"
        if t.get("source"):
            topics_text += f"\n      来源：{t['source']}"

    forbidden = f"\n禁止出现以下词汇：{forbidden_words}。" if forbidden_words else ""
    sp = f"\n核心卖点：{selling_points}。" if selling_points else ""

    return (
        f"你是一位资深今日头条运营专家。请为品牌【{brand_name}】（{industry}行业，目标用户：{audience}）"
        f"生成一条今日头条图文内容。{sp}{forbidden}\n\n"
        f"内容风格：{tone}\n\n"
        f"可供参考的今日热点话题（优先使用 TOP1）：{topics_text}\n\n"
        "请严格按照以下要求生成：\n\n"
        "1. 【标题】1个，10~30字，新闻式标题，含核心信息点，注重信息量和价值感，不用夸张标题党\n"
        "2. 【正文】300~800字，结构：\n"
        f"   - 开头：一句话点明核心信息或新闻由头\n"
        f"   - 中间：信息型内容或深度分析，自然融入品牌（{brand_name}）的价值或观点\n"
        "   - 结尾：总结观点或引导互动（欢迎在评论区讨论... / 关注我了解更多...）\n"
        "3. 【标签】7-10 个话题标签（#开头），采用“金字塔结构”分层，核心原则：\n"
        "   【平台热度 × 内容贴合】双权重——不堆砌热门泛标签，精准长尾优先于无关热词\n"
        f"   • 大流量泛标签 1-2个：覆盖【{industry}】赛道，承接平台推荐流量\n"
        "   • 精准垂类标签 2-3个：直接对应文章核心主题和功能关键词\n"
        f"   • 人群定位标签 1个：锁定【{audience}】，帮助系统精准分发\n"
        "   • 场景标签 1个：对应用户具体使用场景，提升点击和收藏\n"
        "   • 痛点/需求标签 1个：匹配用户主动搜索意图，承接搜索流量\n"
        f"   • 品牌沉淀标签 1个：固定使用【#{brand_name}】积累账号资产\n\n"
        "输出格式（纯 JSON，不要其他文字，不要 markdown 代码块，严禁在 JSON 中添加任何注释）：\n"
        "{\"title\": \"标题（10~30字）\", \"article\": \"正文...\", \"topics\": [\"#标签1\", \"#标签2\"]}"
    )


def _call_agent_api(prompt: str, agent_url: str = None) -> dict:
    """调用 Agent API（/chat）生成文章，和前端 agent-web 对话同一方式。

    由 agent.exe 根据 config.yaml 的 provider 路由处理 LLM 调用，
    无需本脚本处理任何 API Key。
    """
    import requests
    base_url = (agent_url or os.environ.get("BAICLAW_AGENT_API_URL")
                or "http://localhost:8080/api")
    api_url = f"{base_url.rstrip('/')}/chat"
    print(f"[generate] 调用 Agent API: {api_url}", flush=True)

    resp = requests.post(
        api_url,
        json={"message": prompt},
        timeout=180,
    )
    resp.raise_for_status()
    result = resp.json()  # {"message": "...", "session_id": "..."}

    raw_message = result.get("message", "")
    # 预处理：修复 LLM 常见的 JSON 格式问题
    cleaned = raw_message.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    if m:
        cleaned = m.group(1).strip()
    # 修复 # 在引号外 (#"topic" -> "#topic")
    cleaned = re.sub(r'(?<=[,\[])\s*#\s*"', '"#', cleaned)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    cleaned = re.sub(r',\s*}', '}', cleaned)
    try:
        draft = json.loads(cleaned)
    except json.JSONDecodeError:
        raise RuntimeError(f"Agent 返回内容无法解析为 JSON:\n{raw_message}")

    if "description" in draft and "article" not in draft:
        draft["article"] = draft.pop("description")
    draft["platform"] = "toutiao"
    draft.setdefault("images", [])
    if isinstance(draft.get("topics"), list) and len(draft["topics"]) > 10:
        draft["topics"] = draft["topics"][:10]
    return draft


def generate_ai_image(prompt: str, output_path: str, api_key: str) -> str:
    """调用火山引擎 Ark API（Seedream）生成图片，保存到 output_path，返回路径。"""
    import requests
    resp = requests.post(
        "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        json={
            "model": "doubao-seedream-4-5-251128",
            "prompt": prompt,
            "size": "2K",
            "response_format": "url",
            "watermark": False,
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=120,
    )
    resp.raise_for_status()
    image_url = resp.json()["data"][0]["url"]

    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()
    Path(output_path).write_bytes(img_resp.content)
    print(f"[generate] 图片已保存: {output_path}", flush=True)
    return output_path


def _build_image_prompts(draft: dict, brand_info: dict) -> list[str]:
    """根据标题和正文构造 3 个图片 prompt（封面 + 内容图 × 2）。"""
    title = draft.get("title", "")
    article = draft.get("article", "")
    brand_name = brand_info.get("name", "") if brand_info else ""
    industry = brand_info.get("industry", "") if brand_info else ""

    brand_part = f"品牌：{brand_name}，" if brand_name else ""
    industry_part = f"行业：{industry}，" if industry else ""

    # 封面：基于标题
    cover_prompt = (
        f"头条文章封面图，标题：{title}。{brand_part}{industry_part}"
        "现代简洁设计风格，居中留白区域适合叠加标题文字，高清摄影质感，16:9"
    )

    # 从正文切分主题
    sentences = [s.strip() for s in re.split(r'[。！？\n]+', article) if len(s.strip()) > 10]
    mid = len(sentences) // 3
    if mid < 1:
        mid = 1

    part1 = "。".join(sentences[:mid])[:200]
    content_prompt1 = f"文章配图：{part1}。写实摄影风格，自然光线，高清细节，16:9"

    part2 = "。".join(sentences[-mid:])[:200]
    content_prompt2 = f"文章配图：{part2}。写实摄影风格，自然光线，高清细节，16:9"

    return [cover_prompt, content_prompt1, content_prompt2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topics-json", default=None, help="collect 输出的 topics JSON（省略时自动采集）")
    parser.add_argument("--agent-url", default=None, help="Agent API 地址（默认 http://localhost:8080/api）")
    args, _ = parser.parse_known_args()

    args.image_count = 3

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
    brief_path = str(run_dir / "content-brief.json")

    print(f"[generate] draft output: {args.output}", flush=True)

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

    draft = _call_agent_api(instruction, agent_url=args.agent_url)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    print(f"[generate] draft written: {args.output}", flush=True)

    # 渲染图片：优先使用 AI 生成，无 ARK_API_KEY 时回退到 Playwright 截图
    ark_key = os.environ.get("ARK_API_KEY")
    if ark_key:
        print("[generate] AI 生成图片...", flush=True)
        image_prompts = _build_image_prompts(draft, brand_info)
        image_paths = []
        for i, prompt in enumerate(image_prompts[:args.image_count]):
            img_name = f"0{i+1}_{['cover','content_1','content_2'][i]}.png"
            img_path = str(run_dir / img_name)
            print(f"[generate] 生成图片 {i+1}/{args.image_count}: {img_name}", flush=True)
            generate_ai_image(prompt, img_path, ark_key)
            image_paths.append(img_path)
        draft["images"] = image_paths
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
    else:
        print("[generate] 使用 Playwright 截图生成图片（无 ARK_API_KEY 时回退）", flush=True)
        sys.path.insert(0, str(Path(__file__).parent))
        from render_cover import render_draft
        render_draft(args.output)

    # 提交审核
    print("[generate] 提交审核...", flush=True)
    sys.path.insert(0, str(Path(__file__).parent))
    import submit_for_review as _sfr
    review_id = _sfr.submit_draft(args.output)

    # 重新读取 draft.json 获取最终使用的图片数量（可能已被截断）
    with open(args.output, "r", encoding="utf-8") as _f:
        _final = json.load(_f)
    image_count_actual = len(_final.get("images", []))

    print(json.dumps({
        "draft_path":   args.output,
        "brief_path":   brief_path,
        "review_id":    review_id,
        "image_count":  image_count_actual,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
