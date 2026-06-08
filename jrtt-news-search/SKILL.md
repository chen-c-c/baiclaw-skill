---
name: "jrtt-news-search"
description: "今日头条新闻搜索与爬取。当用户说 获取今日头条新闻、看头条、头条热点、搜索头条新闻 时使用此技能。自动从数据库读取关键词，严禁自行添加 --keyword。Use for Toutiao/jrtt news scraping."
---

# 今日头条关键词新闻爬取 (jrtt-news-search)

Search Toutiao for keyword-based news via mobile JSON API, scrape the first article found, save as `.md` file.

## CRITICAL: Always use DB mode (no --keyword)

**NEVER add --keyword under any circumstances.** The keywords are pre-configured in the
database. When the user asks for Toutiao news — no matter how they phrase it
("获取头条新闻", "看下头条", "头条热点", "今日头条热点", "爬取头条新闻", etc.) — run:

```bash
python scripts/scrape_toutiao.py --out-dir "./jrtt"
```

**DO NOT pass --keyword "热点新闻" or any other keyword.** Even if the user mentions
"热点", "热门", or any topic word, do NOT translate it into a keyword. The database
already knows what to search for. Any --keyword you add will be IGNORED when DB
keywords exist, and mislead the user.

The only valid use of --keyword is when the user explicitly says a SPECIFIC search
term AND there are no DB keywords configured, e.g. "搜索头条关于AI的新闻" → `--keyword "AI"`.

Supports two modes:

- **DB mode (default)**: Reads keywords from SQLite `enterprise_agent_config_cache_rewrite` (filtered by `mediaType: "toutiao"`). **Use this unless user specifies a keyword.**
- **CLI mode**: Pass `--keyword` for a specific keyword the user asked for. Only use this when the user explicitly names a topic.

## Prerequisites

- Python 3.9+
- `requests` (`pip install requests`)

## Usage

```bash
# DB mode (DEFAULT — use this when no keyword specified)
python scripts/scrape_toutiao.py --out-dir "./jrtt"

# CLI mode (only when user gives a specific keyword)
python scripts/scrape_toutiao.py --keyword "人工智能" --out-dir "./jrtt"
```

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--keyword` | No | (from DB) | IGNORED when DB has keywords. Only use when DB is empty AND user explicitly names a topic. |
| `--out-dir` | No | `./jrtt` | Output root directory |
| `--keep-local` | No | `false` | Keep local .md files after successful upload |

### Examples

```bash
# Multi-keyword from DB (reads enterprise_agent_config_cache_rewrite, mediaCode=toutiao)
python scripts/scrape_toutiao.py --out-dir "./jrtt"

# Single keyword
python scripts/scrape_toutiao.py --keyword "人工智能" --out-dir "./jrtt"
```

## Execution Flow

1. Determine keywords: CLI arg or SQLite `enterprise_agent_config_cache_rewrite` table
2. For each keyword:
   - Call `https://m.toutiao.com/api/search/content/` (mobile JSON API) with Cookie authentication
   - For each article found, call `https://m.toutiao.com/i{article_id}/info/` to get full body HTML
   - Extract `<img>` tags from HTML content
   - Download images to local `images/` directory
   - Batch upload images to backend via `POST /api/image/upload/batch`
   - Replace `<img>` tags with `{{IMG:<imageId>}}` placeholders using backend-returned imageIds
   - Strip remaining HTML tags from body, save as `./jrtt/<keyword>/<safe_title>.md`
   - Upload article to backend API (with `images` array metadata), delete local file on success
   - Write per-keyword `summary.json`
3. Write overall `summary.json` (DB mode only)
4. Print aggregate stats

## Image Handling

Articles scraped from Toutiao may contain embedded images. The scraper:

1. **Extracts** `<img>` tag `src` and `alt` attributes from the Toutiao content API HTML response
2. **Downloads** each image to `./jrtt/<keyword>/images/`
3. **Batch uploads** images to `POST /api/image/upload/batch` (base64-encoded JSON array)
4. **Replaces** `<img>` tags in HTML with `{{IMG:<imageId>}}` placeholders using backend-returned IDs
5. **Strips** remaining HTML tags to get plain text with placeholders intact
6. **Records** paragraph position for each image (paragraphIdx, totalParagraphs, ratio)
7. **Saves** article via `POST /device/article/save` with `images` field:
   ```json
   { "imageId": "img_xxxx", "sortOrder": 0, "originalUrl": "https://...", "altText": "..." }
   ```

### AI Rewrite Image Placeholder Restoration

When articles are rewritten by AI (via `rewrite_subscriber.py`):
- `{{IMG:*}}` placeholders are **extracted** and **removed** before sending content to the LLM
- After AI returns the rewritten text, placeholders are **restored** by paragraph position mapping:
  `new_position = floor(old_paragraphIdx * new_total_paragraphs / old_total_paragraphs)`
- Each placeholder is reinserted at the proportional paragraph position in the rewritten text

## Output

### DB mode (multi-keyword)

```
jrtt/
├── 李荣浩/
│   ├── article1.md
│   ├── article2.md
│   └── summary.json
├── 人工智能/
│   ├── article1.md
│   └── summary.json
└── summary.json          ← overall aggregate
```

Overall summary (`jrtt/summary.json`):
```json
{
  "status": "done",
  "mode": "multi",
  "total_keywords": 2,
  "keywords": ["李荣浩", "人工智能"],
  "source": "db",
  "pages_per_keyword": 1,
  "total_articles_found": 30,
  "total_articles_scraped": 28,
  "total_articles_failed": 2,
  "total_articles_skipped_video": 0,
  "output_dir": "./jrtt",
  "per_keyword": [...]
}
```

### CLI mode (single keyword)

Articles saved flat in `./jrtt/`, per-keyword `summary.json` only.

Each article Markdown:
```markdown
# <标题>

- **发布时间**: YYYY-MM-DD HH:MM
- **作者**: <昵称>
- **原文链接**: <URL>

---

<正文内容>
```

## Anti-Scraping & Stability

- Calls mobile JSON API directly with `requests` (no headless browser needed)
- Authenticated via real Toutiao Cookie string (bypasses bot detection)
- iPhone Safari User-Agent with proper `Referer` and `X-Requested-With` headers
- Retry with backoff (3 attempts) on API failures
- Only the first article is scraped per keyword to minimize traffic
- Individual article failures do not stop the overall process
- Filenames sanitized for Windows/Linux/Mac compatibility

## Scheduled Task (定时任务)

When the user asks for scheduled Toutiao news scraping (e.g., "每天早上9点帮我抓取头条新闻",
"每2小时抓一次"), use the native `cron` tool with `action: "add"` to create the cron job.
Construct the job definition **inline** in the cron call — do NOT run any helper script.

### Job Definition Template

- `name`: `"jrtt-news-fetch"`
- `description`: Chinese description matching the schedule (e.g., "每天早上9点自动抓取头条新闻")
- `enabled`: `true`
- `schedule`: See translation table below
- `sessionTarget`: `"isolated"` (CRITICAL — independent execution session)
- `wakeMode`: `"now"`
- `payload.kind`: `"agentTurn"`
- `payload.timeoutSeconds`: `1800`
- `payload.message`: The scrape command with **absolute path**:
  ```
  python C:\Users\EDY\AppData\Roaming\BaiClaw\SKILLs\jrtt-news-search\scripts\scrape_toutiao.py --out-dir ./jrtt
  ```
- `delivery.mode`: `"none"` (CRITICAL — avoids "Channel is required" error)

### CRITICAL Rules

1. **`delivery.mode` MUST be `"none"`**. `"announce"` requires an IM channel that may not exist.
2. **`sessionTarget` MUST be `"isolated"`**. `"main"` with delivery settings is unsupported.
3. **For wall-clock times, use `schedule.kind: "cron"`** with the correct 5-field expression.
   Do NOT use `schedule.kind: "at"` (one-time) or `"every"` (interval-only).
4. **Do NOT call `message` tool** inside a cron session. The cron system handles delivery.
   Calling `message` from a cron session without a channel will fail with "Channel is required".
5. **Do NOT run `scheduler.py`** or any Python helper — construct the JSON inline.
6. Use `"every"` schedule kind only when user says "每X小时/分钟" without a wall-clock anchor.

### User Request → Schedule Translation

All times assume `tz: "Asia/Shanghai"`. Use standard 5-field cron:

| User says | `schedule` |
|-----------|-----------|
| 每天早上9点 | `{ kind: "cron", expr: "0 9 * * *", tz: "Asia/Shanghai" }` |
| 每天早上8点半 | `{ kind: "cron", expr: "30 8 * * *", tz: "Asia/Shanghai" }` |
| 每天下午3点 | `{ kind: "cron", expr: "0 15 * * *", tz: "Asia/Shanghai" }` |
| 每小时 | `{ kind: "cron", expr: "0 * * * *", tz: "Asia/Shanghai" }` |
| 工作日早上9点 | `{ kind: "cron", expr: "0 9 * * 1-5", tz: "Asia/Shanghai" }` |
| 每周一早上9点 | `{ kind: "cron", expr: "0 9 * * 1", tz: "Asia/Shanghai" }` |
| 每天9点和18点 | `{ kind: "cron", expr: "0 9,18 * * *", tz: "Asia/Shanghai" }` |
| 每2小时 | `{ kind: "every", everyMs: 7200000 }` |
| 每30分钟 | `{ kind: "every", everyMs: 1800000 }` |

### Job Creation Flow

1. **Check for existing job**: call `cron.list` to see if `jrtt-news-fetch` already exists
2. If exists → use `cron.update` with the job's `id` and new `schedule`
3. If not → use `cron.add` with the full definition
4. **Confirm to user** in Chinese: "已创建定时任务，每天早上 9:00（北京时间）自动抓取头条新闻"

### Managing Existing Jobs

- **Change time**: `cron.update` with new `schedule`
- **Pause**: `cron.update` with `enabled: false`
- **Resume**: `cron.update` with `enabled: true`
- **Delete**: `cron.delete` with job `id`
- **List**: `cron.list`

### Common Mistakes to Avoid

| Mistake | Why it fails |
|---------|-------------|
| `delivery.mode: "announce"` | Needs IM channel → "Channel is required" |
| `schedule.kind: "at"` for daily | Fires once, never repeats |
| `schedule.kind: "every"` for wall-clock | Cannot express "9 AM every day" |
| Calling `message` from cron session | No channel in cron session |
| Running scheduler.py to generate JSON | Unnecessary indirection, may produce wrong `kind` |
