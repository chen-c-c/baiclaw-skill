---
name: "xiaohongshu"
version: 1.0.0
description: |
  Create and publish Xiaohongshu (小红书) image-text posts end-to-end.
  Automatically collects today's trending topics, calls Claude/DeepSeek API
  to generate draft content (titles, article, hashtags), renders carousel images,
  then publishes via Playwright.
  Activated when users or scheduled tasks mention "生成小红书内容", "写小红书",
  "发布小红书", "generate xiaohongshu post", "publish xiaohongshu", "发布笔记".

  首次发布时会弹出浏览器请用户手动登录，登录成功后 Cookie 保存到本地，后续自动复用。
  Cookie 失效时返回 cookieExpired: true，需提示用户在管理后台更新 Cookie。
---

# xiaohongshu Skill

完整的小红书图文生成 + 发布流水线，共两个阶段：

> ⚠️ **严格规则（必须遵守）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - 所有步骤必须通过调用下方指定的脚本完成，不得绕过
> - 图片生成**只能**调用 `render_carousel.py`，禁止用 Pillow/PIL 或其他方式自行绘图
> - `generate.py` 已内置 LLM 调用，**禁止**让 Agent 自行生成标题/正文/标签

## 环境要求

```bash
pip install requests playwright
playwright install chromium
```

> generate.py 通过调用 Agent API（`POST /chat`）生成文章内容，无需 API Key。
> Agent API 地址由 `BAICLAW_AGENT_API_URL` 环境变量指定，默认 `http://localhost:8080/api`。

## Skill 路径

```bash
export XHS_GENERATE_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/generate.py"
export XHS_PUBLISH_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/publish.py"
export XHS_CHECK_PUBLISH_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/check_and_publish.py"
```

---

## 执行规则（必须遵守，优先级高于下方所有说明）

1. **draft_path 必须从 generate.py 的 stdout 取**：generate.py 最后一行输出 JSON `{"draft_path": "...", "brief_path": "...", "review_id": "..."}`，**禁止使用任何历史路径或自行拼接路径**。generate.py 已内置热点采集、轮播图渲染和提交审核，无需 Agent 额外调用。

---

## 阶段一：生成内容

```bash
python "$XHS_GENERATE_SCRIPT"
```

**无需任何参数**，generate.py 自动采集今日热点、调用 LLM 生成内容、渲染轮播图、提交审核。

stdout 最后一行输出（必须解析此 JSON 获取 draft_path）：
```json
{"draft_path": "C:\\...\\runs\\20260509-142340\\draft.json", "brief_path": "...", "review_id": "..."}
```

> 品牌、语调、卖点、违禁词全部自动从 SQLite 读取，无需传参。

generate.py 内部自动完成轮播图渲染和提交审核，stdout 输出中包含 `review_id`，告知用户「内容已提交审核，请在管理后台查看」。

---

## 阶段二：检查审核结果并发布（定时任务，每 5 分钟）

```bash
python "$XHS_CHECK_PUBLISH_SCRIPT"
```

> 该脚本由 BaiClaw 定时任务触发（cron `*/5 * * * *`），无需 Agent 手动调用。
> 拉取 status=approved 的文章 → 还原图片和 draft → 调用 publish.py → 回写发布结果。

### Cookie 初始化（首次使用 / 已失效）

Cookie 失效时 publish.py 会自动弹出浏览器窗口让您重新登录，登录后脚本自动保存新 Cookie 并继续发布。

首次运行或 Cookie 过期时弹出的浏览器窗口中请完成账号登录（账号密码或扫码均可），登录成功后脚本自动检测、保存 Cookie 到本地数据库并继续发布。

Cookie 保存位置：`%APPDATA%\BaiClaw\cookies\{account-id}.json`（文件缓存）及本地 SQLite 数据库。

**发布成功返回：**
```json
{ "success": true, "publishedUrl": "https://www.xiaohongshu.com/explore/..." }
```

> Agent 无需处理 cookieExpired 场景——publish.py 已在内部自动弹出浏览器等待手工登录并刷新 Cookie。

---

## 完整流水线（一步）

```bash
# 一步完成：自动采集热点 → 生成内容 → 渲染轮播图 → 提交审核，输出 review_id
python "$XHS_GENERATE_SCRIPT"

# 审核通过后由定时任务自动发布（无需手动调用）：
# python "$XHS_CHECK_PUBLISH_SCRIPT"
```
