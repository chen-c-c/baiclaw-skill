---
name: "toutiao"
description: |
  Generate Toutiao (今日头条) image-text posts and submit for review.
  Runs generate.py end-to-end: automatically collects hot topics, reads
  brand info, calls the Agent API to generate article content (title, article,
  tags), renders images, and submits for review. Publishes automatically via
  scheduled task once approved — no manual publish needed.

  First-time publish pops up a browser for manual login; cookies are cached
  locally for subsequent auto-publish. When cookies expire the scheduled
  task returns cookieExpired: true — notify user to update cookies in admin.
  Activated when users or scheduled tasks mention "生成头条内容", "发头条",
  "头条图文", "写头条", "create toutiao post".
  IMPORTANT: This skill ONLY runs generate.py. Publishing is handled by
  a cron scheduled task, NOT by the agent.
---

# toutiao Skill

完整的今日头条图文生成流水线。**Agent 只需运行 `python generate.py`，所有步骤自动完成。**

> ⚠️ **严格规则（必须遵守）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - **禁止调用任何 LLM API**（包括 Anthropic SDK、OpenAI SDK、DeepSeek SDK 等）
>   — generate.py 通过调用 Agent API（/chat）生成文章，由 Agent 内部处理 LLM 调用
> - **禁止**查看、分析或搜索 `generate.py`、`render_cover.py` 等内部模块的源代码
>   — `generate.py` 是一个黑盒入口，只需运行即可
> - **禁止**导入或直接调用 `render_draft`、`submit_draft` 等内部函数
> - **禁止**调用 `publish.py`、`check_and_publish.py` — 发布由定时任务自动处理
> - **generate.py 失败时**：不要自行排查原因，不要检查环境变量，不要要求用户提供 API Key。
>   直接将错误输出报告给用户。

## 环境要求

```bash
pip install playwright requests
playwright install chromium
```

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export TOUTIAO_GENERATE_SCRIPT="$SKILLS_ROOT/toutiao/scripts/generate.py"
export TOUTIAO_CHECK_PUBLISH_SCRIPT="$SKILLS_ROOT/toutiao/scripts/check_and_publish.py"
```

---

## 执行规则（必须遵守，优先级高于下方所有说明）

1. **直接运行 `python "$TOUTIAO_GENERATE_SCRIPT"`**：
   - generate.py 是端到端入口，无需传参
   - 内部自动完成：采集热点 → 读取品牌 → 调用 Agent API 生成文章 → 渲染图片 → 提交审核
2. **禁止对 generate.py 做任何探索或修改**：
   - 不要阅读 generate.py 的源代码
   - 不要查看 `render_cover.py`、`submit_for_review.py` 等被 generate.py 调用的模块
   - generate.py 是一个黑盒，只运行它，不要干涉它
3. **draft_path 必须从 generate.py 的 stdout 取**：最后一行输出 JSON
   `{"draft_path": "...", "brief_path": "...", "review_id": "...", "image_count": N}`，
   **禁止使用任何历史路径或自行拼接路径**。
4. **禁止调用 publish.py / check_and_publish.py**：发布由 BaiClaw 定时任务
   （cron `*/5 * * * *`）自动处理，审核通过后自动发布。

---

## 执行步骤

### 唯一步骤：运行 generate.py（端到端生成）

```bash
python "$TOUTIAO_GENERATE_SCRIPT"
```

generate.py 自动完成：
1. 采集今日热点（36氪/爱范儿/DuckDuckGo）
2. 读取企业品牌资料（名称、行业、调性、目标用户、卖点、违禁词）
3. 调用 Agent API（/chat）生成文章（标题 + 正文 + 标签）
4. 渲染 3 张配图（有 ARK_API_KEY 时 AI 生成，否则 Playwright 截图）
5. 提交审核

stdout 最后一行输出（必须解析此 JSON 获取 review_id）：
```json
{"draft_path": "...", "brief_path": "...", "review_id": "...", "image_count": 3}
```

> 图片、审核全部自动处理，无需额外操作。

generate.py 执行完成后，告知用户「内容已提交审核（review_id: xxx），请在管理后台审批，审核通过后将自动发布」。

---

## 架构说明（理解即可，无需查看代码）

```
generate.py                      ← 端到端生成（唯一入口，运行无需传参）
├── 采集热点（collect.py）
├── 读取品牌（enterprise_db.py）
├── 调用 Agent API（/chat）生成文章  ← 和前端 agent-web 对话同一方式，无需 API Key
│      └── agent.exe 内部根据 config.yaml 的 provider 路由调用 LLM
├── render_cover.py              ← HTML+Playwright 截图生成 3 张配图（AI 生成不可用时回退）
└── submit_draft()               ← 提交审核（内部调用 submit_for_review.py）

check_and_publish.py             ← 定时任务自动触发（Agent 勿调用）
publish.py                       ← Playwright 自动化发布（Agent 勿调用）
```

**Agent 只需要做一件事：运行 `python generate.py`。不需要手动构造任何内容。**

---

## 阶段二：检查审核结果并发布（定时任务，每 5 分钟）

```bash
python "$TOUTIAO_CHECK_PUBLISH_SCRIPT"
```

> 该脚本由 BaiClaw 定时任务触发（cron `*/5 * * * *`），无需 Agent 手动调用。
> 拉取 `status=approved` 且 `platform=toutiao` 的文章 → 还原图片和 draft → 调用 publish.py → 回写发布结果。

### Cookie 初始化（首次使用）

首次运行时会自动弹出浏览器，请在弹出窗口中完成头条账号登录（扫码或手机号登录均可），登录成功后脚本自动检测并保存 Cookie。

Cookie 保存位置：`%APPDATA%\BaiClaw\cookies\toutiao-{account-id}.json`（Windows）

**发布成功返回：**
```json
{ "success": true, "publishedUrl": "https://www.toutiao.com/article/7412345678901234567/" }
```

> 发布页面地址：https://mp.toutiao.com/profile_v4/graphic/publish?from=toutiao_pc

**Cookie 失效返回：**
```json
{ "success": false, "error": "Cookie 已失效，请重新登录", "cookieExpired": true }
```

Agent 收到 `cookieExpired: true` 时应：
1. 通过 IM 推送告警：「头条账号 Cookie 已失效，请在管理后台更新」
2. 终止本次定时任务，不重试

---

## 完整流水线

```bash
# 只需一步（不需要手动生成 draft.json）：
python "$TOUTIAO_GENERATE_SCRIPT"
# generate.py 内部自动完成：采集热点 → 读取品牌 → 调用 Agent API 生成文章
# → 渲染配图 → 提交审核

# 审核通过后由定时任务自动发布
```
