# NOTE: This is a copy from SKILLs/xiaohongshu/scripts/enterprise_db.py. Keep in sync.
#!/usr/bin/env python3
# @author FondaWu
import json
import os
import sqlite3
import sys


def _get_db_path() -> str | None:
    # APPDATA 由 BaiClaw buildSkillEnv() 继承自 process.env
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    db_path = os.path.join(appdata, "BaiClaw", "baiclaw.sqlite")
    return db_path if os.path.exists(db_path) else None


def get_enterprise_data() -> dict | None:
    """读取 SQLite 中的企业信息，返回 {brandProfiles, publishAccounts}"""
    db_path = _get_db_path()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        # 从 meta 获取 currentEnterpriseId
        meta_row = conn.execute(
            "SELECT value FROM kv WHERE key = ?",
            ["enterprise_agent_config_cache_meta"],
        ).fetchone()
        if not meta_row:
            conn.close()
            return None
        meta = json.loads(meta_row[0])
        enterprise_id = meta.get("currentEnterpriseId")
        if not enterprise_id:
            conn.close()
            return None

        # 读取品牌信息
        brand_row = conn.execute(
            "SELECT value FROM kv WHERE key = ?",
            ["enterprise_agent_config_cache_brand"],
        ).fetchone()
        brand_profiles = []
        if brand_row:
            brand_profiles = json.loads(brand_row[0]).get(enterprise_id, [])

        # 读取发布账号信息（含 cookiePlain）
        account_row = conn.execute(
            "SELECT value FROM kv WHERE key = ?",
            ["enterprise_agent_config_cache_account"],
        ).fetchone()
        publish_accounts = []
        if account_row:
            publish_accounts = json.loads(account_row[0]).get(enterprise_id, [])

        conn.close()
        return {
            "brandProfiles": brand_profiles,
            "publishAccounts": publish_accounts,
        }
    except Exception as e:
        print(f"[warn] 读取企业信息失败: {e}", file=sys.stderr)
        return None


def get_first_brand(data: dict) -> dict:
    """返回第一个品牌的原始 BrandProfile dict"""
    profiles = data.get("brandProfiles") or []
    return profiles[0] if profiles else {}


def get_xhs_account(data: dict) -> dict | None:
    """返回第一个小红书发布账号的完整对象（含 cookiePlain）"""
    accounts = data.get("publishAccounts") or []
    for acc in accounts:
        if acc.get("platform") == "xiaohongshu":
            return acc
    return None


def get_xhs_account_id(data: dict) -> str | None:
    """返回第一个小红书发布账号 ID"""
    acc = get_xhs_account(data)
    return acc.get("id") if acc else None


def _get_db_write_path() -> str | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    db_path = os.path.join(appdata, "BaiClaw", "baiclaw.sqlite")
    return db_path if os.path.exists(db_path) else None


def _last_ts_key(phone: str) -> str:
    return f"xhs_reply_last_ts:{phone}"


def get_last_reply_ts(phone: str) -> str | None:
    """读取上次回复的最后一条消息的时间戳 (ISO format)."""
    db_path = _get_db_write_path()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?",
            [_last_ts_key(phone)],
        ).fetchone()
        conn.close()
        if row:
            return row[0].strip()
        return None
    except Exception:
        return None


def set_last_reply_ts(ts: str, phone: str):
    """记录本次回复的最后一条消息的时间戳 (ISO format)."""
    db_path = _get_db_write_path()
    if not db_path:
        return
    try:
        import time
        now_int = int(time.time() * 1000)
        key = _last_ts_key(phone)
        conn = sqlite3.connect(db_path)
        existing = conn.execute(
            "SELECT 1 FROM kv WHERE key = ?", [key]
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE kv SET value = ?, updated_at = ? WHERE key = ?",
                [ts, now_int, key],
            )
        else:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                [key, ts, now_int],
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[warn] 写入最后回复时间失败: {e}", file=sys.stderr)


def get_xhs_account_name(data: dict) -> str | None:
    """返回第一个小红书发布账号名（手机号，来自 accountName 字段）"""
    acc = get_xhs_account(data)
    return acc.get("accountName") if acc else None


def get_enabled_replies(data: dict) -> list[str]:
    """返回当前小红书账号已启用的自定义回复语列表。
    从 publishAccounts[0].replies 中筛选 enable='1' 的 replyMsg。"""
    acc = get_xhs_account(data)
    if not acc:
        return []
    replies = acc.get("replies") or []
    return [r["replyMsg"] for r in replies if r.get("enable") == "1" and r.get("replyMsg")]


def get_xhs_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取小红书账号的明文 Cookie"""
    acc = get_xhs_account(data)
    return acc.get("cookiePlain") if acc else None


def get_cookie_from_admin_api(account_id: str) -> str | None:
    """调 agent-backend 解密接口，返回小红书 Cookie 明文字符串"""
    api_url = os.environ.get("BAICLAW_ADMIN_API_URL", "").rstrip("/")
    token = os.environ.get("BAICLAW_ADMIN_TOKEN", "")
    if not api_url or not token:
        return None
    try:
        import requests
        resp = requests.get(
            f"{api_url}/api/device/publish-accounts/{account_id}/cookie",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 401:
            print("[warn] BAICLAW_ADMIN_TOKEN 已过期或无效", file=sys.stderr)
            return None
        resp.raise_for_status()
        return resp.json().get("data", {}).get("cookie")
    except Exception as e:
        print(f"[warn] 从管理后台获取 Cookie 失败: {e}", file=sys.stderr)
        return None
