---
name: "xiaohongshu"
description: |
  Create and publish Xiaohongshu (小红书) image-text posts end-to-end.
  Takes a topics JSON from content-collect (or manually created), generates content
  instructions for the Agent, then publishes via Playwright browser automation.
  Activated when users or scheduled tasks mention "生成小红书内容", "写小红书",
  "发布小红书", "generate xiaohongshu post", "publish xiaohongshu", "发布笔记",
  or after content-collect has produced a topics JSON file.

  首次发布时会弹出浏览器请用户手动登录，登录成功后 Cookie 保存到本地，后续自动复用。
  Cookie 失效时返回 cookieExpired: true，需提示用户在管理后台更新 Cookie。
official: false
---

# xiaohongshu Skill

完整的小红书图文生成 + 发布流水线，分两个阶段：

> ⚠️ **严格规则（必须遵守）**
> - **禁止**自行编写任何 Python/JS 脚本来生成图片、写文件、处理 JSON
> - 所有步骤必须通过调用下方指定的脚本完成，不得绕过
> - draft.json 直接用 `Write` 工具写入，不要写临时脚本来生成它
> - 图片生成**只能**调用 `render_carousel.py`，禁止用 Pillow/PIL 或其他方式自行绘图

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export XHS_GENERATE_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/generate.py"
export XHS_RENDER_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/render_carousel.py"
export XHS_PUBLISH_SCRIPT="$SKILLS_ROOT/xiaohongshu/scripts/publish.py"
export CONTENT_COLLECT_SCRIPT="$SKILLS_ROOT/content-collect/scripts/collect.py"
```

---

## 阶段一：生成内容指令

```bash
python "$XHS_GENERATE_SCRIPT" \
  --topics-json /tmp/topics-{taskRunId}.json \
  --brand-name "品牌名称" \
  --industry "行业" \
  --tone "活泼/专业/温暖" \
  --selling-points "核心卖点（逗号分隔）" \
  --forbidden-words "违禁词1,违禁词2" \
  --image-mode temp \
  --image-path "/tmp/title.png" \
  --output /tmp/content-brief-{taskRunId}.json
```

**参数说明：**
- `--topics-json`: collect.py 输出的 topics JSON 路径
- `--brand-name`/`--industry`：覆盖 topics JSON 中的值（可选）
- `--image-mode`: `temp`（默认本地图片） | `local`（同temp） | `cloud`（预留）
- `--image-path`: 封面图本地路径
- `--output`: 将结果写入文件（不指定则输出到 stdout）

**输出示例：**

```json
{
  "instruction": "你是一位资深小红书运营专家...请严格按照以下要求生成..."
}
```

### Agent 处理步骤

收到 `instruction` 后，Agent 应：

1. 按 `instruction` 生成 3 个备选标题（各不同情绪钩子，不超过 20 字）
2. 正文 **500-800 字**（开头共鸣 → 中间干货/故事带品牌 → 结尾引导互动），字数不足须补足
3. 5-8 个话题标签（`#` 开头）
4. 使用 `Write` 工具直接将结果写入 draft JSON 文件，**禁止写临时 Python 脚本来生成 JSON**

**draft JSON 格式（images 先留空数组，步骤3渲染后再填入）：**

```json
{
  "titles": ["标题1（首选）", "标题2", "标题3"],
  "article": "正文内容（500-800字）",
  "topics": ["#话题1", "#话题2", "#话题3"],
  "images": []
}
```

---

## 阶段二：发布笔记

```bash
python "$XHS_PUBLISH_SCRIPT" \
  --draft-json /tmp/draft-{taskRunId}.json \
  --account-id "acc_xxxxx" \
  --headless true
```

**参数：**
- `--headless`: `true`（无头，默认） | `false`（可见浏览器，调试用）

### Cookie 初始化（首次使用）

首次运行时会自动弹出浏览器（即使设置 `--headless true`，首次登录必须可见），请在弹出窗口中完成登录（账号密码或扫码均可），登录成功后脚本自动检测并保存 Cookie。

Cookie 保存位置：`%APPDATA%\BaiClaw\cookies\{account-id}.json`（Windows）

**发布成功返回：**
```json
{ "success": true, "publishedUrl": "https://www.xiaohongshu.com/explore/..." }
```

**Cookie 失效返回：**
```json
{ "success": false, "error": "Cookie 已失效，请重新登录", "cookieExpired": true }
```

Agent 收到 `cookieExpired: true` 时应：
1. 通过 IM 推送告警：「小红书账号 Cookie 已失效，请在管理后台更新」
2. 终止本次定时任务，不重试

---

## 完整流水线（四步）

```bash
# 步骤1：采集热点素材
python "$CONTENT_COLLECT_SCRIPT" \
  --brand-name "白泽AI" --industry "AI工具" --keywords "AI助手,效率工具" \
  --target-audience "职场白领" --output /tmp/topics-001.json

# 步骤2：生成内容指令（Agent 据此生成 draft JSON）
python "$XHS_GENERATE_SCRIPT" \
  --topics-json /tmp/topics-001.json --output /tmp/content-brief-001.json

# Agent 分析 instruction → 将标题/正文/标签写入 /tmp/draft-001.json

# 步骤3：渲染 6 张轮播封面图
python "$XHS_RENDER_SCRIPT" \
  --draft-json /tmp/draft-001.json \
  --brand "白泽AI" \
  --output-dir /tmp/carousel-001

# render_carousel.py 输出 JSON，其中 images 字段为 6 张 PNG 路径
# Agent 需将 images 字段更新回 draft-001.json，再执行步骤4

# 步骤4：发布
python "$XHS_PUBLISH_SCRIPT" \
  --draft-json /tmp/draft-001.json --account-id "acc_01" --headless true
```

**步骤3 → 步骤4 衔接说明**

`render_carousel.py` 的输出示例：
```json
{ "images": ["/tmp/carousel-001/01_cover.png", "...06_cta.png"], "count": 6 }
```

Agent 收到后，将 `images` 数组写回 `draft-001.json` 的 `images` 字段，再调用 publish.py 发布（publish.py 会将这 6 张图依次上传）。
