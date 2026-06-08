---
name: "douyin-video"
version: 1.0.0
description: |
  Create and publish Douyin (抖音) short videos end-to-end.
  Automatically collects today's trending topics, calls Claude/DeepSeek API to generate
  video script and Prompt, calls Wanxiang (万相) wan2.5-t2v-preview API to generate vertical video,
  then publishes via Playwright to creator.douyin.com.
  Activated when users or scheduled tasks mention "生成抖音视频", "发抖音视频", "发布短视频",
  "抖音短视频", "create douyin video", "publish douyin video".

  首次发布时会弹出浏览器请用户手动登录，登录成功后 Cookie 保存到本地，后续自动复用。
  Cookie 失效时返回 cookieExpired: true，需提示用户在管理后台更新 Cookie。
---

# douyin-video Skill

完整的抖音短视频生成 + 发布流水线，共三个阶段：

> ⚠️ **严格规则（必须遵守，违反即为错误）**
> - **禁止**自行编写任何 Python/JS 脚本（包括"启动脚本"、"wrapper 脚本"）
> - **禁止**自行设置或修改任何环境变量——所有 API Key 和配置已由 BaiClaw 框架自动注入，Agent 无需也不应干预
> - **禁止**以任何理由调用 `os.environ`、`export`、`set` 等命令修改运行环境
> - 所有步骤必须通过调用下方指定的脚本完成，不得绕过
> - 视频生成**只能**调用 `generate.py`（内含万相 API 调用），禁止用其他方式
> - `generate.py` 已内置 LLM 调用，**禁止**让 Agent 自行生成标题/正文/标签
> - 如果脚本报错（包括 API Key 缺失、LLM 返回空等），**直接把错误信息上报给用户**，不要尝试自行修复环境

## 环境要求

```bash
pip install anthropic openai playwright requests
```

必须设置的环境变量（由 BaiClaw 框架自动注入，**Agent 无需手动设置**）：
- `DASHSCOPE_API_KEY` — 阿里云百炼 API Key（万相文生视频，北京地域）
- `LOBSTER_APIKEY_DEEPSEEK` 或 `ANTHROPIC_API_KEY` — LLM API Key
- 可选 `BAICLAW_TEMP_DIR` — 临时文件根目录（默认系统 temp）

> **注意**：若环境变量缺失导致脚本报错，直接把报错信息告知用户，不要自行写脚本补充或修改环境变量。

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export DOUYIN_VIDEO_GENERATE_SCRIPT="$SKILLS_ROOT/douyin-video/scripts/generate.py"
export DOUYIN_VIDEO_CHECK_PUBLISH_SCRIPT="$SKILLS_ROOT/douyin-video/scripts/check_and_publish.py"
```

---

## 执行规则（必须遵守，优先级高于下方所有说明）

1. **draft_path 必须从 generate.py 的 stdout 取**：generate.py 最后一行输出 JSON `{"draft_path": "...", "brief_path": "...", "review_id": "..."}`，**禁止使用任何历史路径或自行拼接路径**。
2. 视频生成通常需要 **1~5 分钟**，generate.py 会自动轮询等待，无需手动干预。

---

## 阶段一：生成内容与视频

```bash
python "$DOUYIN_VIDEO_GENERATE_SCRIPT"
```

**无需任何参数**，generate.py 自动完成：
1. 采集今日热点话题
2. 调用 LLM 生成视频 Prompt、标题、描述文案、话题标签
3. 调用万相 wan2.5-t2v-preview API 生成 9:16 竖屏短视频
4. 轮询等待视频生成完成（约 1~5 分钟，最长 15 分钟）
5. 下载视频到本地临时目录
6. 提交审核队列

stdout 最后一行输出（必须解析此 JSON 获取 draft_path）：
```json
{"draft_path": "C:\\...\\runs\\20260518-133251\\draft.json", "brief_path": "...", "review_id": "..."}
```

> 品牌、语调、卖点、违禁词全部自动从 SQLite 读取，无需传参。

---

## 阶段三：检查审核结果并发布（定时任务，每 5 分钟）

```bash
python "$DOUYIN_VIDEO_CHECK_PUBLISH_SCRIPT"
```

> 该脚本由 BaiClaw 定时任务触发（cron `*/5 * * * *`），无需 Agent 手动调用。
> 拉取 status=approved、platform=douyin-video 的文章 → 还原视频和 draft → 调用 publish.py → 回写发布结果。

### Cookie 初始化（首次使用）

首次运行时会自动弹出浏览器，请在弹出窗口中完成登录（账号密码或扫码均可），登录成功后脚本自动检测并保存 Cookie。

Cookie 保存位置：`%APPDATA%\BaiClaw\cookies\douyin-{account-id}.json`（Windows）

注意：douyin-video 与 douyin 图集技能共用同一个持久浏览器 Profile，无需重复登录。

**发布成功返回：**
```json
{"success": true, "publishedUrl": "https://www.douyin.com/video/..."}
```

**Cookie 失效返回：**
```json
{"success": false, "error": "Cookie 已失效，请重新登录", "cookieExpired": true}
```

Agent 收到 `cookieExpired: true` 时应：
1. 通过 IM 推送告警：「抖音账号 Cookie 已失效，请在管理后台更新」
2. 终止本次定时任务，不重试

---

## 完整流水线（一步）

```bash
# 一步完成：自动采集热点 → 生成内容 → 调用万相 API → 下载视频 → 提交审核，输出 review_id
python "$DOUYIN_VIDEO_GENERATE_SCRIPT"

# 审核通过后由定时任务自动发布（无需手动调用）：
# python "$DOUYIN_VIDEO_CHECK_PUBLISH_SCRIPT"
```
