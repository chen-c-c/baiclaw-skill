#!/usr/bin/env python3
# @author FondaWu
"""
写入 API Key 到 %APPDATA%\\BaiClaw\\api_keys.json，供所有 SKILL 脚本读取。

用法：
  python setup_api_keys.py --dashscope sk-xxx
  python setup_api_keys.py --list
"""
import argparse
import json
import os
import sys
from pathlib import Path


def get_keys_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "BaiClaw" / "api_keys.json"


def load() -> dict:
    p = get_keys_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save(keys: dict):
    p = get_keys_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存: {p}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="管理 BaiClaw SKILL API Keys")
    parser.add_argument("--dashscope", metavar="KEY", help="阿里云百炼（万相/通义）API Key")
    parser.add_argument("--list", action="store_true", help="列出当前所有已配置 key（脱敏显示）")
    args = parser.parse_args()

    keys = load()

    if args.list:
        if not keys:
            print("暂无已配置的 API Key")
        for k, v in keys.items():
            masked = v[:8] + "***" + v[-4:] if len(v) > 12 else "***"
            print(f"  {k} = {masked}")
        return

    if args.dashscope:
        keys["DASHSCOPE_API_KEY"] = args.dashscope.strip()
        save(keys)
        masked = args.dashscope[:8] + "***" + args.dashscope[-4:]
        print(f"DASHSCOPE_API_KEY 已设置: {masked}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
