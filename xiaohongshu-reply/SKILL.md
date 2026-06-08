---
name: "xiaohongshu-reply"
description: |
  Automatically reads Xiaohongshu (小红书) comments and @mentions, uses LLM to classify
  intent and generate conversion-oriented replies, then auto-sends via Playwright.
  Uses phone + SMS verification code to login (two-phase interactive flow).
  Activated when users mention "回复小红书评论", "处理小红书私信", "回复小红书留言",
  "小红书自动回复", "自动回复小红书", "xhs reply", "回复小红书消息",
  "回复小红书通知", "回复小红书@", "回复小红书提到",
  or similar requests about replying to Xiaohongshu interactions.
---

# xiaohongshu-reply Skill

自动回复小红书评论和 @ 提到我的，通过 SMS 验证码登录，浏览器保持打开。

Phone 从 SQLite 自动读取（`accountName` 字段），与 xiaohongshu publish skill 使用相同的账号数据。

> **严格规则（必须遵守）**
> - 评论和 @ 回复 ≤ 50 字
> - `complaint` 类意图**禁止**自动回复，必须转人工处理
> - `--headless` 必须为 `false`（SMS 登录需要可见浏览器）
> - 禁止自编代码修改 `replied.json`，只能通过 `main.py` 写入
> - **必须后台运行脚本**，在检测到 `need_code` 后立即向用户索要验证码
> - 自动滚动加载全部消息，回复从旧到新（从下往上），避免遗漏
> - 每次运行后记录最后回复时间到 SQLite (`xhs_reply_last_ts`)，下次只处理此时间之后的消息

## 环境要求

```bash
pip install playwright anthropic openai
playwright install chromium
```

LLM 自动检测可用模型：
- 有 `ANTHROPIC_API_KEY` → 调用 Claude
- 有 `LOBSTER_APIKEY_DEEPSEEK` → 调用 DeepSeek

## Agent 执行流程（必须严格遵守）

**关键原则：脚本必须后台运行，浏览器保持打开，轮询间隔已优化为 1 秒。**

### Step 1: 后台启动脚本

```bash
python <SKILL_DIR>/scripts/main.py --limit 50 --headless false
```

**必须设置为后台运行**，脚本会：

- 打开浏览器 → 导航到 `www.xiaohongshu.com/notification`
- 如果已登录 → 直接抓取+回复，输出 summary → 跳到 Step 5
- 如果需要登录 → 填入手机号 → 勾选协议 → 点击"发送验证码"（超时已优化至 3 秒，10-15 秒内完成）

### Step 2: 检测 `need_code`

**脚本启动后约 10-15 秒**，读取脚本输出。输出中包含：

```json
{"status": "need_code", "message": "验证码已发送到 1xxxxxxxxxx，请在会话中输入验证码", "phone": "1xxxxxxxxxx", "code_file": "C:\\Users\\...\\code.txt"}
```

提取 `code_file` 路径和 `phone` 号码。**浏览器保持打开，不要关闭。**

### Step 3: 向用户索要验证码

**立即告诉用户**：验证码已发送到手机号 `{phone}`，请查看短信并提供 6 位验证码。
**等待用户回复验证码。**

### Step 4: 写入验证码

将用户提供的 6 位数字写入 `code_file` 路径（纯文本，只写数字）。

脚本在后台以 **1 秒间隔**快速轮询该文件，检测到内容后自动继续：输入验证码 → 点击登录 → 抓取 → 生成回复 → 发送回复。

### Step 5: 等待完成并报告

等待脚本完成，读取最终输出。向用户报告 summary：
- 总共处理了多少条
- 成功回复了多少条
- 需要人工处理的有多少条（complaint 类）
- 跳过了多少条垃圾信息

---

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--limit N` | 50 | 最多处理条数 |
| `--headless true/false` | false | 必须为 false |
| `--code <验证码>` | 无 | 直接提供验证码，跳过文件轮询 |
| `--model <id>` | 自动检测 | LLM 模型 |
| `--run-dir <path>` | 自动生成 | 输出目录 |

## 输出 JSON

### 等待验证码
```json
{"status": "need_code", "message": "验证码已发送到 177xxxxxxxx，请将6位验证码写入文件: ...", "phone": "177xxxxxxxx", "code_file": "C:\\Users\\...\\code.txt"}
```

### 完成
```json
{"status": "done", "run_id": "20260512-153000", "total": 10, "replied": 6, "needs_human": 1, "skipped_spam": 3, "completed_at": "2026-05-12T15:30:00+08:00"}
```

### 无未回复通知
```json
{"status": "done", "total": 0, "replied": 0, "message": "没有未回复的通知"}
```

## 错误码

| 输出 | 含义 | 处理 |
|------|------|------|
| `{"error": "未找到小红书发布账号"}` | SQLite 无账号 | 检查企业配置 |
| `{"error": "未配置账号手机号"}` | accountName 为空 | 补充手机号 |
| `{"error": "发送验证码失败"}` | 填表/点击失败 | 检查 debug 截图 |
| `{"error": "验证码登录失败"}` | 验证码错误或超时 | 重新运行 |
| `needs_human > 0` | 有负面评论 | 需人工处理 |

## 输出目录

`%APPDATA%\BaiClaw\baiclaw\xhs-reply\{YYYYMMDD-HHmmss}\`

调试截图和 HTML dump 也会保存到此目录。

## 定时任务

需要交互输入验证码，适合手动触发，不适合 cron 定时。

## 文件结构

```
xiaohongshu-reply/
├── SKILL.md                  ← 此文件
└── scripts/
    ├── main.py               ← 唯一入口，两阶段全部逻辑
    ├── xhs_reply_utils.py    ← 工具函数（路径/LLM/去重/JSON）
    ├── enterprise_db.py      ← SQLite 读取
    └── reply_generator.py    ← 意图分类 + 回复生成（可单独使用）
```
