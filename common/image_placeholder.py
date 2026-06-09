#!/usr/bin/env python3
"""图片占位符管理工具：{{IMG:image_id}} 占位符插入、段落位置记录、按段落比例映射还原。

用于 jrtt 爬虫图片提取后的占位符管理，以及 AI 改写后按段落比例还原图片位置。
"""
import base64
import json
import math
import re
import time
from pathlib import Path
from typing import Optional

import requests

IMG_PLACEHOLDER_RE = re.compile(r'\{\{IMG:([^}]+)\}\}')


def download_image(url: str, save_dir: Path, timeout: int = 15) -> Optional[str]:
    """下载单张图片到本地，返回本地绝对路径。失败返回 None。"""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
            "Referer": "https://www.toutiao.com/",
        })
        if resp.status_code != 200:
            print(f"    [image] 下载失败 HTTP {resp.status_code}: {url[:80]}", flush=True)
            return None

        content_type = resp.headers.get("Content-Type", "")
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"

        save_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        fname = f"img_{ts}_{hash(url) & 0xffff:04x}{ext}"
        fpath = save_dir / fname
        fpath.write_bytes(resp.content)
        print(f"    [image] 已下载: {fpath.name} ({len(resp.content)} bytes)", flush=True)
        return str(fpath.resolve())
    except Exception as e:
        print(f"    [image] 下载异常: {e}", flush=True)
        return None


def batch_upload_images(local_paths: list[str], device_id: str, token: str, base_url: str) -> list[dict]:
    """批量上传图片到后端 POST /image/upload/batch。

    返回: [{"imageId": "img_xxx", "imageUrl": "https://..."}, ...]
    失败时返回空列表（单张失败不影响其他图片上传逻辑）。
    """
    if not local_paths:
        return []

    images_payload = []
    for path in local_paths:
        p = Path(path)
        if not p.exists():
            continue
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        images_payload.append({"fileName": p.name, "data": data, "originalPath": path})

    if not images_payload:
        return []

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    api_url = f"{base_url.rstrip('/')}/image/upload/batch"

    print(f"  [image] 批量上传 {len(images_payload)} 张图片: POST {api_url}", flush=True)
    try:
        resp = requests.post(api_url, json={"images": images_payload}, headers=headers, timeout=120)
        if resp.status_code == 200:
            body = resp.json()
            code = body.get("code")
            if code == 200 or code == 0:
                results = body.get("data", [])
                print(f"  [image] 批量上传成功: {len(results)} 张", flush=True)
                return results
            else:
                print(f"  [image] 批量上传 API 错误: code={code}, message={body.get('message', resp.text)}", flush=True)
                return []
        else:
            print(f"  [image] 批量上传 HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return []
    except requests.exceptions.RequestException as e:
        print(f"  [image] 批量上传请求失败: {e}", flush=True)
        return []


def upload_image(local_path: str, device_id: str, token: str, base_url: str) -> Optional[dict]:
    """逐张上传图片（兜底方案），返回 {"imageId": "...", "imageUrl": "..."}。失败返回 None。"""
    p = Path(local_path)
    if not p.exists():
        print(f"    [image] 上传失败: 文件不存在 {local_path}", flush=True)
        return None

    data = base64.b64encode(p.read_bytes()).decode("ascii")
    payload = {"fileName": p.name, "data": data}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    api_url = f"{base_url.rstrip('/')}/image/upload"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            body = resp.json()
            code = body.get("code")
            if code == 200 or code == 0:
                result = body.get("data", {})
                print(f"    [image] 已上传: {p.name} -> {result.get('imageId', '?')}", flush=True)
                return result
            else:
                print(f"    [image] 上传 API 错误: code={code}", flush=True)
                return None
        else:
            print(f"    [image] 上传 HTTP {resp.status_code}", flush=True)
            return None
    except requests.exceptions.RequestException as e:
        print(f"    [image] 上传请求失败: {e}", flush=True)
        return None


def extract_images_from_html(html: str) -> list[dict]:
    """从 HTML 中提取所有 <img> 标签的 src 和 alt。

    Returns: [{"src": "https://...", "alt": "..."}, ...]
    """
    imgs = []
    for m in re.finditer(
        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\'][^>]*>', html, re.IGNORECASE
    ):
        full_tag = m.group(0)
        src = m.group(1)
        if src and not src.endswith('.svg'):
            if src.startswith("//"):
                src = "https:" + src

            alt = ""
            alt_m = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', full_tag, re.IGNORECASE)
            if alt_m:
                alt = alt_m.group(1)

            imgs.append({"src": src, "alt": alt})
    return imgs


def replace_images_with_placeholders(html: str, url_to_image_id: dict[str, str]) -> tuple[str, list[dict]]:
    """将 HTML 中的 <img> 标签替换为 {{IMG:imageId}} 占位符。

    url_to_image_id: {原始图片URL: 后端返回的imageId}

    返回: (带占位符的 HTML 文本, 图片段落位置信息列表)
    位置信息: {"imageId": id, "src": url, "alt": alt,
              "paragraphIdx": p, "totalParagraphs": t, "ratio": r}
    """
    if not url_to_image_id:
        return html, []

    # Count paragraphs by block-level tags
    paragraph_splits = list(re.finditer(
        r'(<(?:p|div|h[1-6]|section|article|br\s*/?)[^>]*>)', html, re.IGNORECASE
    ))
    total_paragraphs = len(paragraph_splits) + 1

    # Extract all img info first, then replace one by one
    imgs = extract_images_from_html(html)
    if not imgs:
        return html, []

    placeholder_html = html
    img_positions = []
    sort_order = 0

    for img_info in imgs:
        src = img_info["src"]
        alt = img_info.get("alt", "")

        # Skip images not in our mapping (not uploaded)
        image_id = url_to_image_id.get(src)
        if not image_id:
            continue

        # Find and replace this specific <img> tag
        img_pattern = re.compile(
            r'<img[^>]*src\s*=\s*["\']' + re.escape(src) + r'["\']\s*[^>]*>',
            re.IGNORECASE
        )
        m = img_pattern.search(placeholder_html)
        if not m:
            continue

        img_start = m.start()
        para_idx = sum(1 for s in paragraph_splits if s.start() < img_start)
        ratio = para_idx / max(total_paragraphs, 1)

        placeholder = f"{{{{IMG:{image_id}}}}}"
        placeholder_html = (placeholder_html[:m.start()] + placeholder +
                          placeholder_html[m.end():])

        img_positions.append({
            "imageId": image_id,
            "sortOrder": sort_order,
            "src": src,
            "alt": alt,
            "paragraphIdx": para_idx,
            "totalParagraphs": total_paragraphs,
            "ratio": round(ratio, 4),
        })
        sort_order += 1

    return placeholder_html, img_positions


def remove_placeholders(text: str) -> str:
    """移除文本中所有的 {{IMG:*}} 占位符。"""
    return IMG_PLACEHOLDER_RE.sub('', text)


def extract_placeholder_positions(text: str) -> list[dict]:
    """从文本中提取 {{IMG:*}} 占位符的段落位置。

    Returns: [{"imageId": id, "placeholder": "{{IMG:id}}", "paragraphIdx": p, "totalParagraphs": t, "ratio": r}, ...]
    """
    positions = []
    paragraphs = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
    total = len(paragraphs)

    for i, para in enumerate(paragraphs):
        for m in IMG_PLACEHOLDER_RE.finditer(para):
            positions.append({
                "imageId": m.group(1),
                "placeholder": m.group(0),
                "paragraphIdx": i,
                "totalParagraphs": total,
                "ratio": round(i / max(total, 1), 4),
            })
    return positions


def restore_placeholders_by_ratio(rewritten_text: str, original_positions: list[dict]) -> str:
    """AI 改写后按段落比例映射还原 {{IMG:*}} 占位符。

    算法: new_position = floor(old_paragraphIdx * new_total / old_total)

    original_positions: 从 extract_placeholder_positions 获取的位置信息列表
    rewritten_text: AI 改写后的纯文本（不含占位符）

    返回: 在对应段落位置重新插入 {{IMG:imageId}} 的文本
    """
    if not original_positions:
        return rewritten_text

    paragraphs = [p for p in re.split(r'\n\s*\n', rewritten_text) if p.strip()]
    new_total = len(paragraphs)

    # Get old_total from first position
    old_total = original_positions[0].get("totalParagraphs", 1)

    # Sort by paragraphIdx for consistent ordering
    sorted_positions = sorted(original_positions, key=lambda p: p.get("paragraphIdx", 0))

    # Calculate target paragraphs
    insertions = []  # list of (target_para_idx, imageId, placeholder)
    for pos in sorted_positions:
        old_idx = pos.get("paragraphIdx", 0)
        image_id = pos.get("imageId", "")
        # new_position = floor(old_paragraphIdx * new_total / old_total)
        target_idx = math.floor(old_idx * new_total / old_total) if old_total > 0 else 0
        target_idx = min(target_idx, new_total - 1) if new_total > 0 else 0
        placeholder = f"{{{{IMG:{image_id}}}}}"
        insertions.append((target_idx, image_id, placeholder))

    # Insert from back to front to preserve indices
    insertions.sort(key=lambda x: (-x[0], x[1]))
    for para_idx, _img_id, placeholder in insertions:
        if 0 <= para_idx < len(paragraphs):
            paragraphs[para_idx] = placeholder + "\n\n" + paragraphs[para_idx]
        else:
            paragraphs.append(placeholder)

    return "\n\n".join(paragraphs)


def extract_and_restore(original_text: str, rewritten_text: str) -> str:
    """一站式：从原始文本提取占位符位置，在改写后文本中按比例还原。"""
    if IMG_PLACEHOLDER_RE.search(rewritten_text):
        return rewritten_text

    positions = extract_placeholder_positions(original_text)
    if not positions:
        return rewritten_text

    return restore_placeholders_by_ratio(rewritten_text, positions)
