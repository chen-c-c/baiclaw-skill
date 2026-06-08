"use strict";

const fs = require("fs");
const path = require("path");

const { resolveDbPath, sha1 } = require("./utils");

function requireNodeSqlite() {
  const originalEmitWarning = process.emitWarning;
  process.emitWarning = (warning, ...args) => {
    if (String(warning).includes("SQLite is an experimental feature")) return;
    return originalEmitWarning.call(process, warning, ...args);
  };
  try {
    return require("node:sqlite");
  } finally {
    process.emitWarning = originalEmitWarning;
  }
}

function openDatabase(dbPath) {
  try {
    const Database = require("better-sqlite3");
    return { db: new Database(dbPath), kind: "better-sqlite3" };
  } catch (error) {
    try {
      const { DatabaseSync } = requireNodeSqlite();
      return { db: new DatabaseSync(dbPath), kind: "node:sqlite" };
    } catch {
      throw new Error(`SQLite storage is unavailable. better-sqlite3 failed: ${error.message}`);
    }
  }
}

class WechatArticleStore {
  constructor(options = {}) {
    this.dbPath = resolveDbPath(options.dbPath);
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    const opened = openDatabase(this.dbPath);
    this.db = opened.db;
    this.dbKind = opened.kind;
    if (this.dbKind === "better-sqlite3") {
      this.db.pragma("journal_mode = WAL");
    } else {
      this.db.exec("PRAGMA journal_mode = WAL");
    }
    this.migrate();
  }

  migrate() {
    const sqlPath = path.resolve(__dirname, "..", "migrations", "001_create_wechat_articles.sql");
    const sql = fs.readFileSync(sqlPath, "utf8");
    this.db.exec(sql);
    this.ensureArticleColumns();
  }

  ensureArticleColumns() {
    const columns = this.db.prepare("PRAGMA table_info(wechat_articles)").all();
    const names = new Set(columns.map((column) => column.name));
    if (!names.has("content_html")) {
      this.db.exec("ALTER TABLE wechat_articles ADD COLUMN content_html TEXT NOT NULL DEFAULT ''");
    }
    if (!names.has("content_text")) {
      this.db.exec("ALTER TABLE wechat_articles ADD COLUMN content_text TEXT NOT NULL DEFAULT ''");
    }
    if (!names.has("media_json")) {
      this.db.exec("ALTER TABLE wechat_articles ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'");
    }
    if (!names.has("asset_dir")) {
      this.db.exec("ALTER TABLE wechat_articles ADD COLUMN asset_dir TEXT NOT NULL DEFAULT ''");
    }
  }

  close() {
    this.db.close();
  }

  getState(biz) {
    return this.db
      .prepare("SELECT biz, nickname, last_publish_time, last_offset, last_synced_at FROM wechat_crawler_state WHERE biz = ?")
      .get(biz) || null;
  }

  updateState({ biz, nickname = "", lastPublishTime = 0, lastOffset = 0 }) {
    const now = Date.now();
    this.db.prepare(`
      INSERT INTO wechat_crawler_state (biz, nickname, last_publish_time, last_offset, last_synced_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(biz) DO UPDATE SET
        nickname = excluded.nickname,
        last_publish_time = MAX(wechat_crawler_state.last_publish_time, excluded.last_publish_time),
        last_offset = excluded.last_offset,
        last_synced_at = excluded.last_synced_at,
        updated_at = excluded.updated_at
    `).run(biz, nickname, lastPublishTime, lastOffset, now, now);
  }

  upsertArticles(articles, account) {
    if (!articles.length) return { inserted: 0, updated: 0 };
    let inserted = 0;
    let updated = 0;
    const now = Date.now();
    const stmt = this.db.prepare(`
      INSERT INTO wechat_articles (
        id, biz, title, link, digest, cover, publish_time, author,
        created_at, updated_at, nickname, matched_keywords, content_html, content_text,
        media_json, asset_dir, raw_json
      )
      VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?
      )
      ON CONFLICT(link) DO UPDATE SET
        biz = excluded.biz,
        title = excluded.title,
        digest = excluded.digest,
        cover = excluded.cover,
        publish_time = excluded.publish_time,
        author = excluded.author,
        updated_at = excluded.updated_at,
        nickname = excluded.nickname,
        matched_keywords = excluded.matched_keywords,
        content_html = CASE
          WHEN excluded.content_html != '' THEN excluded.content_html
          ELSE wechat_articles.content_html
        END,
        content_text = CASE
          WHEN excluded.content_text != '' THEN excluded.content_text
          ELSE wechat_articles.content_text
        END,
        media_json = CASE
          WHEN excluded.media_json != '[]' THEN excluded.media_json
          ELSE wechat_articles.media_json
        END,
        asset_dir = CASE
          WHEN excluded.asset_dir != '' THEN excluded.asset_dir
          ELSE wechat_articles.asset_dir
        END,
        raw_json = excluded.raw_json
    `);

    this.db.exec("BEGIN IMMEDIATE");
    try {
      for (const article of articles) {
        const existing = this.db.prepare("SELECT id FROM wechat_articles WHERE link = ?").get(article.link);
        stmt.run(
          sha1(article.link),
          article.biz,
          article.title,
          article.link,
          article.digest || "",
          article.cover || "",
          article.publishTime || 0,
          article.author || "",
          now,
          now,
          account?.nickname || "",
          JSON.stringify(article.matchedKeywords || []),
          article.contentHtml || "",
          article.contentText || "",
          JSON.stringify(article.media || []),
          article.assetDir || "",
          JSON.stringify(article.raw || {}),
        );
        if (existing) updated += 1;
        else inserted += 1;
      }
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
    return { inserted, updated };
  }

  searchArticles(keyword, options = {}) {
    const limit = Math.max(1, Math.min(Number(options.limit) || 20, 200));
    const query = `%${String(keyword || "").trim()}%`;
    const params = options.biz ? [options.biz, query, query, limit] : [query, query, limit];
    const sql = options.biz
      ? `SELECT * FROM wechat_articles WHERE biz = ? AND (title LIKE ? OR digest LIKE ?) ORDER BY publish_time DESC, updated_at DESC LIMIT ?`
      : `SELECT * FROM wechat_articles WHERE title LIKE ? OR digest LIKE ? ORDER BY publish_time DESC, updated_at DESC LIMIT ?`;
    return this.db.prepare(sql).all(...params).map(rowToArticle);
  }

  getLatestArticles(options = {}) {
    const limit = Math.max(1, Math.min(Number(options.limit) || 20, 200));
    const params = options.biz ? [options.biz, limit] : [limit];
    const sql = options.biz
      ? "SELECT * FROM wechat_articles WHERE biz = ? ORDER BY publish_time DESC, updated_at DESC LIMIT ?"
      : "SELECT * FROM wechat_articles ORDER BY publish_time DESC, updated_at DESC LIMIT ?";
    return this.db.prepare(sql).all(...params).map(rowToArticle);
  }
}

function rowToArticle(row) {
  let matchedKeywords = [];
  let media = [];
  try {
    matchedKeywords = JSON.parse(row.matched_keywords || "[]");
  } catch {
    matchedKeywords = [];
  }
  try {
    media = JSON.parse(row.media_json || "[]");
  } catch {
    media = [];
  }
  return {
    id: row.id,
    biz: row.biz,
    title: row.title,
    link: row.link,
    digest: row.digest,
    cover: row.cover,
    publishTime: row.publish_time,
    author: row.author,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    nickname: row.nickname,
    matchedKeywords,
    contentHtml: row.content_html || "",
    contentText: row.content_text || "",
    media,
    assetDir: row.asset_dir || "",
  };
}

module.exports = { WechatArticleStore };
