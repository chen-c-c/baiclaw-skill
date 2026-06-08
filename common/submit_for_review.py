#!/usr/bin/env python3
# @author FondaWu
"""
通用 submit_for_review — 提交文章到 agent-backend 审核队列。
用法：python submit_for_review.py --platform <xiaohongshu|zhihu|douyin|wechat-channels> --draft-json <path>
也可由平台脚本 import 调用：from submit_for_review import submit_draft; submit_draft("zhihu", path)
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from enterprise_db import (
    get_admin_api_url, get_admin_token, get_enterprise_data, get_first_brand,
    get_zhihu_account, get_xhs_account, get_douyin_account, get_wechat_channels_account,
    get_toutiao_account,
)

_ACCOUNT_GETTERS = {
    "zhihu":           get_zhihu_account,
    "xiaohongshu":     get_xhs_account,
    "douyin":          get_douyin_account,
    "wechat-channels": get_wechat_channels_account,
    "toutiao":         get_toutiao_account,
}


def _build_titles(platform: str, draft: dict) -> str:
    if platform == "xiaohongshu":
        return json.dumps(draft.get("titles", []), ensure_ascii=False)
    return json.dumps([draft.get("title", "")], ensure_ascii=False)


def _build_topics(platform: str, draft: dict) -> str:
    if platform == "zhihu":
        return json.dumps([], ensure_ascii=False)
    return json.dumps(draft.get("topics", []), ensure_ascii=False)


def submit_draft(platform: str, draft_path: str) -> str:
    """提交文章到审核队列，返回 review_id。失败时抛出 RuntimeError。"""
    _path = Path(draft_path)
    with open(_path, "r", encoding="utf-8") as f:
        draft = json.load(f)

    image_data = []
    for img_path in draft.get("images", []):
        p = Path(img_path)
        if p.exists():
            image_data.append({
                "fileName": p.name,
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            })
    # 视频平台：将视频文件编码为 imageData 中的单条记录
    video_path = draft.get("video", "")
    if video_path:
        vp = Path(video_path)
        if vp.exists():
            image_data.append({
                "fileName": vp.name,
                "data": base64.b64encode(vp.read_bytes()).decode("ascii"),
            })

    ent = get_enterprise_data() or {}
    brand = get_first_brand(ent)
    account_getter = _ACCOUNT_GETTERS.get(platform)
    if account_getter is None:
        raise RuntimeError(f"不支持的平台: {platform}")
    account = account_getter(ent)

    # 如果 get_first_brand 没有拿到品牌信息，尝试通过账号的 brandId 匹配品牌
    if not brand.get("id") and account and account.get("brandId"):
        profiles = ent.get("brandProfiles") or []
        for b in profiles:
            if b.get("id") == account.get("brandId"):
                brand = b
                break

    admin_url   = os.environ.get("BAICLAW_ADMIN_API_URL", "").rstrip("/") or get_admin_api_url()
    admin_token = os.environ.get("BAICLAW_ADMIN_TOKEN",   "").strip('"')  or get_admin_token()
    if not admin_url or not admin_token:
        raise RuntimeError("缺少 BAICLAW_ADMIN_API_URL 或 BAICLAW_ADMIN_TOKEN")

    # ─────────────────────────────────────────────────────────────────────
    # SHARED ENDPOINT — DO NOT change to a platform-specific path
    # (e.g. /device/toutiao-review/submit). All platforms POST to the
    # same endpoint; the backend routes via the "platform" field below.
    # ─────────────────────────────────────────────────────────────────────
    url = f"{admin_url}/device/xhs-review/submit"  # ← shared — do NOT make platform-specific
    print(f"[submit_for_review] 提交 {platform} 审核: {url}", flush=True)
    resp = requests.post(
        url,
        json={
            "platform":    platform,
            "brandId":     brand.get("id", ""),
            "brandName":   brand.get("name", ""),
            "accountId":   account.get("id", "") if account else "",
            "accountName": account.get("accountName", "") if account else "",
            "titles":      _build_titles(platform, draft),
            "article":     draft.get("article", ""),
            "topics":      _build_topics(platform, draft),
            "imageData":   json.dumps(image_data, ensure_ascii=False),
            "draftJson":   json.dumps(draft, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 200:
        raise RuntimeError(result.get("message", "提交失败"))
    return result["data"]["id"]


def main(platform: str = None):
    if platform is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--platform", required=True,
                            choices=list(_ACCOUNT_GETTERS.keys()))
        parser.add_argument("--draft-json", required=True, help="draft.json 路径")
        args = parser.parse_args()
        platform = args.platform
        draft_json = args.draft_json
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--draft-json", required=True)
        draft_json = parser.parse_args().draft_json

    try:
        review_id = submit_draft(platform, draft_json)
        print(f"[submit_for_review] 提交成功，审核ID: {review_id}", flush=True)
        print(json.dumps({"success": True, "review_id": review_id}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
