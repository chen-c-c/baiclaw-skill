---
name: "toutiao"
description: |
  Generate Toutiao (今日头条) image-text posts and submit for review.
  The Agent generates article content (title, article, tags) using its own
  capabilities, then generate.py renders carousel images and submits to the
  review queue. Publishes automatically via scheduled task once approved —
  no manual publish needed.

  First-time publish pops up a browser for manual login; cookies are cached
  locally for subsequent auto-publish. When cookies expire the scheduled
  task returns cookieExpired: true — notify user to update cookies in admin.
  Activated when users or scheduled tasks mention "生成头条内容", "发头条",
  "头条图文", "写头条", "create toutiao post".
  IMPORTANT: This skill ONLY runs generate.py. Publishing is handled by
  a cron scheduled task, NOT by the agent.
---

# toutiao Skill

完整的今日头条图文生成流水线。**Agent 需要做两步：① 用自身能力生成文章内容 ② 运行 generate.py。**

> ⚠️ **严格规则（必须遵守）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - **禁止调用任何 LLM API**（包括 Anthropic SDK、OpenAI SDK、DeepSeek SDK 等）— Agent 使用自身能力生成文章内容，不要写代码调外部 LLM
> - **Agent 负责生成文章内容**（标题、正文、标签），`generate.py` 只负责渲染图片和提交审核
> - **Agent 必须阅读下方的 Prompt 参考**（见步骤一），确保生成的内容符合品牌调性
> - **禁止**查看、分析或搜索 `generate.py`、`render_cover.py` 等内部模块的源代码
>   — `generate.py` 是一个黑盒入口，只需运行即可
> - **禁止**导入或直接调用 `render_draft`、`submit_draft` 等内部函数
> - **禁止**调用 `publish.py`、`check_and_publish.py` — 发布由定时任务自动处理
> - **generate.py 失败时**：不要自行排查原因，不要检查环境变量，不要要求用户提供 API Key。
>   直接将错误输出报告给用户。

## 环境要求

```bash
pip install playwright requests
```

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export TOUTIAO_GENERATE_SCRIPT="$SKILLS_ROOT/toutiao/scripts/generate.py"
export TOUTIAO_CHECK_PUBLISH_SCRIPT="$SKILLS_ROOT/toutiao/scripts/check_and_publish.py"
```

---

## 执行规则（必须遵守，优先级高于下方所有说明）

1. **Agent 使用自身能力（当前会话）生成文章内容**：不要写代码调用任何 LLM API，直接用你的自然语言理解与生成能力。使用下方步骤一的 Prompt 参考，结合品牌信息、媒体账号和热点话题，直接生成标题/正文/标签。生成后保存为 draft.json。
2. **然后运行 `python generate.py --draft-json <draft.json>`**：自动完成渲染 3 张配图 + 提交审核。
3. **禁止对 generate.py 做任何探索或修改**：
   - 不要阅读 generate.py 的源代码
   - 不要查看 `render_cover.py`、`submit_for_review.py` 等被 generate.py 调用的模块
   - generate.py 是一个黑盒，只运行它，不要干涉它
4. **draft_path 必须从 generate.py 的 stdout 取**：最后一行输出 JSON
   `{"draft_path": "...", "brief_path": "...", "review_id": "..."}`，
   **禁止使用任何历史路径或自行拼接路径**。
5. **禁止调用 publish.py / check_and_publish.py**：发布由 BaiClaw 定时任务
   （cron `*/5 * * * *`）自动处理，审核通过后自动发布。

---

## 执行步骤

### 步骤一：Agent 生成文章内容（用自身能力，不调外部 LLM）

Agent 使用自身的自然语言生成能力，根据以下三类信息直接生成文章（标题、正文、标签），不要写代码调用任何 LLM API，保存为 JSON 文件：

**① 企业信息** — 从 SQLite 读取品牌资料：
```python
from enterprise_db import get_enterprise_data, get_first_brand
data = get_enterprise_data()
brand_info = get_first_brand(data) if data else {}
# 可用字段：name, industry, tone, targetAudience, sellingPoints, forbiddenWords
```

**② 媒体信息** — 从 SQLite 读取头条发布账号：
```python
from enterprise_db import get_toutiao_account
account = get_toutiao_account(data)
# 可用字段：accountName（账号名称）
```

**③ 热点话题** — 自动采集今日热点（如果已有 topics.json 可直接使用，否则采集）：
```python
from collect import collect_from_db
topics_json = collect_from_db(str(run_dir))
# topics 结构：{"topics": [{"title": "...", "summary": "...", "source": "..."}]}
```

**完整 Prompt 参考**（结合以上三类信息，生成高质量头条图文）：
```text
你是一位资深今日头条运营专家。请为品牌「{brand_name}」（{industry}行业，目标用户：{audience}）生成一条今日头条图文内容。
发布账号：{account_name}

内容风格：{tone}
核心卖点：{selling_points}
禁止出现以下词汇：{forbidden_words}

可供参考的今日热点话题（优先使用 TOP1）：
  [1] {话题标题}
      摘要：{话题摘要}
      来源：{话题来源}

请严格按照以下要求生成：

1. 【标题】1个，10~30字，新闻式标题，含核心信息点，注重信息量和价值感，不用夸张标题党
2. 【正文】300~800字，结构：
   - 开头：一句话点明核心信息或新闻由头
   - 中间：信息型内容或深度分析，自然融入品牌（{brand_name}）的价值或观点
   - 结尾：总结观点或引导互动（欢迎在评论区讨论... / 关注我了解更多...）
3. 【标签】7-10 个话题标签（#开头），采用「金字塔结构」分层，核心原则：
   「平台热度 × 内容贴合」双权重——不堆砌热门泛标签，精准长尾优先于无关热词
   • 大流量泛标签 1-2个：覆盖「{industry}」赛道，承接平台推荐流量
   • 精准垂类标签 2-3个：直接对应文章核心主题和功能关键词
   • 人群定位标签 1个：锁定「{audience}」，帮助系统精准分发
   • 场景标签 1个：对应用户具体使用场景，提升点击和收藏
   • 痛点/需求标签 1个：匹配用户主动搜索意图，承接搜索流量
   • 品牌沉淀标签 1个：固定使用「#{brand_name}」积累账号资产
```

**draft.json 格式要求：**
```json
{
  "title": "标题（10~30字）",
  "article": "正文（300~800字）",
  "topics": ["#标签1", "#标签2", ...],
  "platform": "toutiao"
}
```

### 步骤二：渲染图片 + 提交审核

```bash
python "$TOUTIAO_GENERATE_SCRIPT" --draft-json <draft.json 路径>
```

generate.py 自动完成：渲染 3 张配图（HTML + Playwright 截图）→ 提交审核。

stdout 最后一行输出（必须解析此 JSON 获取 review_id）：
```json
{"draft_path": "...", "brief_path": "...", "review_id": "...", "image_count": 3}
```

> 图片、审核全部自动处理，无需额外操作。

generate.py 执行完成后，告知用户「内容已提交审核（review_id: xxx），请在管理后台审批，审核通过后将自动发布」。

---

## 架构说明（理解即可，无需查看代码）

```
Agent 生成文章内容（当前会话）  ← Agent 使用自身 LLM 能力生成
       │
       ▼  draft.json
generate.py --draft-json        ← 仅渲染配图 + 提交审核
├── render_cover.py             ← HTML+Playwright 截图生成 3 张配图
└── submit_draft()              ← 提交审核（内部调用 submit_for_review.py）

check_and_publish.py            ← 定时任务自动触发（Agent 勿调用）
publish.py                      ← Playwright 自动化发布（Agent 勿调用）
```

**Agent 只需要做两件事：① 生成 draft.json ② 运行 `generate.py --draft-json`。**

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
# 步骤 1：Agent 生成文章内容（标题+正文+标签），保存为 draft.json

# 步骤 2：渲染配图 + 提交审核
python "$TOUTIAO_GENERATE_SCRIPT" --draft-json /path/to/draft.json

# 步骤 3：审核通过后由定时任务自动发布（无需手动调用）：
# python "$TOUTIAO_CHECK_PUBLISH_SCRIPT"
```

