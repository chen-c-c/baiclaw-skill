---
name: "content-collect"
description: |
  Collect today's trending topics and hot content for a brand, directly producing
  a structured topics JSON for downstream use (xiaohongshu, article-writer, etc.).
  Scrapes 36氪快讯 + 爱范儿 headlines with DuckDuckGo as fallback.
  Activated when tasks mention "素材收集", "热点搜索", "爆款分析", "collect content",
  "content research", or when preparing for content creation.
official: false
---

# content-collect Skill

搜集今日热点素材，直接输出结构化 `topics.json`，无需 Agent 额外分析。

## Skill 路径（初始化一次）

```bash
export SKILLS_ROOT="${LOBSTERAI_SKILLS_ROOT:-${SKILLS_ROOT:-$HOME/Library/Application Support/LobsterAI/SKILLs}}"
export CONTENT_COLLECT_SCRIPT="$SKILLS_ROOT/content-collect/scripts/collect.py"
```

## 调用方式

```bash
python "$CONTENT_COLLECT_SCRIPT" \
  --brand-name "品牌名称" \
  --industry "行业" \
  --keywords "关键词1,关键词2" \
  --target-audience "目标用户描述" \
  --output /tmp/topics-{taskRunId}.json
```

## 搜集策略（按优先级）

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | 36氪快讯 | 中文科技商业新闻，当天热点 |
| 2 | 爱范儿 | 消费科技/数字产品头版 |
| 3 | DuckDuckGo | 兜底搜索（中文结果较弱） |

## 输出格式

```json
{
  "collected_at": "2026-04-28T16:30:00",
  "date": "2026-04-28",
  "brand": { "name": "白泽AI", "industry": "AI工具", "keywords": "...", "target_audience": "..." },
  "sources_used": [
    { "source": "36氪快讯", "status": "ok" },
    { "source": "爱范儿",   "status": "ok" },
    { "source": "DuckDuckGo", "status": "used" }
  ],
  "topics": [
    { "title": "...", "summary": "...", "url": "...", "source": "36氪快讯", "relevance": 3 },
    ...
  ],
  "total_raw": 28
}
```

## 后续步骤

将 `--output` 文件路径传给 `xiaohongshu/generate.py --topics-json` 继续内容生成。
