CREATE TABLE IF NOT EXISTS wechat_articles (
  id TEXT PRIMARY KEY,
  biz TEXT NOT NULL,
  title TEXT NOT NULL,
  link TEXT NOT NULL,
  digest TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  publish_time INTEGER NOT NULL DEFAULT 0,
  author TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  nickname TEXT NOT NULL DEFAULT '',
  matched_keywords TEXT NOT NULL DEFAULT '[]',
  content_html TEXT NOT NULL DEFAULT '',
  content_text TEXT NOT NULL DEFAULT '',
  media_json TEXT NOT NULL DEFAULT '[]',
  asset_dir TEXT NOT NULL DEFAULT '',
  raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_articles_link
ON wechat_articles(link);

CREATE INDEX IF NOT EXISTS idx_wechat_articles_biz_publish_time
ON wechat_articles(biz, publish_time DESC);

CREATE INDEX IF NOT EXISTS idx_wechat_articles_updated_at
ON wechat_articles(updated_at DESC);

CREATE TABLE IF NOT EXISTS wechat_crawler_state (
  biz TEXT PRIMARY KEY,
  nickname TEXT NOT NULL DEFAULT '',
  last_publish_time INTEGER NOT NULL DEFAULT 0,
  last_offset INTEGER NOT NULL DEFAULT 0,
  last_synced_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
