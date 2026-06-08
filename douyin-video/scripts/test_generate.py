#!/usr/bin/env python3
# @author FondaWu
"""
test_generate.py — 测试 generate.py 中的辅助函数
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))

from generate import build_article_brief


def test_article_brief_no_video_prompt_field():
    brand = {"name": "测试品牌", "industry": "电商", "tone": "直接", "targetAudience": "年轻人",
             "sellingPoints": ["卖点A"], "forbiddenWords": []}
    topics = {"topics": [{"title": "热点1", "summary": "摘要1", "source": "抖音热榜"}]}
    instruction = build_article_brief(brand, topics)
    assert "video_prompt" not in instruction
    assert "video" not in instruction.lower()


def test_article_brief_contains_topic_summary():
    brand = {"name": "品牌", "industry": "行业", "tone": "风格", "targetAudience": "用户",
             "sellingPoints": [], "forbiddenWords": []}
    topics = {"topics": [{"title": "热点标题", "summary": "热点摘要内容", "source": "来源"}]}
    instruction = build_article_brief(brand, topics)
    assert "热点标题" in instruction
    assert "热点摘要内容" in instruction


def test_article_brief_output_json_format_without_video():
    brand = {"name": "品牌", "industry": "行业", "tone": "风格", "targetAudience": "用户",
             "sellingPoints": [], "forbiddenWords": []}
    topics = {"topics": []}
    instruction = build_article_brief(brand, topics)
    # 输出格式示例中不应含 video_prompt 字段
    assert '"video_prompt"' not in instruction
    # 应含 title / article / topics 字段描述
    assert '"title"' in instruction
    assert '"article"' in instruction
    assert '"topics"' in instruction


# ── Task 2 tests ──────────────────────────────────────────────────────────────

from generate import build_video_brief, call_llm_for_video_prompt
from unittest.mock import patch
import generate as gen


def test_video_brief_no_topic_summary():
    brand = {"name": "品牌", "industry": "电商", "tone": "简洁", "targetAudience": "用户",
             "sellingPoints": ["全球最大平台"], "forbiddenWords": []}
    title = "10倍效率！AI正在改变采购方式"
    instruction = build_video_brief(brand, title)
    assert "article" not in instruction
    assert "topics" not in instruction
    assert "画面" in instruction or "场景" in instruction


def test_video_brief_output_format_is_json_with_video_prompt_key():
    brand = {"name": "品牌", "industry": "行业", "tone": "风格", "targetAudience": "用户",
             "sellingPoints": [], "forbiddenWords": []}
    instruction = build_video_brief(brand, "标题")
    assert '"video_prompt"' in instruction


def test_call_llm_for_video_prompt_returns_string_truncated_to_500():
    with patch.object(gen, "call_llm_json_inner", return_value={"video_prompt": "A" * 600}):
        result = call_llm_for_video_prompt("any instruction")
    assert isinstance(result, str)
    assert len(result) <= 500


def test_call_llm_for_video_prompt_fallback_empty():
    with patch.object(gen, "call_llm_json_inner", return_value={}):
        result = call_llm_for_video_prompt("any instruction")
    assert result == ""


# ── Task 3 tests ──────────────────────────────────────────────────────────────

import json


def test_main_calls_two_llm_stages(tmp_path):
    """验证 main() 两阶段调用：文案 instruction 不含 video_prompt，视频 instruction 不含热点摘要"""
    topics_data = {"topics": [{"title": "热点标题", "summary": "这是热点摘要内容", "source": "源"}]}
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(json.dumps(topics_data, ensure_ascii=False), encoding="utf-8")

    article_result = {"title": "测试标题", "article": "文案内容", "topics": ["#标签1"],
                      "platform": "douyin"}
    captured = {}

    def fake_call_llm(instruction):
        captured["article_instruction"] = instruction
        return article_result

    def fake_call_llm_for_video_prompt(instruction):
        captured["video_instruction"] = instruction
        return "cinematic scene with warm lighting"

    with patch.object(gen, "call_llm", side_effect=fake_call_llm), \
         patch.object(gen, "call_llm_for_video_prompt", side_effect=fake_call_llm_for_video_prompt), \
         patch.object(gen, "get_enterprise_data", return_value={}), \
         patch.object(gen, "get_first_brand", return_value={"name": "品牌", "industry": "行业",
                                                             "tone": "风格", "targetAudience": "用户",
                                                             "sellingPoints": [], "forbiddenWords": []}), \
         patch.object(gen, "get_dashscope_api_key", return_value="fake-key"), \
         patch.object(gen, "create_video_task", return_value="task-123"), \
         patch.object(gen, "poll_video_task", return_value="http://example.com/video.mp4"), \
         patch.object(gen, "download_video"), \
         patch("submit_for_review.submit_draft", return_value="review-001"), \
         patch("sys.argv", ["generate.py", "--topics-json", str(topics_file)]):
        gen.main()

    assert '"video_prompt"' not in captured["article_instruction"]
    assert "这是热点摘要内容" not in captured["video_instruction"]
    assert "测试标题" in captured["video_instruction"]
