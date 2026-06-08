#!/usr/bin/env python3
"""Scrape Toutiao (今日头条) news articles by keyword via mobile JSON API.

Uses m.toutiao.com JSON APIs directly (no headless browser needed),
with a real Cookie string to bypass anti-bot detection.

Search API → extract article metadata → content info API → save .md files.
"""
import argparse
import html as _html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse as _urlparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "common"))
from image_placeholder import (
    download_image,
    batch_upload_images,
    upload_image,
    extract_images_from_html,
    replace_images_with_placeholders,
)

# ── Constants ──────────────────────────────────────────────────────────────────────

TZ_CN = timezone(timedelta(hours=8))

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

TOUTIAO_COOKIE = (
    "passport_csrf_token=8300f6d8a62dc1f2d8de6f2112d1c7ba; "
    "passport_csrf_token_default=8300f6d8a62dc1f2d8de6f2112d1c7ba; "
    "tt_webid=7638882599284557375; "
    "_ga=GA1.1.1823711851.1779090518; "
    "ttwid=1%7Cq3tL_fPkvUVBoRGgnObDij5Zz6cmCKAZ_rvwpddvJCs%7C1779172469%7C9f525b86db4285182a91a43742aadc43a61896e8705ac1b33f9bd4f2b515dcf2; "
    "n_mh=EQRIsYoJESnIGT-ug70zOhpji2p07Ocgc0wH2BtAO8I; "
    "sso_uid_tt=5c3e7b485a17492fad21910c8b63498f; "
    "sso_uid_tt_ss=5c3e7b485a17492fad21910c8b63498f; "
    "toutiao_sso_user=80961be6f067e82e22c41936c070304c; "
    "toutiao_sso_user_ss=80961be6f067e82e22c41936c070304c; "
    "sid_guard=0225a7604e069baca887d6ad04b7a849%7C1779173097%7C5184001%7CSat%2C+18-Jul-2026+06%3A44%3A58+GMT; "
    "uid_tt=063bd5d21459393fac59bc6bdce0e378; "
    "uid_tt_ss=063bd5d21459393fac59bc6bdce0e378; "
    "sid_tt=0225a7604e069baca887d6ad04b7a849; "
    "sessionid=0225a7604e069baca887d6ad04b7a849; "
    "sessionid_ss=0225a7604e069baca887d6ad04b7a849; "
    "is_staff_user=false; "
    "has_biz_token=false; "
    "odin_tt=f102a116886bf2b937a1740cc574dd5edc8dc00bf4b5e7fc2b74bcdecbcb81803f5d558091bb2c47a92e811e313f11e8"
)


# ── DB / config ───────────────────────────────────────────────────────────────────

def _get_db_path() -> str | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    db_path = os.path.join(appdata, "BaiClaw", "baiclaw.sqlite")
    return db_path if os.path.exists(db_path) else None


def _read_keywords_from_db() -> list[str]:
    raw = _get_kv_value("enterprise_agent_config_cache_rewrite")
    if raw:
        try:
            records = json.loads(raw)
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict) and record.get("mediaType") == "toutiao":
                        kw = record.get("keywords") or ""
                        keywords = [k.strip() for k in re.split(r'[,，]', kw) if k.strip()]
                        if keywords:
                            return keywords
        except Exception as e:
            print(f"[warn] 解析 enterprise_agent_config_cache_rewrite 失败: {e}", flush=True)

    # Fallback: legacy enterprise_agent_config_cache_queryKey
    db_path = _get_db_path()
    if not db_path:
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?",
            ["enterprise_agent_config_cache_queryKey"],
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return []
        keywords = [kw.strip() for kw in re.split(r'[,，]', row[0]) if kw.strip()]
        return keywords
    except Exception as e:
        print(f"[warn] 从数据库读取关键词失败: {e}", flush=True)
        return []


def _get_kv_value(key: str) -> str | None:
    db_path = _get_db_path()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute("SELECT value FROM kv WHERE key = ?", [key]).fetchone()
        conn.close()
        if row and row[0]:
            return row[0].strip().strip('"')
        return None
    except Exception:
        return None


def _get_device_id() -> str:
    return _get_kv_value("deviceId") or ""


def _get_admin_token() -> str | None:
    env_token = os.environ.get("BAICLAW_ADMIN_TOKEN", "").strip().strip('"')
    if env_token:
        return env_token
    for key in ("deviceToken", "auth.saToken", "auth.token"):
        token = _get_kv_value(key)
        if token:
            return token
    return None


def _get_api_base_url() -> str:
    for env_name in ("BAICLAW_ADMIN_API_URL", "JRTT_API_URL"):
        env_url = os.environ.get(env_name, "").strip()
        if env_url:
            return env_url.rstrip("/")
    return "http://localhost:8081/api"


def upload_article(article_data: dict, device_id: str, token: str, base_url: str, images: list[dict] | None = None) -> bool:
    publish_time = article_data.get("publishTime") or ""
    if publish_time == "未知":
        publish_time = ""

    payload: dict = {
        "title": article_data.get("title", ""),
        "content": article_data.get("bodyText", ""),
        "publishDate": publish_time,
        "url": article_data.get("url", ""),
        "publisher": article_data.get("author", ""),
        "articleSource": "toutiao",
        "deviceId": device_id,
    }

    if images:
        # Format: [{"imageId": "img_xxx", "sortOrder": 0, "originalUrl": "https://...", "altText": "..."}]
        payload["images"] = images

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    api_url = f"{base_url.rstrip('/')}/device/article/save"
    print(f"  [upload] POST {api_url} title={payload['title'][:40]}...", flush=True)
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            body = resp.json()
            code = body.get("code")
            if code == 200 or code == 0:
                return True
            else:
                print(f"  [upload] API 返回错误: code={code}, message={body.get('message', resp.text)}", flush=True)
                return False
        elif resp.status_code == 401:
            print(f"  [upload] 认证失败 (401), 请检查 admin token. 响应: {resp.text[:500]}", flush=True)
            return False
        else:
            print(f"  [upload] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [upload] 请求失败: {e}", flush=True)
        return False


# ── Safe filename ─────────────────────────────────────────────────────────────────

def safe_print(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode('utf-8', errors='replace').decode('utf-8', errors='replace'), flush=True)


def safe_filename(title: str) -> str:
    title = re.sub(r'[​‌‍‎‏⁠­﻿]', '', title)
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', title).strip()
    safe = re.sub(r'_+', '_', safe)
    if not safe:
        safe = "untitled"
    return safe[:80] + '.md' if len(safe) > 80 else safe + '.md'


def _safe_dirname(keyword: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', keyword).strip()
    safe = re.sub(r'_+', '_', safe)
    if not safe:
        safe = "untitled"
    return safe[:50]


def save_article(data: dict, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(data["title"])
    filepath = out_dir / filename

    pub_time = data["publishTime"] or "未知"
    author = data["author"] or "未知"
    url = data["url"]

    md = f"""# {data["title"]}

- **发布时间**: {pub_time}
- **作者**: {author}
- **原文链接**: {url}

---

{data["bodyText"]}
"""
    filepath.write_text(md, encoding="utf-8")
    return str(filepath)


# ── Toutiao mobile API ────────────────────────────────────────────────────────────

def _extract_article_id(link: str) -> str:
    """Extract the numeric article ID from a Toutiao URL."""
    m = re.search(r"toutiao\.com/(?:a|group/)(\d+)", link)
    return m.group(1) if m else ""


def _strip_html_tags(s: str) -> str:
    """Remove HTML tags and return plain text."""
    text = re.sub(r"<[^>]+>", " ", s)
    text = _html.unescape(text)
    return " ".join(text.split())


def _search_mobile(keyword: str, offset: int = 0) -> list[dict]:
    """Search Toutiao via the mobile JSON API. Returns list of article metadata."""
    params = {
        "keyword": keyword,
        "pd": "information",
        "source": "search_subtab_switch",
        "from": "information",
        "aid": "1455",
        "offset": offset,
    }
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Referer": f"https://m.toutiao.com/search?keyword={quote(keyword)}",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": TOUTIAO_COOKIE,
    }

    for attempt in range(1, 4):
        try:
            resp = requests.get(
                "https://m.toutiao.com/api/search/content/",
                params=params,
                headers=headers,
                timeout=15,
            )
            print(f"  [debug] 搜索 API: HTTP {resp.status_code}, {len(resp.content)} 字节", flush=True)

            if resp.status_code != 200:
                if attempt < 3:
                    time.sleep(attempt * 2)
                    continue
                raise RuntimeError(f"搜索 API 返回 HTTP {resp.status_code}")

            data = resp.json()
            items = data.get("data", [])
            if not items:
                count = data.get("count", 0)
                if attempt < 3:
                    time.sleep(attempt * 2)
                    continue
                raise RuntimeError(f"未获取到文章（count={count}），Cookie 可能已过期或被反爬拦截")

            articles = []
            for item in items:
                group_id = str(item.get("group_id", ""))
                link = f"https://www.toutiao.com/article/{group_id}/" if group_id else item.get("article_url", "")

                source = item.get("media_name") or item.get("source", "")

                pub_time = item.get("datetime", "")
                if not pub_time:
                    ts_str = item.get("publish_time", "")
                    if ts_str:
                        try:
                            pub_time = datetime.fromtimestamp(int(ts_str)).strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OSError):
                            pass

                articles.append({
                    "title": item.get("title", ""),
                    "link": link,
                    "group_id": group_id,
                    "source": source,
                    "pubTime": pub_time,
                })
            return articles

        except RuntimeError:
            raise
        except Exception as e:
            if attempt < 3:
                time.sleep(attempt * 2)
            else:
                raise RuntimeError(f"搜索失败: {e}") from e

    raise RuntimeError("多次尝试后仍无法获取搜索结果")


def _fetch_article_content(link: str, group_id: str = "") -> str:
    """Fetch article body HTML via the mobile info JSON API.

    Uses group_id (unique article ID from search API) as primary identifier,
    since article_url may point to 3rd-party domains (cctvnews, etc.).

    Returns raw HTML content (images are NOT yet replaced with placeholders).
    """
    article_id = group_id or _extract_article_id(link)
    if not article_id:
        raise ValueError(f"无法提取文章 ID: link={link[:100]}, group_id={group_id}")

    info_url = f"https://m.toutiao.com/i{article_id}/info/"
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json, */*",
        "Referer": "https://m.toutiao.com/",
        "Cookie": TOUTIAO_COOKIE,
    }

    for attempt in range(1, 4):
        try:
            resp = requests.get(info_url, headers=headers, timeout=15)
            print(f"    [debug] Info API (attempt {attempt}): HTTP {resp.status_code}, {len(resp.content)} 字节", flush=True)
            if resp.status_code != 200:
                if attempt < 3:
                    time.sleep(attempt * 2)
                    continue
                raise RuntimeError(f"文章 API 返回 HTTP {resp.status_code}")

            data = resp.json()
            d = data.get("data", {})
            body = d.get("content") or d.get("article_body") or d.get("body", "")
            if not body:
                if attempt < 3:
                    time.sleep(attempt)
                    continue
                raise ValueError("文章正文为空（可能需要登录或 Cookie 已过期）")
            return body

        except (RuntimeError, ValueError):
            raise
        except Exception as e:
            if attempt < 3:
                time.sleep(attempt)
            else:
                raise RuntimeError(f"获取文章失败: {e}") from e

    raise RuntimeError("多次尝试后仍无法获取文章内容")


# ── Single keyword scrape ─────────────────────────────────────────────────────────

def scrape_keyword(keyword: str, out_dir: Path, keep_local: bool = False) -> dict:
    """Scrape Toutiao articles for a single keyword (2 pages) via JSON APIs.

    Returns a summary dict and writes per-keyword summary.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    all_files: list[str] = []
    articles_found = 0
    articles_scraped = 0
    articles_failed = 0
    articles_uploaded = 0

    # Prepare upload credentials
    device_id = _get_device_id()
    token = _get_admin_token()
    base_url = _get_api_base_url()
    print(f"  [info] 上传 API: {base_url}", flush=True)
    if not device_id:
        print(f"  [warn] 缺少 deviceId: SQLite kv 表中无 deviceId 记录，且未设置环境变量", flush=True)
    if not token:
        print(f"  [warn] 缺少 admin token: 环境变量 BAICLAW_ADMIN_TOKEN 未设置，且 SQLite 中无 deviceToken/auth.saToken/auth.token", flush=True)
    can_upload = bool(device_id and token)
    if can_upload:
        print(f"  [info] 上传已启用 (deviceId={device_id[:8]}..., token={token[:8]}...)", flush=True)
    else:
        print(f"  [warn] 上传已禁用 — 文章将仅保存为本地 .md 文件", flush=True)

    # 1. Search for articles via mobile API (2 pages)
    articles: list[dict] = []
    for page in range(2):
        try:
            page_articles = _search_mobile(keyword, offset=page * 30)
            articles.extend(page_articles)
            print(f"  [jrtt] 第{page + 1}页 找到 {len(page_articles)} 篇", flush=True)
            if page == 0:
                time.sleep(1)
        except Exception as e:
            print(f"  [error] 第{page + 1}页搜索失败: {e}", flush=True)

    articles_found = len(articles)
    print(f"  [jrtt] 共找到 {articles_found} 篇", flush=True)

    if not articles:
        summary = {
            "status": "done",
            "keyword": keyword,
            "articles_found": 0,
            "articles_scraped": 0,
            "articles_failed": 0,
            "articles_uploaded": 0,
            "output_dir": str(out_dir),
            "files": [],
        }
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    # 2. Fetch content for each article
    for idx, art in enumerate(articles):
        title_preview = art['title'][:50]
        source_info = f" [{art['source']}]" if art['source'] else ""
        time_info = f" ({art['pubTime']})" if art['pubTime'] else ""
        print(f"  [{idx + 1}/{articles_found}] {title_preview}...{source_info}{time_info}", flush=True)

        try:
            body_html = _fetch_article_content(art["link"], art.get("group_id", ""))

            # ── Image pipeline: extract → download → batch upload → replace → strip HTML ──
            image_meta: list[dict] = []
            url_to_image_id: dict[str, str] = {}

            imgs = extract_images_from_html(body_html)
            if imgs:
                print(f"    [image] 发现 {len(imgs)} 张图片", flush=True)
                images_dir = out_dir / "images"
                images_dir.mkdir(parents=True, exist_ok=True)

                # Download all images
                local_paths: list[str] = []
                img_srcs: list[str] = []
                for img_info in imgs:
                    src = img_info.get("src", "")
                    if not src:
                        continue
                    local = download_image(src, images_dir)
                    if local:
                        local_paths.append(local)
                        img_srcs.append(src)

                # Upload images to backend
                if can_upload and local_paths:
                    results = batch_upload_images(local_paths, device_id, token, base_url)
                    if results:
                        for i, result in enumerate(results):
                            image_id = result.get("imageId", "")
                            if image_id and i < len(img_srcs):
                                url_to_image_id[img_srcs[i]] = image_id
                    else:
                        # Fallback: upload one by one
                        for i, local in enumerate(local_paths):
                            result = upload_image(local, device_id, token, base_url)
                            if result and result.get("imageId") and i < len(img_srcs):
                                url_to_image_id[img_srcs[i]] = result["imageId"]

                    # Clean up local images after upload
                    for local in local_paths:
                        try:
                            Path(local).unlink(missing_ok=True)
                        except Exception:
                            pass
                elif local_paths:
                    print(f"    [image] 无上传能力，跳过 {len(local_paths)} 张图片", flush=True)

                # Replace <img> tags with {{IMG:imageId}} placeholders in HTML
                if url_to_image_id:
                    body_html, pos_info = replace_images_with_placeholders(body_html, url_to_image_id)
                    # Build image_meta in the format expected by the article save API
                    for pos in pos_info:
                        image_meta.append({
                            "imageId": pos.get("imageId", ""),
                            "sortOrder": pos.get("sortOrder", 0),
                            "originalUrl": pos.get("src", ""),
                            "altText": pos.get("alt", ""),
                        })
                    print(f"    [image] 已替换 {len(url_to_image_id)} 张图片为占位符", flush=True)

            # Strip remaining HTML → plain text with {{IMG:*}} placeholders intact
            body_text = _strip_html_tags(body_html)

            data = {
                "title": art["title"],
                "publishTime": art["pubTime"],
                "author": art["source"],
                "url": art["link"],
                "bodyText": body_text,
            }

            fpath = save_article(data, out_dir)
            articles_scraped += 1
            print(f"    -> 已保存: {Path(fpath).name}", flush=True)

            if can_upload:
                if upload_article(data, device_id, token, base_url, images=image_meta if image_meta else None):
                    articles_uploaded += 1
                    if keep_local:
                        all_files.append(str(Path(fpath).name))
                        print(f"    -> 已上传 (保留本地文件)", flush=True)
                    else:
                        try:
                            Path(fpath).unlink(missing_ok=True)
                            print(f"    -> 已上传, 本地文件已删除", flush=True)
                        except Exception as e:
                            print(f"    -> 上传成功但删除本地文件失败: {e}", flush=True)
                            all_files.append(str(Path(fpath).name))
                else:
                    all_files.append(str(Path(fpath).name))
            else:
                all_files.append(str(Path(fpath).name))

        except Exception as e:
            articles_failed += 1
            print(f"    -> 正文获取失败: {e}", flush=True)

        # Brief delay between articles
        if idx < len(articles) - 1:
            time.sleep(0.5)

    summary = {
        "status": "done",
        "keyword": keyword,
        "articles_found": articles_found,
        "articles_scraped": articles_scraped,
        "articles_failed": articles_failed,
        "articles_uploaded": articles_uploaded,
        "output_dir": str(out_dir),
        "files": all_files,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────────

def main():
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="今日头条关键词新闻爬取 (mobile API)")
    parser.add_argument("--keyword", default=None, help="搜索关键字 (可选，不传则从数据库读取)")
    parser.add_argument("--out-dir", default="./jrtt", help="输出根目录 (默认 ./jrtt)")
    parser.add_argument("--keep-local", action="store_true", help="上传后保留本地 .md 文件")
    args = parser.parse_args()

    base_dir = Path(args.out_dir).resolve()

    # Determine keyword list.
    # IMPORTANT: when DB keywords exist, --keyword is IGNORED. DB is the sole source of
    # truth in DB mode. This prevents the agent from injecting spurious keywords like
    # "热点新闻" when the user says "头条热点" etc.
    db_keywords = _read_keywords_from_db()
    cli_keyword = args.keyword.strip() if args.keyword else ""

    if db_keywords:
        if cli_keyword:
            print(f"[jrtt] 忽略 --keyword '{cli_keyword}' (DB 模式只使用数据库配置的关键词)", flush=True)
        keywords = list(db_keywords)
        source = "db"
    elif cli_keyword:
        keywords = [cli_keyword]
        source = "cli"
    else:
        print("[jrtt] 错误: 未指定 --keyword 且数据库中无关键词", flush=True)
        sys.exit(1)

    print(f"[jrtt] 关键词 ({source}): {', '.join(keywords)}, 每关键词 2 页", flush=True)

    per_keyword = []

    for ki, kw in enumerate(keywords):
        if len(keywords) > 1:
            kw_out_dir = base_dir / _safe_dirname(kw)
            print(f"\n[jrtt] [{ki + 1}/{len(keywords)}] 搜索: {kw}", flush=True)
        else:
            kw_out_dir = base_dir if args.keyword else base_dir / _safe_dirname(kw)
            print(f"\n[jrtt] 搜索: {kw}, 输出: {kw_out_dir}", flush=True)

        summary = scrape_keyword(kw, kw_out_dir, keep_local=args.keep_local)
        per_keyword.append(summary)
        print(f"  [jrtt] 完成: {summary['articles_scraped']}/{summary['articles_found']} 篇", flush=True)

        # Brief delay between keywords
        if ki < len(keywords) - 1:
            time.sleep(2)

    # Overall summary
    if len(keywords) > 1 or source == "db":
        total_found = sum(s["articles_found"] for s in per_keyword)
        total_scraped = sum(s["articles_scraped"] for s in per_keyword)
        total_failed = sum(s["articles_failed"] for s in per_keyword)
        total_uploaded = sum(s.get("articles_uploaded", 0) for s in per_keyword)
        overall = {
            "status": "done",
            "mode": "multi",
            "total_keywords": len(keywords),
            "keywords": keywords,
            "source": source,
            "total_articles_found": total_found,
            "total_articles_scraped": total_scraped,
            "total_articles_failed": total_failed,
            "total_articles_uploaded": total_uploaded,
            "output_dir": str(base_dir),
            "per_keyword": per_keyword,
        }
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / "summary.json").write_text(
            json.dumps(overall, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[jrtt] 全部完成: {total_scraped}/{total_found} 篇, {total_failed} 失败, {total_uploaded} 已上传", flush=True)
        print(f"[jrtt] 摘要: {base_dir / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
