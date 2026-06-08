#!/usr/bin/env python3
# @author FondaWu
"""
cover_generator.py — 调用千问（wanx-poster-generation-v1）生成海报背景图

使用阿里云海报生成接口，返回 bg_urls（不含文字的纯背景），
供 render_carousel.py 叠加 Playwright 渲染的品牌文字。

依赖：requests
环境变量：QWEN_IMAGE_API_KEY
"""
import os
import tempfile
import time
from pathlib import Path

import requests

TASK_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
QUERY_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
DEFAULT_LORA = "浓郁色彩"  # 商业品牌内容适用；valid: 中国刺绣/2D插画1/浅蓝抽象/深蓝抽象/童话油画/剪纸工艺/浓郁色彩

_TEMP_BASE = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def _create_task(api_key: str, title: str, sub_title: str, prompt_text_zh: str,
                 lora_name: str) -> str:
    """提交海报生成任务，返回 task_id。"""
    input_data: dict = {
        "title": title[:30],
        "prompt_text_zh": (prompt_text_zh[:50] if prompt_text_zh else title[:50]),
        "wh_ratios": "竖版",
        "generate_mode": "generate",
        "generate_num": 1,
    }
    if sub_title:
        input_data["sub_title"] = sub_title[:30]
    if lora_name:
        input_data["lora_name"] = lora_name
        input_data["lora_weight"] = 0.8
        input_data["ctrl_ratio"] = 0.7
        input_data["ctrl_step"] = 0.7

    payload = {
        "model": "wanx-poster-generation-v1",
        "input": input_data,
        "parameters": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
    }
    resp = requests.post(TASK_URL, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"海报API提交失败 ({resp.status_code}): {resp.text}")
    data = resp.json()
    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"海报API未返回task_id: {data}")
    return task_id


def _poll_task(api_key: str, task_id: str, timeout: int = 120) -> dict:
    """轮询任务结果，返回 output 字段；超时或失败抛异常。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            QUERY_URL.format(task_id=task_id),
            headers=headers,
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"查询任务失败 ({resp.status_code}): {resp.text}")
        output = resp.json().get("output", {})
        status = output.get("task_status", "")
        if status == "SUCCEEDED":
            return output
        if status == "FAILED":
            raise RuntimeError(
                f"海报生成失败: {output.get('code')} - {output.get('message')}"
            )
        time.sleep(4)
    raise RuntimeError(f"海报生成超时（{timeout}s）, task_id={task_id}")


def generate_cover(
    prompt: str,
    output_path: str,
    title: str = "",
    sub_title: str = "",
    lora_name: str = DEFAULT_LORA,
    timeout: int = 120,
    max_retries: int = 2,
) -> str | None:
    """生成海报背景图（bg_urls，不含文字），保存到 output_path，返回本地路径；失败返回 None。"""
    api_key = os.environ.get("QWEN_IMAGE_API_KEY", "")
    if not api_key:
        return None

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"[cover] 重试第 {attempt} 次...", flush=True)
                time.sleep(5)
            print(f"[cover] 提交海报任务，风格={lora_name}, 标题={title[:20]}", flush=True)
            task_id = _create_task(
                api_key=api_key,
                title=title or prompt[:30],
                sub_title=sub_title,
                prompt_text_zh=prompt[:50],
                lora_name=lora_name,
            )
            print(f"[cover] task_id={task_id}，等待结果...", flush=True)
            output = _poll_task(api_key, task_id, timeout=timeout)

            url_list = output.get("bg_urls") or output.get("render_urls", [])
            if not url_list:
                raise RuntimeError("海报API返回中未找到图片URL")

            print(f"[cover] 下载背景图...", flush=True)
            img_resp = requests.get(url_list[0], timeout=60)
            if not img_resp.ok:
                raise RuntimeError(f"下载图片失败 ({img_resp.status_code})")

            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_resp.content)

            size_kb = len(img_resp.content) / 1024
            print(f"[cover] 封面已保存: {output_path} ({size_kb:.0f} KB)", flush=True)
            return output_path

        except RuntimeError as e:
            last_err = e
            msg = str(e)
            # InvalidParameter 说明参数有误，不重试
            if "InvalidParameter" in msg or "lora_name" in msg:
                raise
            print(f"[cover] 第 {attempt} 次失败（{msg}），{'重试' if attempt < max_retries else '放弃'}...", flush=True)

    raise last_err


def main():
    import argparse
    parser = argparse.ArgumentParser(description="用千问海报生成 API 生成封面背景图")
    parser.add_argument("--title", required=True)
    parser.add_argument("--sub-title", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--lora", default=DEFAULT_LORA)
    parser.add_argument("--output", "-o", default=str(_TEMP_BASE / "cover.png"))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    path = generate_cover(
        prompt=args.prompt or args.title,
        output_path=args.output,
        title=args.title,
        sub_title=args.sub_title,
        lora_name=args.lora,
        timeout=args.timeout,
    )
    print(path)


if __name__ == "__main__":
    main()
