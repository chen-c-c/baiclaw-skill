# wechat-article-crawler

BaiClaw 微信公众号文章采集 Skill。

## 自动登录

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" login --format markdown
```

命令会打开微信公众平台登录页。用户扫码登录后，Skill 自动读取并保存 `mp.weixin.qq.com` 的 Cookie
和后台 token。不要扫描文章页弹出的二维码。

用户也可以直接执行采集命令；如果 Cookie/token 已过期，`mp-sync` 会自动打开登录页，等登录成功后继续执行原任务。

## 采集文章

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mp-sync \
  --nickname "星秩云枢数字科技有限公司" \
  --keywords "小寒,小雪" \
  --full \
  --max-pages 5 \
  --format markdown
```

关键词是 OR 匹配：标题或摘要包含任意一个关键词就会入库。
已有增量状态时，默认只看新文章；要重新扫描历史文章请加 `--full`。

默认会抓取：

- 正文 HTML
- 正文纯文本
- 图片
- 视频/iframe/音频链接

图片和可直接下载的音视频会保存在本地资源目录；微信视频如果只能拿到播放器地址，会以媒体链接保存到
`media_json`。

## 本地存储

数据库表：

- `wechat_articles`
- `wechat_crawler_state`

资源目录默认保存在当前 BaiClaw 项目本地：

```txt
D:\work\AI-yw\B\BaiClaw\data\wechat-article-crawler
```

每篇文章会有独立目录，并生成 `media.json`。

## 查询

```bash
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" search --keyword "小寒" --format markdown
node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" latest --limit 20 --format markdown
```
