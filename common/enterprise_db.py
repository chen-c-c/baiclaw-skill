#!/usr/bin/env python3
# @author FondaWu
import json
import os
import sqlite3
import sys

# 后端 API 地址由 BaiClaw 主程序通过 BAICLAW_ADMIN_API_URL 环境变量注入，
# SKILL 脚本不自行维护后端路由。本地开发未设置环境变量时默认使用 localhost。
_DEFAULT_API_URL = "http://localhost:8081/api"


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
            # fallback: 取第一个 tenant 的 enterpriseId
            tenants = meta.get("tenants") or {}
            if tenants:
                enterprise_id = next(iter(tenants.keys()))
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


def get_xhs_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取小红书账号的明文 Cookie"""
    acc = get_xhs_account(data)
    return acc.get("cookiePlain") if acc else None


def get_douyin_account(data: dict) -> dict | None:
    """返回第一个抖音发布账号的完整对象（含 cookiePlain）"""
    accounts = data.get("publishAccounts") or []
    for acc in accounts:
        if acc.get("platform") == "douyin":
            return acc
    return None


def get_douyin_account_id(data: dict) -> str | None:
    """返回第一个抖音发布账号 ID"""
    acc = get_douyin_account(data)
    return acc.get("id") if acc else None


def get_douyin_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取抖音账号的明文 Cookie"""
    acc = get_douyin_account(data)
    return acc.get("cookiePlain") if acc else None


def get_wechat_channels_account(data: dict) -> dict | None:
    """返回第一个微信视频号发布账号的完整对象（含 cookiePlain）"""
    accounts = data.get("publishAccounts") or []
    for acc in accounts:
        if acc.get("platform") in ("wechat-channels", "wechat"):
            return acc
    return None


def get_wechat_channels_account_id(data: dict) -> str | None:
    """返回第一个微信视频号发布账号 ID"""
    acc = get_wechat_channels_account(data)
    return acc.get("id") if acc else None


def get_wechat_channels_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取微信视频号账号的明文 Cookie"""
    acc = get_wechat_channels_account(data)
    return acc.get("cookiePlain") if acc else None


def get_admin_token() -> str | None:
    """从 SQLite kv 表读取 deviceToken，降级 auth.saToken → auth.token"""
    db_path = _get_db_path()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        for key in ("deviceToken", "auth.saToken", "auth.token"):
            row = conn.execute(
                "SELECT value FROM kv WHERE key = ?", [key]
            ).fetchone()
            if row and row[0]:
                conn.close()
                return row[0].strip().strip('"')
        conn.close()
        return None
    except Exception as e:
        print(f"[warn] 读取 admin token 失败: {e}", file=sys.stderr)
        return None


def get_admin_api_url() -> str:
    """返回审核接口地址，优先使用环境变量 BAICLAW_ADMIN_API_URL"""
    return os.environ.get("BAICLAW_ADMIN_API_URL", _DEFAULT_API_URL).rstrip("/")


def get_toutiao_account(data: dict) -> dict | None:
    """返回第一个头条号发布账号的完整对象（含 cookiePlain）"""
    accounts = data.get("publishAccounts") or []
    for acc in accounts:
        if acc.get("platform") == "toutiao":
            return acc
    return None


def get_toutiao_account_id(data: dict) -> str | None:
    """返回第一个头条号发布账号 ID"""
    acc = get_toutiao_account(data)
    return acc.get("id") if acc else None


def get_toutiao_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取头条号账号的明文 Cookie"""
    acc = get_toutiao_account(data)
    return acc.get("cookiePlain") if acc else None


def get_zhihu_account(data: dict) -> dict | None:
    """返回第一个知乎发布账号的完整对象（含 cookiePlain）"""
    accounts = data.get("publishAccounts") or []
    for acc in accounts:
        if acc.get("platform") == "zhihu":
            return acc
    return None


def get_zhihu_account_id(data: dict) -> str | None:
    """返回第一个知乎发布账号 ID"""
    acc = get_zhihu_account(data)
    return acc.get("id") if acc else None


def get_zhihu_cookie(data: dict) -> str | None:
    """从 SQLite 缓存中读取知乎账号的明文 Cookie"""
    acc = get_zhihu_account(data)
    return acc.get("cookiePlain") if acc else None


def get_api_keys() -> dict:
    """读取 %APPDATA%\\BaiClaw\\api_keys.json，返回 API key 字典。不存在时返回空字典。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return {}
    path = os.path.join(appdata, "BaiClaw", "api_keys.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 api_keys.json 失败: {e}", file=sys.stderr)
        return {}


def get_dashscope_api_key() -> str | None:
    """获取阿里云百炼（万相/通义）API Key。优先读环境变量，其次读 api_keys.json。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    return get_api_keys().get("DASHSCOPE_API_KEY") or None


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
