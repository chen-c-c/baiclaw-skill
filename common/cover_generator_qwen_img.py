#!/usr/bin/env python3
# @author FondaWu
"""
cover_generator.py — 调用千问（qwen-image-2.0-pro）生成封面背景图

依赖：requests（已在 requirements.txt 中）
环境变量：QWEN_IMAGE_API_KEY
"""
import json
import os
import tempfile
from pathlib import Path

import requests

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_SIZE = "1440*1920"  # 3:4，匹配抖音卡片截图比例（1080×1440）

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def generate_cover(
    prompt: str,
    output_path: str,
    size: str = DEFAULT_SIZE,
    negative_prompt: str = "低分辨率，文字模糊，扭曲，畸形，画面杂乱，AI感重，人物面部",
    timeout: int = 120,
) -> str | None:
    """调用千问 Image API 生成封面背景图，保存到 output_path，返回本地路径；失败返回 None"""
    api_key = os.environ.get("QWEN_IMAGE_API_KEY", "")
    if not api_key:
        return None

    payload = {
        "model": "qwen-image-2.0-pro",
        "input": {
            "messages": [{"role": "user", "content": [{"text": prompt}]}]
        },
        "parameters": {
            "size": size,
            "n": 1,
            "watermark": False,
            "prompt_extend": True,
            "negative_prompt": negative_prompt,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    print(f"[cover] 正在调用千问生图...", flush=True)
    print(f"[cover] 提示词: {prompt[:120]}...", flush=True)

    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"千问API请求失败 ({resp.status_code}): {resp.text}")

    data = resp.json()
    choices = data.get("output", {}).get("choices", [])
    if not choices:
        raise RuntimeError(f"千问API返回无结果: {json.dumps(data, ensure_ascii=False)}")

    contents = choices[0].get("message", {}).get("content", [])
    image_url = None
    for c in contents:
        if "image" in c:
            image_url = c["image"]
            break

    if not image_url:
        raise RuntimeError("千问API返回中未找到图片URL")

    print(f"[cover] 正在下载图片...", flush=True)
    img_resp = requests.get(image_url, timeout=60)
    if not img_resp.ok:
        raise RuntimeError(f"下载图片失败 ({img_resp.status_code})")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_resp.content)

    size_kb = len(img_resp.content) / 1024
    print(f"[cover] 封面已保存: {output_path} ({size_kb:.0f} KB)", flush=True)
    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="用千问 AI 生成封面背景图")
    parser.add_argument("--prompt", required=True, help="图片描述提示词")
    parser.add_argument("--output", "-o", default=str(_TEMP_BASE / "cover.png"), help="输出路径")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="图片尺寸 (宽*高)")
    parser.add_argument("--timeout", type=int, default=120, help="API超时秒数")
    args = parser.parse_args()

    path = generate_cover(
        prompt=args.prompt,
        output_path=args.output,
        size=args.size,
        timeout=args.timeout,
    )
    print(path)


if __name__ == "__main__":
    main()
