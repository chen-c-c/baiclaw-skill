#!/usr/bin/env python3
# @author FondaWu
"""
common/llm.py — 统一 LLM 调用入口

自动检测框架注入的 Provider Key，调用 BaiClaw 当前启用的大模型。
优先级：LOBSTER_APIKEY_DEEPSEEK > ANTHROPIC_API_KEY（Claude）

可导入函数：
  call_llm(prompt, max_tokens, system)  -> str   # 返回原始文本
  call_llm_json(prompt, max_tokens)     -> dict  # 返回 JSON 解析结果
"""
import json
import os
import re
from pathlib import Path


def _load_dotenv() -> None:
    """加载项目根目录 .env（不覆盖已有环境变量）。"""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()


def _provider() -> tuple[str, str, str]:
    """检测当前启用的 Provider，返回 (type, api_key, base_url)。
    type: 'openai_compat' | 'claude'

    框架注入规则（来自 openclawConfigSync.ts）：
      LOBSTER_PROVIDER_API_KEY  — 当前激活 provider 的 key（始终设置）
      LOBSTER_PROVIDER_BASE_URL — 当前激活 provider 的 base URL（OpenAI 兼容时设置）
      ANTHROPIC_API_KEY         — Anthropic/Claude 专用 key
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # 当前激活 Provider 的 key（框架始终注入，优先级最高）
    active_key = (os.environ.get("LOBSTER_PROVIDER_API_KEY")
                  or os.environ.get("LOBSTER_APIKEY_DEEPSEEK", ""))  # 向后兼容

    # 如果 active key 就是 anthropic key，走 Claude 分支
    if not active_key or active_key == anthropic_key:
        return "claude", anthropic_key, ""

    # OpenAI 兼容 Provider（DeepSeek / Moonshot / Qwen 等）
    base_url = (os.environ.get("LOBSTER_PROVIDER_BASE_URL")      # 框架注入
                or os.environ.get("BAICLAW_LLM_BASE_URL")        # .env 手动配置
                or "https://api.deepseek.com")                   # 默认兜底
    return "openai_compat", active_key, base_url


def call_llm(prompt: str, max_tokens: int = 1024, system: str = "",
             json_mode: bool = False) -> str:
    """调用框架当前启用的大模型，返回原始文本。

    Args:
        prompt:     用户提示词
        max_tokens: 最大输出 token 数
        system:     系统提示（可选）
        json_mode:  True 时对 DeepSeek 启用 response_format=json_object，确保输出合法 JSON
    """
    provider, api_key, base_url = _provider()
    _effective_base = base_url or os.environ.get("ANTHROPIC_BASE_URL", "(default)")
    print(f"[llm] provider={provider} base_url={_effective_base} key={api_key[:8]}...", flush=True)

    if provider == "openai_compat":
        from openai import OpenAI
        model = (os.environ.get("BAICLAW_LLM_MODEL")
                 or os.environ.get("LOBSTER_PROVIDER_MODEL")
                 or os.environ.get("LOBSTER_MODEL_DEEPSEEK", "deepseek-chat"))
        print(f"[llm] 调用 OpenAI兼容 ({base_url}, {model})...", flush=True)
        client = OpenAI(api_key=api_key, base_url=base_url)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=messages)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content.strip()

    else:
        import anthropic
        model = (os.environ.get("BAICLAW_LLM_MODEL")
                 or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
        print(f"[llm] 调用 Claude ({model})...", flush=True)
        client = anthropic.Anthropic()
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                break
        if not text:
            raise RuntimeError(f"LLM 返回内容中未找到文本块: {resp.content}")
        return text


def _strip_json_comments(text: str) -> str:
    """去除 JSON 字符串外的行内注释（# 和 //）。"""
    result = []
    in_str = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != '\\'):
            in_str = not in_str
            result.append(ch)
        elif not in_str and ch == '#':
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        elif not in_str and ch == '/' and i + 1 < len(text) and text[i + 1] == '/':
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def call_llm_json(prompt: str, max_tokens: int = 1024, system: str = "") -> dict:
    """调用框架当前启用的大模型，自动解析 JSON 响应。

    DeepSeek 使用 json_mode 强制输出合法 JSON；Claude 作为兜底清理注释再解析。
    """
    text = call_llm(prompt, max_tokens=max_tokens, system=system, json_mode=True)

    # 去除可能的 markdown 代码块包裹
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if m:
        text = m.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兜底：去除行内注释后再解析
        cleaned = _strip_json_comments(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 返回内容无法解析为 JSON: {e}\n原始返回:\n{text}") from e
