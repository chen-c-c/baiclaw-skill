---
name: "toutiao"
description: |
  Two modes: (1) Generate content + submit for review — Agent runs generate.py
  which automatically collects hot topics, reads brand info, calls Agent API
  to generate article content (title, article, tags), renders images, and
  submits for review. (2) Publish approved articles — Agent runs
  check_and_publish.py to pull approved articles from backend and auto-publish.
  First-time publish pops up a browser for manual login; cookies are cached
  locally for subsequent auto-publish. When cookies expire the scheduled
  task returns cookieExpired: true — notify user to update cookies in admin.
  Activated when users or scheduled tasks mention "生成头条内容", "发头条",
  "头条图文", "写头条", "create toutiao post", "发布已审核", "已审核文章",
  "发送已审核", "发布头条新闻", "publish approved toutiao".
---

# toutiao Skill

> ⚠️ **严格规则（必须遵守，优先级最高）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - **禁止调用任何 LLM API** — generate.py 通过调用 Agent API 生成文章，
>   由 Agent 内部处理 LLM 调用，不要手动调任何 LLM SDK/API
> - **禁止**查看、分析或搜索 `generate.py`、`render_cover.py`、`check_and_publish.py`、
>   `publish.py` 等内部模块的源代码 — 这些都是黑盒入口，只需运行即可
> - **禁止**导入或直接调用 `render_draft`、`submit_draft` 等内部函数
> - **禁止直接调用 `publish.py`** — 发布通过 `check_and_publish.py` 或定时任务处理
> - **必须使用 Bash 工具执行脚本**，禁止在未执行脚本的情况下编造结果：
>   - ❌ 禁止在未运行脚本的情况下编造"已发布"、"已成功"等结论
>   - ❌ 禁止凭空生成 JSON 输出作为结果
>   - ❌ 禁止用自己的知识替代脚本的实际执行
>   - ✅ 必须先使用 Bash 工具运行脚本，捕获真实的 stdout/stderr 并如实报告
> - **脚本执行失败时**：不要自行排查原因，不要检查环境变量，不要问用户要 API Key
>   - 直接将原始错误输出（stdout + stderr）报告给用户，**禁止将错误替换为"成功"消息**

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

## 模式选择（🚨 必须立即执行，不要问用户问题）

**判断依据**：用户消息中是否包含以下关键词：
- **发布已审核匹配词**：发布已审核、已审核文章、发送已审核、发布头条新闻、publish approved
- **生成内容匹配词**：生成头条、发头条、写头条、create toutiao post、热点

> 如果用户的消息同时包含两类词，优先执行"发布已审核"。

---

### 🏆 模式 A：发布已审核文章（优先级最高）

当用户说"发布已审核头条"、"发送已审核文章"、"publish approved toutiao"或类似表述时：

**立即执行以下操作，不要问任何问题，不要看任何文件：**

1. 打开 Bash 工具，直接运行：
   ```bash
   python "$TOUTIAO_CHECK_PUBLISH_SCRIPT"
   ```
2. **等待脚本执行完毕**（最长 5 分钟）
3. 捕获 stdout 的**最后一行非空文本**（JSON 格式）
4. 根据 JSON 内容如实报告：

   | 最后一行 JSON | 含义 | 回复用户 |
   |---|---|---|
   | `{"processed": N, "success": N, "failed": 0, "details": [...]}` | 发布成功 | "已成功发布 N 篇文章" |
   | `{"processed": 0, "message": "no pending publish"}` | 无待发布文章 | "当前没有待发布的已审核文章" |
   | `{"success": false, "error": "...", "cookieExpired": true}` | Cookie 失效 | "头条账号 Cookie 已失效，请在管理后台更新" |
   | `{"success": false, "error": "登录超时"}` | 登录超时 | "请在浏览器中完成扫码登录" |

5. **如果脚本弹出浏览器**：告诉用户"请在弹出的浏览器窗口中完成扫码登录"

**禁止做的事：**
- ❌ 不要问用户"要发布什么内容"
- ❌ 不要问用户"文章在哪里"
- ❌ 不要读 enterprise_db.py
- ❌ 不要检查数据库
- ❌ 不要手动构造 draft.json
- ❌ 不要直接运行 publish.py
- ❌ 不要查看任何源代码
- ❌ 不要编造结果

---

### 模式 B：生成新内容 + 提交审核

当用户说"生成头条"、"写头条"、"发头条"等时，执行以下步骤。

#### 步骤一：确保 Agent 服务已运行

generate.py 通过调用 Agent API（`POST /chat`）生成文章内容。
确保 Agent 服务（agent.exe）已启动并可访问（默认 `http://localhost:8080/api`）。

可通过环境变量 `BAICLAW_AGENT_API_URL` 自定义 Agent API 地址。

#### 步骤二：运行 generate.py（端到端生成）

```bash
python "$TOUTIAO_GENERATE_SCRIPT"
```

generate.py 自动完成：
1. 采集今日热点（36氪/爱范儿/DuckDuckGo）
2. 读取企业品牌资料（名称、行业、调性、目标用户、卖点、违禁词）
3. 调用 Agent API 生成文章（标题 + 正文 + 标签）
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
generate.py                      ← 端到端生成（模式 B 入口，运行无需传参）
├── 采集热点（collect.py）
├── 读取品牌（enterprise_db.py）
├── 调用 Agent API（/chat）生成文章  ← 和前端 agent-web 对话同一方式，无需 API Key
│      └── agent.exe 内部根据 config.yaml 的 provider 路由调用 LLM
├── render_cover.py              ← HTML+Playwright 截图生成 3 张配图（AI 生成不可用时回退）
└── submit_draft()               ← 提交审核（内部调用 submit_for_review.py）

check_and_publish.py             ← 发布已审核文章（模式 A 入口，Agent 直接调用）
publish.py                       ← Playwright 自动化发布（通过 check_and_publish.py 间接调用，禁止直接调用）
```

**Agent 只需做两件事：运行 `generate.py` 或运行 `check_and_publish.py`。不需要手动构造任何内容。**

---

## Cookie 说明（发布功能相关）

首次运行发布流程时会自动弹出浏览器，请在弹出窗口中完成头条账号登录（扫码或手机号登录均可），登录成功后脚本自动检测并保存 Cookie。

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
2. 终止本次发布任务，不重试

---

## 完整流水线

```bash
# 模式 A：发布已审核文章
python "$TOUTIAO_CHECK_PUBLISH_SCRIPT"

# 模式 B：生成新内容 + 提交审核
python "$TOUTIAO_GENERATE_SCRIPT"
# 审核通过后由定时任务或模式 A 发布
```
