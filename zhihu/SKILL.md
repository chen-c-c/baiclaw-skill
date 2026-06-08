---
name: "zhihu"
version: 1.0.0
description: |
  Create and publish Zhihu (知乎) article posts end-to-end.
  Automatically collects today's trending topics, calls Claude/DeepSeek API
  to generate a long-form article draft, renders a single cover image, then publishes via Playwright.
  Activated when users or scheduled tasks mention "生成知乎文章", "发知乎", "发布知乎", "写知乎",
  "知乎专栏", "知乎文章", "create zhihu article", "publish zhihu".

  首次发布时会弹出浏览器请用户手动登录，登录成功后 Cookie 保存到本地，后续自动复用。
  Cookie 失效时返回 cookieExpired: true，需提示用户在管理后台更新 Cookie。
---

# zhihu Skill

完整的知乎文章生成 + 发布流水线，共两个阶段：

> ⚠️ **严格规则（必须遵守）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - 所有步骤必须通过调用下方指定的脚本完成，不得绕过
> - 封面图片**只能**调用 `zhihu/scripts/render_cover.py` 生成，**严禁使用 `render_carousel.py`（无论是 common/ 还是任何其他目录下的）**
> - `generate.py` 已内置 LLM 调用，**禁止**让 Agent 自行生成标题/正文

## 环境要求

```bash
pip install anthropic openai playwright
```

`generate.py` 自动检测可用模型，无需手动配置 API Key：
- 有 `LOBSTER_APIKEY_DEEPSEEK` → 调用 DeepSeek
- 有 `ANTHROPIC_API_KEY` → 调用 Claude
- 可选 `BAICLAW_TEMP_DIR` — 临时文件根目录（默认系统 temp）

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export ZHIHU_GENERATE_SCRIPT="$SKILLS_ROOT/zhihu/scripts/generate.py"
export ZHIHU_CHECK_PUBLISH_SCRIPT="$SKILLS_ROOT/zhihu/scripts/check_and_publish.py"
```

---

## 执行规则（必须遵守，优先级高于下方所有说明）

1. **draft_path 必须从 generate.py 的 stdout 取**：generate.py 最后一行输出 JSON `{"draft_path": "...", "brief_path": "...", "review_id": "..."}`，**禁止使用任何历史路径或自行拼接路径**。generate.py 已内置热点采集、封面渲染和提交审核，无需 Agent 额外调用。

---

## 阶段一：生成内容

```bash
python "$ZHIHU_GENERATE_SCRIPT"
```

**无需任何参数**，generate.py 自动采集今日热点、调用 LLM 生成长文、渲染封面、提交审核。

stdout 最后一行输出（必须解析此 JSON 获取 draft_path）：
```json
{"draft_path": "C:\\...\\runs\\20260509-142340\\draft.json", "brief_path": "...", "review_id": "..."}
```

draft.json 格式（知乎专属）：
```json
{
  "title":    "标题（≤100字）",
  "article":  "正文（1000-3000字，知乎风格结构化长文）",
  "images":   [],
  "platform": "zhihu"
}
```

> 无 `topics` 字段（知乎文章不使用 hashtag）。

generate.py 内部自动完成封面渲染和提交审核，stdout 输出中包含 `review_id`，告知用户「内容已提交审核，请在管理后台查看」。

---

## 阶段二：检查审核结果并发布（定时任务，每 5 分钟）

```bash
python "$ZHIHU_CHECK_PUBLISH_SCRIPT"
```

> 该脚本由 BaiClaw 定时任务触发（cron `*/5 * * * *`），无需 Agent 手动调用。
> 拉取 status=approved 且 platform=zhihu 的文章 → 还原图片和 draft → 调用 publish.py → 回写发布结果。

### Cookie 初始化（首次使用）

首次运行时会自动弹出浏览器，请在弹出窗口中完成知乎登录（账号密码或扫码均可），登录成功后脚本自动检测并保存 Cookie。

Cookie 保存位置：`%APPDATA%\BaiClaw\cookies\zhihu-{account-id}.json`（Windows）

**发布成功返回：**
```json
{ "success": true, "publishedUrl": "https://zhuanlan.zhihu.com/p/..." }
```

**Cookie 失效返回：**
```json
{ "success": false, "error": "Cookie 已失效，请重新登录", "cookieExpired": true }
```

Agent 收到 `cookieExpired: true` 时应：
1. 通过 IM 推送告警：「知乎账号 Cookie 已失效，请在管理后台更新」
2. 终止本次定时任务，不重试

---

## 完整流水线（一步）

```bash
# 一步完成：自动采集热点 → 生成知乎长文 → 渲染封面 → 提交审核，输出 review_id
python "$ZHIHU_GENERATE_SCRIPT"

# 审核通过后由定时任务自动发布（无需手动调用）：
# python "$ZHIHU_CHECK_PUBLISH_SCRIPT"
```
