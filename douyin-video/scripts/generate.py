#!/usr/bin/env python3
# @author FondaWu
"""
douyin-video/generate.py — 生成抖音短视频内容

流程：采集热点 → LLM 生成脚本/提示词 → 万相 wan2.5-t2v-preview API 生成竖屏视频 → 下载 → 提交审核
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

_SKILLS_ROOT = Path(__file__).parent.parent.parent  # SKILLs/
sys.path.insert(0, str(_SKILLS_ROOT / "common"))

from enterprise_db import get_enterprise_data, get_first_brand, get_dashscope_api_key

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)

_WANXIANG_CREATE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
_WANXIANG_QUERY_URL  = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
_WANXIANG_MODEL      = "wan2.5-t2v-preview"
_VIDEO_SIZE          = "480*832"    # 9:16 竖屏 480P（wan2.5支持的枚举值），省成本
_VIDEO_DURATION      = 10           # 秒
_POLL_INTERVAL_S     = 15           # 轮询间隔
_MAX_POLLS           = 60           # 最长等待 15 分钟


def build_article_brief(brand_info: dict, topics: dict) -> str:
    top_items = topics.get("topics", [])
    top3 = top_items[:3] if top_items else []

    brand_name     = brand_info.get("name", "未知品牌")
    industry       = brand_info.get("industry", "未知行业")
    tone           = brand_info.get("tone", "直接有力")
    audience       = brand_info.get("targetAudience", "年轻用户")
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
        f"你是一位资深抖音短视频运营专家。请为品牌【{brand_name}】（{industry}行业，目标用户：{audience}）"
        f"撰写一条抖音短视频的文案。{sp}{forbidden}\n\n"
        f"内容风格：{tone}\n\n"
        f"可供参考的今日热点话题（优先使用 TOP1）：{topics_text}\n\n"
        "请严格按照以下要求输出：\n\n"
        "1. 【标题】1个，≤30字，含爆款钩子词，适合抖音曝光标题\n"
        "2. 【描述文案】100~200字，抖音风格，结尾带互动引导（评论区/关注）\n"
        "3. 【标签】恰好 5 个话题标签（#开头）\n\n"
        "输出格式（纯 JSON，不要其他文字，不要 markdown 代码块）：\n"
        '{"title": "标题（≤30字）", "article": "描述文案...", '
        '"topics": ["#标签1", "#标签2", "#标签3", "#标签4", "#标签5"]}'
    )


def build_video_brief(brand_info: dict, title: str) -> str:
    brand_name     = brand_info.get("name", "未知品牌")
    industry       = brand_info.get("industry", "未知行业")
    tone           = brand_info.get("tone", "直接有力")
    selling_points = "、".join(brand_info.get("sellingPoints") or [])

    sp_line = f"品牌核心价值：{selling_points}。" if selling_points else ""

    return (
        f"你是一位专业的 AI 文生视频提示词工程师。请为品牌【{brand_name}】（{industry}行业）"
        f"生成一段适合 9:16 竖屏短视频的中文画面描述。{sp_line}\n"
        f"视频风格：{tone}\n"
        f"主题关键词（仅作视觉灵感参考，不要照搬文字）：{title}\n\n"
        "要求：\n"
        "- 用中文描述：主体、场景/环境、构图、色调、运镜、氛围\n"
        "- 风格：电影质感、高画质、9:16 竖屏格式\n"
        "- 禁止出现：人物面部、品牌 Logo 文字、字幕覆盖层、旁白语句、抽象概念\n"
        "- 仅使用具体可视化的语言描述画面，不超过 200 字\n\n"
        '输出格式（纯 JSON，不要其他文字，不要 markdown 代码块）：\n'
        '{"video_prompt": "中文画面描述"}'
    )


def call_llm_json_inner(instruction: str, max_tokens: int = 512) -> dict:
    from llm import call_llm_json
    return call_llm_json(instruction, max_tokens=max_tokens)


def call_llm_for_video_prompt(instruction: str) -> str:
    try:
        result = call_llm_json_inner(instruction, max_tokens=512)
        prompt = result.get("video_prompt", "")
        if not prompt:
            print("[generate][warn] 阶段二 LLM 未返回 video_prompt，将回退到标题作为提示词", flush=True)
        return prompt[:500]
    except Exception as e:
        print(f"[generate][warn] 阶段二 LLM 调用失败（{e}），将回退到标题作为提示词", flush=True)
        return ""


def call_llm(instruction: str) -> dict:
    from llm import call_llm_json
    draft = call_llm_json(instruction, max_tokens=1024)
    if "description" in draft and "article" not in draft:
        draft["article"] = draft.pop("description")
    draft["platform"] = "douyin"
    draft.setdefault("topics", [])
    if isinstance(draft.get("title"), str) and len(draft["title"]) > 30:
        draft["title"] = draft["title"][:30]
    if isinstance(draft.get("topics"), list) and len(draft["topics"]) > 5:
        draft["topics"] = draft["topics"][:5]
    return draft


def create_video_task(prompt: str, api_key: str) -> str:
    """提交万相文生视频任务，返回 task_id"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    body = {
        "model": _WANXIANG_MODEL,
        "input": {"prompt": prompt},
        "parameters": {
            "size": _VIDEO_SIZE,
            "duration": _VIDEO_DURATION,
            "prompt_extend": True,
        },
    }
    resp = requests.post(_WANXIANG_CREATE_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"万相 API 未返回 task_id: {data}")
    print(f"[generate] 万相任务已创建: task_id={task_id}", flush=True)
    return task_id


def poll_video_task(task_id: str, api_key: str) -> str:
    """轮询等待视频生成完成，返回视频下载 URL"""
    url = _WANXIANG_QUERY_URL.format(task_id=task_id)
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(1, _MAX_POLLS + 1):
        time.sleep(_POLL_INTERVAL_S)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output", {})
        status = output.get("task_status", "UNKNOWN")
        print(f"[generate] 轮询第 {attempt} 次，状态: {status}", flush=True)

        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError("SUCCEEDED 但未返回 video_url")
            return video_url
        elif status in ("FAILED", "CANCELED", "UNKNOWN"):
            err_code = output.get("code", "")
            err_msg  = output.get("message", "")
            raise RuntimeError(f"视频生成失败: status={status} code={err_code} message={err_msg}")

    raise TimeoutError(f"视频生成超时（等待 {_MAX_POLLS * _POLL_INTERVAL_S // 60} 分钟）")


def download_video(video_url: str, save_path: Path) -> None:
    """下载视频文件到本地"""
    print(f"[generate] 下载视频中...", flush=True)
    resp = requests.get(video_url, stream=True, timeout=300)
    resp.raise_for_status()
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    size_kb = save_path.stat().st_size // 1024
    print(f"[generate] 视频已保存: {save_path} ({size_kb} KB)", flush=True)


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
        from collect import collect_from_db
        args.topics_json = collect_from_db(str(run_dir))

    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(run_dir / "draft.json")
    brief_path  = str(run_dir / "content-brief.json")
    video_path  = run_dir / "video.mp4"

    data       = get_enterprise_data()
    brand_info = get_first_brand(data) if data else {}
    if brand_info.get("name"):
        print(f"[generate] 品牌: {brand_info['name']}", flush=True)
    else:
        print("[generate][warn] 未找到企业品牌信息，使用默认值", flush=True)

    topics = json.loads(Path(args.topics_json).read_text(encoding="utf-8"))

    # 阶段一：生成文案（标题/描述/标签）
    article_instruction = build_article_brief(brand_info=brand_info, topics=topics)
    Path(brief_path).write_text(
        json.dumps({"instruction": article_instruction}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[generate] brief written: {brief_path}", flush=True)

    draft = call_llm(article_instruction)
    print(f"[generate] LLM 生成标题: {draft.get('title', '?')}", flush=True)

    api_key = get_dashscope_api_key()
    if not api_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，请运行 setup_api_keys.py 或设置环境变量")

    # 阶段二：独立生成视频提示词（只传品牌 + 标题，不传热点摘要）
    video_instruction = build_video_brief(brand_info=brand_info, title=draft.get("title", ""))
    video_prompt = call_llm_for_video_prompt(video_instruction)
    if not video_prompt:
        video_prompt = draft.get("title", "")
        print(f"[generate][warn] 视频提示词回退为标题: {video_prompt}", flush=True)
    draft["video_prompt"] = video_prompt
    print(f"[generate] 视频提示词: {video_prompt[:120]}", flush=True)

    task_id   = create_video_task(video_prompt, api_key)
    video_url = poll_video_task(task_id, api_key)
    download_video(video_url, video_path)

    draft["video"] = str(video_path)

    Path(output_path).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[generate] draft written: {output_path}", flush=True)

    print("[generate] 提交审核...", flush=True)
    import submit_for_review as _sfr
    review_id = _sfr.submit_draft("douyin", output_path)

    print(json.dumps({
        "draft_path": output_path,
        "brief_path": brief_path,
        "review_id":  review_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
