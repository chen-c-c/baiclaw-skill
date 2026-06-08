#!/usr/bin/env python3
"""Shared utilities for xiaohongshu-reply skill.

Path management, replied.json, human-like typing, LLM calling.
"""
import hashlib
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from playwright.sync_api import Page

# ── Constants ──────────────────────────────────────────────────────────────────

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
"""

TZ_CN = timezone(timedelta(hours=8))

CONFIDENCE_THRESHOLD = 0.7

FIXED_REPLIES = [
    "感谢您的支持，如有意向，请加我们的企业微信：baiClawAI",
    "谢谢您的关注，请加我们的企业微信了解更多：baiClawAI",
    "谢谢支持，加企业微信：baiClawAI 获取更多产品信息",
]


def is_spam_prefilter(text: str) -> bool:
    """Pre-filter spam content before LLM classification.
    Checks: pure numbers, pure emoji/symbols, less than 3 meaningful chars."""
    text = text.strip()
    if not text:
        return True
    # 纯数字
    if re.match(r'^\d+$', text):
        return True
    # 纯表情: no Chinese chars, letters, or digits → symbols/emoji only
    cleaned = re.sub(r'\s+', '', text)
    if cleaned and not re.search(r'[一-鿿㐀-䶿a-zA-Z0-9]', cleaned):
        return True
    # 字数小于三个字 (meaningful chars: Chinese + letters)
    meaningful = re.findall(r'[一-鿿㐀-䶿a-zA-Z]', text)
    if len(meaningful) < 3:
        return True
    return False


def extract_comment_body(text: str) -> str:
    """Extract the actual comment content from XHS notification text.

    Two formats observed:
    1. Colon: '昵称 评论了你的笔记: 评论内容'
    2. Newline:
       昵称
       评论了你的笔记
       评论内容
       回复
    """
    # Format 1: colon-separated
    patterns = [
        r'评论了你的笔记[：:]\s*',
        r'评论了你的\S*\s*[：:]\s*',
        r'评论[：:]\s*',
        r'回复了你[：:]\s*',
        r'回复\s*[：:]\s*',
        r'@了你[：:]\s*',
        r'提到了你[：:]\s*',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            body = text[m.end():].strip()
            if body:
                return body

    # Format 2: newline-separated — body is the line after action line,
    # before UI junk like '回复' button text.
    # If action line found but no body, the comment is emoji/image-only
    # (innerText doesn't capture emoji). Return empty to trigger spam detection.
    action_markers = ['评论了你的笔记', '评论', '回复了你', '回复', '@了你', '提到了你']
    ui_junk = {'回复', 'Reply', '举报', '删除', '关注', '赞', '收藏', '分享'}
    lines = text.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        for marker in action_markers:
            if stripped == marker or stripped.startswith(marker + ' '):
                for j in range(i + 1, len(lines)):
                    candidate = lines[j].strip()
                    if candidate and candidate not in ui_junk:
                        return candidate
                # Action line found but no body → emoji/image-only comment
                return ''

    # Fallback: last colon-separated segment
    for sep in [': ', '：']:
        idx = text.rfind(sep)
        if idx > 0:
            body = text[idx + len(sep):].strip()
            if body and len(body) < len(text) * 0.8:
                return body
    return text

# ── Path helpers ───────────────────────────────────────────────────────────────

def get_reply_base() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "BaiClaw" / "baiclaw" / "xhs-reply"
    else:
        base = Path(os.environ.get("BAICLAW_TEMP_DIR", tempfile.gettempdir())) / "baiclaw" / "xhs-reply"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_replied_path() -> Path:
    return get_reply_base() / "replied.json"


def create_run_dir() -> Path:
    run_id = datetime.now(TZ_CN).strftime("%Y%m%d-%H%M%S")
    run_dir = get_reply_base() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ── replied.json ───────────────────────────────────────────────────────────────

# Per-ID TTL: IDs older than this many days are pruned on load
_ID_TTL_DAYS = 7


def load_replied_ids() -> tuple[set, set]:
    """Return (replied_comment_ids, replied_dm_conversation_ids).

    Supports two formats:
      - Old (list):  {"comment_ids": ["id1", "id2"], ...}
      - New (dict):  {"comment_ids": {"id1": "2026-...", "id2": "2026-..."}, ...}
    In the new format, IDs older than _ID_TTL_DAYS are pruned automatically.
    """
    path = get_replied_path()
    if not path.exists():
        return set(), set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return set(), set()

    now = datetime.now(TZ_CN)
    cutoff = now - timedelta(days=_ID_TTL_DAYS)
    modified = False

    def _load_and_prune(key: str) -> set:
        nonlocal modified
        raw = data.get(key, [])
        if isinstance(raw, dict):
            # New format: {id: added_at_iso}
            surviving = set()
            for id_, ts in list(raw.items()):
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt >= cutoff:
                        surviving.add(id_)
                    else:
                        modified = True
                except (ValueError, TypeError):
                    surviving.add(id_)
            return surviving
        # Old format: list — keep all, migrate to new format on next write
        return set(raw)

    comments = _load_and_prune("comment_ids")
    dms = _load_and_prune("dm_conversation_ids")

    # If IDs were pruned, write back the cleaned file
    if modified:
        try:
            data["comment_ids"] = {id_: ts for id_, ts in (data.get("comment_ids", {}) if isinstance(data.get("comment_ids"), dict) else {}).items() if id_ in comments}
            data["dm_conversation_ids"] = {id_: ts for id_, ts in (data.get("dm_conversation_ids", {}) if isinstance(data.get("dm_conversation_ids"), dict) else {}).items() if id_ in dms}
            data["updated_at"] = now.isoformat()
            # Atomic write for the cleanup
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix="replied_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except Exception:
            pass

    return comments, dms


def append_replied_id(type_: str, id_: str):
    """Append a comment_id or dm_conversation_id to replied.json atomically.

    Stores IDs as {id: added_at_iso} dict for per-ID TTL tracking.
    Migrates old list-format data to the new dict format on first write.
    """
    path = get_replied_path()
    now = datetime.now(TZ_CN).isoformat()
    key = "comment_ids" if type_ == "comment" else "dm_conversation_ids"
    try:
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                data = {}

        # Read existing IDs, migrating old list format to dict
        existing = data.get(key, [])
        if isinstance(existing, dict):
            ids_map = dict(existing)
        else:
            # Migrate from old list format — treat all as current
            ids_map = {id_: now for id_ in existing if isinstance(id_, str)}

        ids_map[id_] = now
        data[key] = ids_map
        data["updated_at"] = now

        # Migrate the other key too if it's still a list
        other_key = "dm_conversation_ids" if key == "comment_ids" else "comment_ids"
        other_val = data.get(other_key, [])
        if isinstance(other_val, list) and other_val:
            data[other_key] = {id_: now for id_ in other_val if isinstance(id_, str)}

        # Atomic write: temp file in same directory, then rename
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix="replied_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"[error] 写入 replied.json 失败: {e}", file=sys.stderr)


# ── Human-like interaction ─────────────────────────────────────────────────────

def human_delay(page: Page, min_ms: int = 800, max_ms: int = 2000):
    page.wait_for_timeout(min_ms + random.randint(0, max_ms - min_ms))


def type_human(page: Page, text: str, char_min: int = 40, char_max: int = 120):
    if not text:
        return
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(char_min + random.randint(0, char_max - char_min))


def deterministic_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


_TIMESTAMP_PAT = re.compile(
    r'(刚刚|\d+\s*分钟前|\d+\s*小时前|\d+\s*天前|\d+\s*周前|\d+\s*月前'
    r'|今天\s*\d{2}:\d{2}|昨天\s*\d{2}:\d{2}'
    r'|\d{1,2}月\d{1,2}日\s*\d{2}:\d{2}'
    r'|\d{4}[-/]\d{2}[-/]\d{2}\s*\d{2}:\d{2})'
)


def strip_relative_time(text: str) -> str:
    """Remove relative timestamps so deterministic_id is stable across time."""
    return _TIMESTAMP_PAT.sub('', text).strip()


# ── LLM ────────────────────────────────────────────────────────────────────────

from anthropic import Anthropic

def _default_model() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    if os.environ.get("LOBSTER_APIKEY_DEEPSEEK"):
        return "deepseek-chat"
    return os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def call_llm(system_prompt: str, user_prompt: str,
             model: str = None, max_tokens: int = 1024) -> str:
    """Unified LLM call: Claude or DeepSeek. Returns text content."""
    model = model or _default_model()
    deepseek_key = os.environ.get("LOBSTER_APIKEY_DEEPSEEK")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if deepseek_key and not anthropic_key:
        from openai import OpenAI
        client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
        actual_model = model if model.startswith("deepseek") else "deepseek-chat"
        print(f"[llm] 调用 DeepSeek ({actual_model})...", flush=True)
        resp = client.chat.completions.create(
            model=actual_model, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    else:
        print(f"[llm] 调用 Claude ({model})...", flush=True)
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            thinking={"type": "disabled"},
        )
        text = ""
        thinking_text = ""
        for block in resp.content:
            block_type = getattr(block, "type", None) or type(block).__name__
            if block_type == "thinking":
                thinking_text = getattr(block, "thinking", "") or ""
                continue
            block_text = getattr(block, "text", None)
            if block_text:
                text = block_text.strip()
                break
        if not text and thinking_text:
            # Some models return thinking-only; try to extract from thinking
            import re as _re
            m = _re.search(r'(?:reply|回复|回答)[：:]\s*(.+?)(?:\n|$)', thinking_text, _re.I)
            if m:
                text = m.group(1).strip()
        if not text:
            raise RuntimeError(f"LLM 返回中未找到文本块: {str(resp.content)[:200]}")
        return text


def extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code fences."""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


# ── JSON I/O ───────────────────────────────────────────────────────────────────

def read_json(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return json.loads(raw.decode("utf-8"))


def write_json(data, path: Path):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
