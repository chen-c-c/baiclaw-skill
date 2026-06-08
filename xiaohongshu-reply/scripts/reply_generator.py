#!/usr/bin/env python3
"""F3+F4: 意图分类 + 回复生成 — Classify intent and generate replies via LLM."""
import argparse
import json
import random
import sys
from pathlib import Path

from xhs_reply_utils import (
    call_llm, extract_json, read_json, write_json,
    is_spam_prefilter, extract_comment_body, FIXED_REPLIES, CONFIDENCE_THRESHOLD,
)
from enterprise_db import get_enterprise_data, get_first_brand


def load_brand_context() -> dict:
    data = get_enterprise_data()
    if not data:
        print("[reply-gen] 未读取到企业数据，使用默认品牌信息", flush=True)
        return {}
    brand = get_first_brand(data)
    if not brand:
        print("[reply-gen] 未找到品牌信息，使用默认值", flush=True)
        return {}
    return brand


# ── 意图分类 Prompt ────────────────────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = (
    'You are a Xiaohongshu comment and message classifier for the brand "{brand_name}" ({industry}). '
    "Classify into exactly one category.\n\n"
    'Categories:\n'
    '- "inquiry": Questions about products, pricing, availability, or how to buy. '
    'Examples: "怎么买？", "多少钱？", "有教程吗？", "在哪里下单？"\n'
    '- "praise": Positive feedback, compliments, appreciation, satisfaction. '
    'Examples: "太好用了！", "种草了", "已下单", "支持", "很棒"\n'
    '- "neutral": General comments without strong positive or negative sentiment. '
    'Examples: "收藏了", "马克", brief reactions, offhand remarks.\n'
    '- "complaint": Dissatisfaction, problem reports, refund requests, criticism, '
    'sarcasm, derogatory remarks. NEEDS human handling. '
    'Examples: "不好用", "被骗了", "质量太差", "退款", "垃圾"\n'
    '- "spam": Irrelevant promotion, ads, gibberish, bot-like patterns. '
    'See spam rules below.\n\n'
    'SPAM RULES — classify as "spam" ONLY when at least ONE indicator is clearly present:\n'
    '1. Promotes unrelated products, services, brands, or accounts\n'
    '2. Contains URLs, QR codes, phone numbers, or "加V/加微信/私信" solicitations\n'
    '3. Gibberish, random characters, copy-paste chain messages, bot-generated text\n'
    '4. Entirely off-topic (viral comments, chain letters, unrelated trending topics)\n'
    '5. Obvious ad templates: "兼职招聘", "日赚", "免费领取", clickbait patterns\n'
    '6. Repeated spam-like short patterns already pre-filtered, confirm borderline cases\n\n'
    'BORDERLINE GUIDANCE: When a message could be either "neutral" or "spam", '
    'classify as "neutral". Short ambiguous messages like "哈哈", "嗯", "哦", '
    'single emoji/word replies, and brief reactions are "neutral", not spam. '
    'Low-effort engagement ("学到了", "不错", "好看") is also "neutral". '
    'Classify as "spam" ONLY when there are clear, unambiguous spam indicators.\n\n'
    'Respond ONLY with valid JSON: {{"intent": "<category>", "confidence": 0.0-1.0, "reason": "<chinese reason>"}}'
)

INTENT_USER_PROMPT = "Classify this message:\n{text}"

# ── 回复生成 Prompt ────────────────────────────────────────────────────────────

COMMENT_REPLY_SYSTEM = (
    'You are a friendly Xiaohongshu CSR for "{brand_name}" ({industry}).\n\n'
    "Rules:\n"
    "1. Reply in Chinese, max 50 characters.\n"
    "2. Sound like a real Xiaohongshu user, not a bot.\n"
    "3. Be warm, natural, conversational.\n"
    "4. Gently guide toward conversion when appropriate.\n"
    "5. DO NOT ask for phone numbers or personal info.\n\n"
    "Brand selling points: {selling_points}\n"
    "Forbidden words: {forbidden_words}\n\n"
    "Reply (max 50 Chinese chars, just the reply text):"
)

COMMENT_REPLY_USER = "User comment: {text}\nIntent: {intent}\n\nGenerate a reply:"

DM_REPLY_SYSTEM = (
    'You are a helpful Xiaohongshu CSR for "{brand_name}" ({industry}).\n\n'
    "Rules:\n"
    "1. Reply in Chinese, max 200 characters.\n"
    "2. Address the user's specific question directly.\n"
    "3. Be helpful and solution-oriented.\n"
    "4. Include a gentle CTA when appropriate.\n"
    "5. DO NOT request phone numbers, addresses, or payment info.\n\n"
    "Brand selling points: {selling_points}\n"
    "Forbidden words: {forbidden_words}\n\n"
    "Conversation context:\n{context}\n\n"
    "Reply (max 200 Chinese chars, just the reply text):"
)

DM_REPLY_USER = "Generate a reply."


def classify_intent(text: str, brand_name: str, industry: str, model: str) -> dict:
    system = INTENT_SYSTEM_PROMPT.format(brand_name=brand_name, industry=industry)
    user = INTENT_USER_PROMPT.format(text=text[:500])
    try:
        resp = call_llm(system, user, model=model, max_tokens=256)
        return extract_json(resp)
    except Exception as e:
        print(f"[reply-gen] 意图分类失败: {e}", file=sys.stderr)
        return {"intent": "spam", "confidence": 0.0, "reason": f"LLM error: {e}"}


def generate_reply(text: str, intent: str, brand: dict, model: str,
                   is_dm: bool = False, context: str = "") -> str:
    if intent in ("complaint", "spam"):
        return ""

    if not is_dm:
        return random.choice(FIXED_REPLIES)

    brand_name = brand.get("name", "默认品牌")
    industry = brand.get("industry", "通用")
    selling_points = ",".join(brand.get("sellingPoints") or ["优质产品"])
    forbidden_words = ",".join(brand.get("forbiddenWords") or [])

    system = DM_REPLY_SYSTEM.format(
        brand_name=brand_name, industry=industry,
        selling_points=selling_points, forbidden_words=forbidden_words,
        context=context or text[:500],
    )
    user = DM_REPLY_USER
    max_tok = 512

    try:
        reply = call_llm(system, user, model=model, max_tokens=max_tok)
        if not reply.strip():
            return ""
        if len(reply) > 200:
            reply = reply[:200].rsplit("。", 1)[0] + "。"
        return reply
    except Exception as e:
        print(f"[reply-gen] 回复生成失败: {e}", file=sys.stderr)
        return ""


def process_items(items: list[dict], type_: str, brand: dict, model: str) -> list[dict]:
    brand_name = brand.get("name", "默认品牌")
    industry = brand.get("industry", "通用")

    results = []
    for item in items:
        text = ""
        context_text = ""
        if type_ == "dm":
            msgs = item.get("messages") or []
            text = msgs[-1].get("content", "") if msgs else ""
            context_text = "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in msgs)
        else:
            text = item.get("content", "")

        result = {
            "type": type_,
            "sub_type": item.get("sub_type", type_),
            "source_id": item.get("comment_id") or item.get("conversation_id") or "",
            "nickname": item.get("nickname", ""),
        }
        if type_ == "comment":
            result["note_title"] = item.get("note_title", "")
            result["original_content"] = text
        else:
            result["conversation_context"] = item.get("messages", [])

        if type_ == "comment":
            body = extract_comment_body(text)
            if is_spam_prefilter(body):
                intent = "spam"
                result["intent"] = intent
                result["confidence"] = 1.0
                result["needs_human"] = False
                result["skipped"] = True
                result["reply"] = None
                print(f"[reply-gen] {type_} {result['source_id']}: spam (pre-filter) → 跳过", flush=True)
            else:
                intent_result = classify_intent(text, brand_name, industry, model)
                intent = intent_result.get("intent", "neutral")
                # Confidence gate: low-confidence spam → neutral
                if intent == "spam" and intent_result.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
                    print(f"[reply-gen] {type_} {result['source_id']}: spam (low conf {intent_result.get('confidence', 0):.2f}) → neutral", flush=True)
                    intent = "neutral"
                result["intent"] = intent
                result["confidence"] = intent_result.get("confidence", 0.0)
                if intent == "complaint":
                    result["needs_human"] = True
                    result["skipped"] = False
                    result["reply"] = None
                    print(f"[reply-gen] {type_} {result['source_id']}: complaint → 需人工处理", flush=True)
                elif intent == "spam":
                    result["needs_human"] = False
                    result["skipped"] = True
                    result["reply"] = None
                    print(f"[reply-gen] {type_} {result['source_id']}: spam → 跳过", flush=True)
                else:
                    result["needs_human"] = False
                    result["skipped"] = False
                    reply = generate_reply(text, intent, brand, model,
                                           is_dm=False, context=context_text)
                    result["reply"] = reply if reply else None
                    if result["reply"]:
                        print(f"[reply-gen] {type_} {result['source_id']}: {intent} → 已生成({len(reply)}字)", flush=True)
                    else:
                        print(f"[reply-gen] {type_} {result['source_id']}: {intent} → 回复为空", flush=True)
        else:
            # Pre-filter DM for mechanical spam (raw DM text, no notification wrapper)
            if is_spam_prefilter(text):
                result["intent"] = "spam"
                result["confidence"] = 1.0
                result["needs_human"] = False
                result["skipped"] = True
                result["reply"] = None
                print(f"[reply-gen] {type_} {result['source_id']}: spam (pre-filter) → 跳过", flush=True)
            else:
                intent_result = classify_intent(text, brand_name, industry, model)
                intent = intent_result.get("intent", "neutral")
                # Confidence gate: low-confidence spam → neutral
                if intent == "spam" and intent_result.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
                    print(f"[reply-gen] {type_} {result['source_id']}: spam (low conf {intent_result.get('confidence', 0):.2f}) → neutral", flush=True)
                    intent = "neutral"
                result["intent"] = intent
                result["confidence"] = intent_result.get("confidence", 0.0)
                if intent == "complaint":
                    result["needs_human"] = True
                    result["skipped"] = False
                    result["reply"] = None
                    print(f"[reply-gen] {type_} {result['source_id']}: complaint → 需人工处理", flush=True)
                elif intent == "spam":
                    result["needs_human"] = False
                    result["skipped"] = True
                    result["reply"] = None
                    print(f"[reply-gen] {type_} {result['source_id']}: spam → 跳过", flush=True)
                else:
                    result["needs_human"] = False
                    result["skipped"] = False
                    reply = generate_reply(text, intent, brand, model,
                                           is_dm=True, context=context_text)
                    result["reply"] = reply if reply else None
                    if result["reply"]:
                        print(f"[reply-gen] {type_} {result['source_id']}: {intent} → 已生成({len(reply)}字)", flush=True)
                    else:
                        print(f"[reply-gen] {type_} {result['source_id']}: {intent} → 回复为空", flush=True)

        if "error" in item:
            result["error"] = item["error"]

        results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(description="意图分类 + 回复生成")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    brand = load_brand_context()
    items = []

    comments_path = args.run_dir / "comments.json"
    if comments_path.exists():
        comments_data = read_json(comments_path)
        result_comments = process_items(comments_data.get("comments", []), "comment", brand, args.model)
        items.extend(result_comments)
        print(f"[reply-gen] 处理 {len(result_comments)} 条评论", flush=True)
    else:
        print("[reply-gen] comments.json 不存在，跳过评论", flush=True)

    dms_path = args.run_dir / "dms.json"
    if dms_path.exists():
        dms_data = read_json(dms_path)
        result_dms = process_items(dms_data.get("conversations", []), "dm", brand, args.model)
        items.extend(result_dms)
        print(f"[reply-gen] 处理 {len(result_dms)} 条私信", flush=True)
    else:
        print("[reply-gen] dms.json 不存在，跳过私信", flush=True)

    sent = sum(1 for i in items if not i.get("skipped") and not i.get("needs_human") and i.get("reply"))
    needs_human = sum(1 for i in items if i.get("needs_human"))
    skipped = sum(1 for i in items if i.get("skipped"))
    errors = sum(1 for i in items if i.get("error"))

    output = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
        ).isoformat(),
        "model": args.model or "",
        "items": items,
    }
    write_json(output, args.run_dir / "replies.json")
    print(f"[reply-gen] 已保存到 {args.run_dir / 'replies.json'}", flush=True)
    print(json.dumps({"total": len(items), "to_send": sent,
                       "needs_human": needs_human, "skipped": skipped, "errors": errors},
                      ensure_ascii=False))


if __name__ == "__main__":
    main()
