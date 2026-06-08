#!/usr/bin/env python3
# @author FondaWu
"""
通用 check_and_publish — 拉取审核通过文章并调用平台 publish.py 发布。
用法：python check_and_publish.py --platform <xiaohongshu|zhihu|douyin|wechat-channels>
也可由平台薄包装直接 import 调用：from check_and_publish import main; main("zhihu")
"""
import argparse
import base64
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enterprise_db import get_admin_api_url, get_admin_token

_SKILLS_ROOT = Path(__file__).parent.parent  # SKILLs/


def _build_draft(platform: str, art: dict, images: list) -> dict:
    """构造临时 draft.json，各平台字段略有差异。"""
    if platform == "xiaohongshu":
        return {
            "titles":  [art["finalTitle"]],
            "article": art["finalArticle"],
            "topics":  art.get("topics", []),
            "images":  images,
        }
    if platform == "zhihu":
        return {
            "title":    art["finalTitle"],
            "article":  art["finalArticle"],
            "images":   images,
            "platform": "zhihu",
        }
    if platform == "toutiao":
        return {
            "title":    art["finalTitle"],
            "article":  art["finalArticle"],
            "topics":   art.get("topics", []),
            "images":   images,
            "platform": "toutiao",
        }
    # douyin / wechat-channels / 其他
    # 如果附件是视频文件（.mp4 等），改为 video 字段供 publish.py 识别
    _VIDEO_EXT = ('.mp4', '.mov', '.webm', '.avi')
    if images and images[0].lower().endswith(_VIDEO_EXT):
        return {
            "title":   art["finalTitle"],
            "article": art["finalArticle"],
            "topics":  art.get("topics", []),
            "video":   images[0],
        }
    return {
        "title":   art["finalTitle"],
        "article": art["finalArticle"],
        "topics":  art.get("topics", []),
        "images":  images,
    }


def main(platform: str = None):
    if platform is None:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--platform", required=True,
            choices=["xiaohongshu", "zhihu", "douyin", "douyin-video", "wechat-channels", "toutiao"],
        )
        platform = parser.parse_args().platform

    admin_url   = os.environ.get("BAICLAW_ADMIN_API_URL", "").rstrip("/") or get_admin_api_url()
    admin_token = os.environ.get("BAICLAW_ADMIN_TOKEN",   "").strip('"')  or get_admin_token()
    if not admin_url or not admin_token:
        print(json.dumps({"success": False, "error": "缺少 BAICLAW_ADMIN_API_URL 或 BAICLAW_ADMIN_TOKEN"}), flush=True)
        sys.exit(1)

    import requests

    temp_base = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw" / "publish" / platform
    temp_base.mkdir(parents=True, exist_ok=True)

    # 1. 拉取待发布列表
    # ─────────────────────────────────────────────────────────────────────
    # SHARED ENDPOINT — DO NOT change to a platform-specific path.
    # All platforms use /device/xhs-review/...; routing is via the
    # "platform" query parameter.
    # ─────────────────────────────────────────────────────────────────────
    url = f"{admin_url}/device/xhs-review/pending-publish?platform={platform}"
    print(f"[check_and_publish] 拉取 {platform} 待发布列表: {url}", flush=True)
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.RequestException as e:
        print(json.dumps({"processed": 0, "error": f"拉取失败: {e}"}), flush=True)
        sys.exit(1)

    if result.get("code") != 200:
        print(json.dumps({"processed": 0, "error": result.get("message", "未知错误")}), flush=True)
        return

    articles = [a for a in result.get("data", []) if a.get("platform") == platform]
    if not articles:
        print(json.dumps({"processed": 0, "message": "no pending publish"}, ensure_ascii=False))
        return

    print(f"[check_and_publish] {platform} 待发布文章数: {len(articles)}", flush=True)

    _default_publish_script = _SKILLS_ROOT / platform / "scripts" / "publish.py"
    _VIDEO_EXT = ('.mp4', '.mov', '.webm', '.avi')
    results = []

    # 2. 逐条处理
    for i, art in enumerate(articles):
        art_id = art["id"]
        print(f"[check_and_publish] 处理 [{i+1}/{len(articles)}] id={art_id}", flush=True)

        work_dir = temp_base / art_id
        work_dir.mkdir(parents=True, exist_ok=True)

        images = []
        for img in art.get("images", []):
            p = work_dir / img["fileName"]
            try:
                p.write_bytes(base64.b64decode(img["data"]))
                images.append(str(p))
            except Exception as e:
                print(f"[warn] 图片还原失败 {img.get('fileName', '?')}: {e}", file=sys.stderr)

        # 附件是视频文件时，切换到 douyin-video 发布脚本
        is_video = bool(images and images[0].lower().endswith(_VIDEO_EXT))
        if is_video and platform == "douyin":
            publish_script = _SKILLS_ROOT / "douyin-video" / "scripts" / "publish.py"
            print(f"[check_and_publish] 检测到视频附件，切换到 douyin-video/publish.py", flush=True)
        else:
            publish_script = _default_publish_script

        tmp_draft = work_dir / "draft.json"
        tmp_draft.write_text(
            json.dumps(_build_draft(platform, art, images), ensure_ascii=False),
            encoding="utf-8",
        )

        cmd = [sys.executable, str(publish_script), "--draft-json", str(tmp_draft), "--headless", "false"]
        if art.get("accountId"):
            cmd.extend(["--account-id", art["accountId"]])

        print(f"[check_and_publish] 执行 publish.py: {' '.join(cmd)}", flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(work_dir))

        pub_result = {"success": False, "error": "unknown"}
        try:
            last_line = [ln for ln in proc.stdout.strip().split("\n") if ln.strip()][-1]
            pub_result = json.loads(last_line)
        except Exception:
            pub_result = {
                "success": False,
                "error": proc.stderr.strip() or proc.stdout.strip() or "publish.py 无输出",
            }

        print(f"[check_and_publish] 结果: {json.dumps(pub_result, ensure_ascii=False)}", flush=True)

        try:
            # SHARED ENDPOINT — see note above about /device/xhs-review/
            requests.post(
                f"{admin_url}/device/xhs-review/publish-result/{art_id}",
                json={
                    "success":      pub_result.get("success", False),
                    "publishedUrl": pub_result.get("publishedUrl", ""),
                    "error":        pub_result.get("error", ""),
                },
                headers={"Authorization": f"Bearer {admin_token}"},
                timeout=10,
            ).raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[error] 回写失败 id={art_id}: {e}", file=sys.stderr)

        results.append({"id": art_id, **pub_result})

        if pub_result.get("cookieExpired"):
            print("[check_and_publish] Cookie 已失效，终止后续发布", file=sys.stderr)
            break

        if i < len(articles) - 1:
            delay = 30 + random.randint(0, 30)
            print(f"[check_and_publish] 等待 {delay} 秒...", flush=True)
            time.sleep(delay)

    # 3. 清理临时文件
    for art in articles:
        shutil.rmtree(temp_base / art["id"], ignore_errors=True)

    print(json.dumps({
        "processed": len(results),
        "success":   sum(1 for r in results if r.get("success")),
        "failed":    sum(1 for r in results if not r.get("success")),
        "details":   results,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
