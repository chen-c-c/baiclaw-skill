---
name: wechat-article-crawler
description: |
  采集微信公众号文章。支持打开微信公众平台登录页让用户扫码登录，自动保存 Cookie/token；
  通过公众号后台接口按公众号名称或 fakeid 拉取历史文章，本地按关键词 OR 过滤，抓取正文 HTML、
  正文文本、图片、视频/iframe 链接并入库。用于 AI Agent、workflow、MCP、RAG 或本地知识库。
---

# WeChat Article Crawler

这是 BaiClaw 当前项目里的微信公众号内容采集 Skill。它保持现有 Skill 注册和调用方式，通过
`scripts/index.js` 对外提供命令。

## 什么时候使用

当用户要“抓取指定公众号中包含指定关键词的文章”“同步公众号历史文章”“保存公众号正文到本地”
时使用本 Skill。

关键词是 OR 逻辑：标题或摘要包含任意一个关键词就算命中。

## 自动登录与自动续跑

用户可以直接发采集任务。执行 `sync`、`sync-all` 或 `mp-sync` 时，如果 Cookie/token 缺失、过期或失效，
命令入口会自动打开微信公众平台登录页，等待用户扫码或账号密码登录，成功后自动继续执行原采集任务。

也可以单独运行登录命令：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" login --format markdown
```

该命令会打开 `https://mp.weixin.qq.com/`，用户扫码登录后，Skill 会通过浏览器 DevTools 自动读取
`mp.weixin.qq.com` 的 Cookie 和后台 URL 中的 token，并保存到：

```txt
<BaiClaw userData>/storage/cookies/wechat.json
```

不要让用户扫描文章页弹出的二维码。那只是“在微信中打开文章”，不能生成后台 Cookie/token。

## 常用命令

按公众号名称采集并保存正文和媒体：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mp-sync \
  --nickname "星秩云枢数字科技有限公司" \
  --keywords "小寒,小雪" \
  --full \
  --max-pages 5 \
  --format markdown
```

已有增量状态时，默认只同步新文章；用户要查历史文章时加 `--full`。

如果已经知道 fakeid：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mp-sync \
  --nickname "星秩云枢数字科技有限公司" \
  --fakeid "MzE5MTc0MTQzNQ==" \
  --keywords "小寒,小雪" \
  --format markdown
```

默认会抓取正文 HTML、正文文本，提取图片、视频、iframe、音频等媒体。图片和可直接下载的视频/音频会保存到
本地资源目录；微信视频通常是播放器或 iframe 地址，无法直接下载时会作为媒体链接保存。

不下载媒体，仅保存正文和媒体链接：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mp-sync \
  --nickname "公众号名称" \
  --keywords "关键词1,关键词2" \
  --no-download-media \
  --format markdown
```

查询本地库：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" search --keyword "小寒" --format markdown
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" latest --limit 20 --format markdown
```

## 存储

文章写入 BaiClaw 的 SQLite：

- `wechat_articles`
- `wechat_crawler_state`

字段包含标题、链接、摘要、封面、发布时间、作者、正文 HTML、正文文本、媒体 JSON、资源目录等。`link`
有唯一索引，因此重复执行是幂等的。

媒体和正文默认保存在当前 BaiClaw 项目本地：

```txt
D:\work\AI-yw\B\BaiClaw\data\wechat-article-crawler\articles\<article-id>-<title>
```

每篇文章目录会生成 `content.html`、`content.txt`、`article.json`、`media.json` 和 `assets/`。`content.html`
是可直接在浏览器打开的离线正文，不保存微信页面壳和底部互动 UI。

## 调度与 MCP

生成调度任务：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" schedule --format json
```

启动 MCP stdio server：

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mcp
```

对外能力：

- `syncAccountArticles(biz)`
- `syncAllAccounts()`
- `searchArticles(keyword)`
- `getLatestArticles(biz)`

## 操作规则

- 不使用搜狗微信作为主链路。
- 用户直接发采集任务即可；Cookie/token 失效时自动运行登录流程并重试原任务。
- 命中规则只依赖本地标题/摘要过滤，不依赖微信搜索。
- 输出必须把 Markdown 结果展示给用户，不要只执行工具后结束。
