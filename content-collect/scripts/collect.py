#!/usr/bin/env python3
# @author FondaWu / OpenClaw Agent
"""
content-collect: 收集今日热点素材

搜集策略（按优先级）：
  1. 36氪快讯 — 中文科技商业新闻源，返回结构化内容
  2. 爱范儿 — 消费科技/数字产品头版
  3. DuckDuckGo 搜索 — 兜底

输出 /tmp/topics-{taskRunId}.json，直接供 xiaohongshu/generate.py 使用。
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

# ── HTTP 客户端 ──────────────────────────────────────────────────────────────

SESSION = None


def _session():
    global SESSION
    if SESSION is None:
        SESSION = requests.Session()
        SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
    return SESSION


def _clean_html(raw: str) -> str:
    """Remove HTML tags, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


# ── Source: 36氪快讯 ─────────────────────────────────────────────────────────

SOURCE_36KR = "36氪快讯"


def fetch_36kr(max_items: int = 10) -> list[dict]:
    """Scrape 36kr newsflashes. Returns list of {title, summary, url, source}."""
    items = []
    url = "https://www.36kr.com/newsflashes"
    try:
        resp = _session().get(url, timeout=30)
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        print(f"[warn] 36kr fetch failed: {e}", file=sys.stderr)
        return items

    # Try to extract news items from the HTML
    # Pattern 1: look for links containing newsflashes IDs
    for m in re.finditer(
        r'href="(/newsflashes/\d+)"[^>]*>([^<]+)</a>',
        html,
    ):
        link = m.group(1)
        title = _clean_html(m.group(2))
        if not title or len(title) < 6:
            continue
        full_url = f"https://www.36kr.com{link}" if not link.startswith("http") else link
        items.append({
            "title": title,
            "summary": "",
            "url": full_url,
            "source": SOURCE_36KR,
        })
        if len(items) >= max_items:
            break

    # If the above didn't work, try extracting from JSON data embedded in the page
    if not items:
        for m in re.finditer(
            r'"template_content"\s*:\s*"([^"]+)"',
            html,
        ):
            title = _clean_html(m.group(1))
            if title and len(title) > 8:
                items.append({
                    "title": title[:120],
                    "summary": "",
                    "url": url,
                    "source": SOURCE_36KR,
                })
                if len(items) >= max_items:
                    break

    # If still empty, try from readable text blocks
    if not items:
        for m in re.finditer(r"36氪获悉，([^。]+[。])", html):
            text = _clean_html(m.group(1))
            items.append({
                "title": text[:80],
                "summary": text,
                "url": url,
                "source": SOURCE_36KR,
            })
            if len(items) >= max_items:
                break

    return items


# ── Source: 爱范儿 ────────────────────────────────────────────────────────────

SOURCE_IFANR = "爱范儿"


def fetch_ifanr(max_items: int = 10) -> list[dict]:
    """Scrape ifanr.com homepage for latest articles."""
    items = []
    url = "https://www.ifanr.com/"
    try:
        resp = _session().get(url, timeout=30)
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        print(f"[warn] ifanr fetch failed: {e}", file=sys.stderr)
        return items

    seen = set()

    # Pattern 1: standard article links (ifanr.com/\d+.html)
    for m in re.finditer(
        r'href="(https?://www\.ifanr\.com/\d+\.html?)"[^>]*>([^<]+)</a>',
        html,
    ):
        link = m.group(1)
        title = _clean_html(m.group(2))
        if not title or len(title) < 4:
            continue
        if title in seen or link in seen:
            continue
        seen.add(title)
        seen.add(link)
        items.append({"title": title, "summary": "", "url": link, "source": SOURCE_IFANR})
        if len(items) >= max_items:
            break

    # Pattern 2: titles in h2/h3 tags (often article cards)
    if len(items) < max_items:
        for m in re.finditer(r'<h[23][^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>\s*</h[23]>', html):
            link = m.group(1)
            title = _clean_html(m.group(2))
            if not title or len(title) < 4 or title in seen or link in seen:
                continue
            if not link.startswith("http"):
                link = "https://www.ifanr.com" + link
            seen.add(title)
            seen.add(link)
            items.append({"title": title, "summary": "", "url": link, "source": SOURCE_IFANR})
            if len(items) >= max_items:
                break

    return items


# ── Source: DuckDuckGo (fallback) ─────────────────────────────────────────────

SOURCE_DDG = "DuckDuckGo"


def search_ddg(query: str, max_results: int = 3) -> list[dict]:
    items = []
    try:
        from ddgs import DDGS
        import concurrent.futures
        def _do_search():
            with DDGS(timeout=15) as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        with concurrent.futures.ThreadPoolExecutor() as pool:
            fut = pool.submit(_do_search)
            results = fut.result(timeout=20)
            for r in results:
                items.append({
                    "title": r.get("title", ""),
                    "summary": r.get("body", ""),
                    "url": r.get("href", ""),
                    "source": SOURCE_DDG,
                })
    except concurrent.futures.TimeoutError:
        print(f"[warn] DDG search timed out for '{query}'", file=sys.stderr)
    except Exception as e:
        print(f"[warn] DDG search failed for '{query}': {e}", file=sys.stderr)
    return items


# ── Aggregator ────────────────────────────────────────────────────────────────


def collect(
    brand_name: str,
    industry: str,
    keywords: str,
    target_audience: str,
) -> dict:
    """Main collect function. Returns the topics JSON directly."""

    today = date.today().isoformat()
    raw_all = []

    # 1. 36氪快讯
    print("[info] fetching 36kr newsflashes...", flush=True)
    kr_items = fetch_36kr(12)
    raw_all.append({"source": SOURCE_36KR, "items": kr_items})
    print(f"[info]    got {len(kr_items)} items", flush=True)

    # 2. 爱范儿
    print("[info] fetching ifanr homepage...", flush=True)
    ifr_items = fetch_ifanr(10)
    raw_all.append({"source": SOURCE_IFANR, "items": ifr_items})
    print(f"[info]    got {len(ifr_items)} items", flush=True)

    # 3. DuckDuckGo — targeted queries (timeout-safe)
    queries = [
        f"{industry} 爆款 今日",
        f"{keywords.split(',')[0]} 热点 AI 最新",
        f"小红书 {industry} 热搜",
        f"{target_audience} 工作痛点 效率",
    ]
    for q in queries:
        print(f"[info] searching DDG: {q}", flush=True)
        ddg_items = search_ddg(q, 3)
        raw_all.append({"source": f"{SOURCE_DDG}: {q}", "items": ddg_items})
        print(f"[info]    got {len(ddg_items)} items", flush=True)

    # Flatten all items with a simple relevance score (keyword match)
    all_items = []
    seen = set()
    kw_list = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    for group in raw_all:
        for item in group["items"]:
            key = item["title"][:50]
            if key in seen:
                continue
            seen.add(key)
            text = (item["title"] + " " + item["summary"]).lower()
            score = sum(1 for kw in kw_list if kw in text)
            if item["source"] in (SOURCE_IFANR, SOURCE_36KR) and any(
                kw in text for kw in ["ai", "人工智能", "智能", "效率", "工具", "手机", "芯片", "大模型", "工作", "职场"]
            ):
                score += 2
            all_items.append({**item, "relevance": score})

    # Sort by relevance, take top items
    all_items.sort(key=lambda x: -x["relevance"])
    top_items = all_items[:20] if all_items else []

    return {
        "collected_at": datetime.now().isoformat(),
        "date": today,
        "brand": {"name": brand_name, "industry": industry, "keywords": keywords, "target_audience": target_audience},
        "sources_used": [
            {"source": SOURCE_36KR, "status": "ok" if kr_items else "empty", "count": len(kr_items)},
            {"source": SOURCE_IFANR, "status": "ok" if ifr_items else "empty", "count": len(ifr_items)},
            {"source": SOURCE_DDG, "status": "used"},
        ],
        "topics": top_items,
        "total_raw": len(all_items),
    }


def _write_output(data: dict, output_path: Optional[str]):
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[info] output written: {output_path}", flush=True)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="content-collect: 搜集今日热点")
    parser.add_argument("--brand-name", required=True, help="品牌名称")
    parser.add_argument("--industry", required=True, help="行业")
    parser.add_argument("--keywords", default="", help="关键词（逗号分隔）")
    parser.add_argument("--target-audience", default="年轻用户", help="目标用户")
    parser.add_argument("--output", "-o", default=None, help="输出 topics JSON 路径")
    args = parser.parse_args()

    data = collect(
        brand_name=args.brand_name,
        industry=args.industry,
        keywords=args.keywords,
        target_audience=args.target_audience,
    )
    _write_output(data, args.output)


if __name__ == "__main__":
    main()
