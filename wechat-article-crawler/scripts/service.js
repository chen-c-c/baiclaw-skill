"use strict";

const path = require("path");

const { WechatArticleCrawler } = require("./crawler");
const { loadCrawlerConfig } = require("./config");
const { WechatMpBackendCrawler } = require("./mp-backend-crawler");
const { WechatArticleStore } = require("./storage");
const { formatMarkdownTable, logger, splitList, uniq } = require("./utils");

function findAccount(config, biz) {
  return (config.accounts || []).find((account) => account.biz === biz);
}

async function syncAccountArticles(biz, options = {}) {
  const config = loadCrawlerConfig({ ...options, biz: biz || options.biz });
  const account = findAccount(config, biz || options.biz);
  if (!account) {
    throw new Error(`No enabled account config found for biz=${biz || options.biz || ""}`);
  }
  const crawler = new WechatArticleCrawler(config, { logger: logger() });
  try {
    return await crawler.syncAccount(account, options);
  } finally {
    crawler.close();
  }
}

async function syncAllAccounts(options = {}) {
  const config = loadCrawlerConfig(options);
  const crawler = new WechatArticleCrawler(config, { logger: logger() });
  try {
    return await crawler.syncAll(options);
  } finally {
    crawler.close();
  }
}

async function syncMpBackendAccount(options = {}) {
  const config = loadCrawlerConfig(options);
  const account = {
    nickname: String(options.nickname || options.account || "").trim(),
    fakeid: String(options.fakeid || "").trim(),
    biz: String(options.biz || options.fakeid || "").trim(),
    keywords: uniq(splitList(options.keywords || options.keyword)),
    enabled: true,
  };
  if (!account.nickname && !account.fakeid) {
    throw new Error("Missing nickname or fakeid for mp backend sync");
  }
  const crawler = new WechatMpBackendCrawler(config, { logger: logger() });
  try {
    return await crawler.syncAccount(account, {
      ...options,
      token: options.token || process.env.WECHAT_MP_TOKEN,
      fetchBody: options.fetchBody !== false && options.noFetchBody !== true,
      downloadMedia: options.downloadMedia !== false && options.noDownloadMedia !== true,
      outputDir: options.outputDir || config.outputDir,
    });
  } finally {
    crawler.close();
  }
}

function searchArticles(keyword, options = {}) {
  const config = loadCrawlerConfig(options);
  const store = new WechatArticleStore({ dbPath: config.dbPath });
  try {
    return store.searchArticles(keyword || options.keyword, {
      biz: options.biz,
      limit: options.limit,
    });
  } finally {
    store.close();
  }
}

function getLatestArticles(biz, options = {}) {
  const config = loadCrawlerConfig({ ...options, biz: biz || options.biz });
  const store = new WechatArticleStore({ dbPath: config.dbPath });
  try {
    return store.getLatestArticles({
      biz: biz || options.biz,
      limit: options.limit,
    });
  } finally {
    store.close();
  }
}

function mediaSummary(article) {
  const media = Array.isArray(article.media) ? article.media : [];
  if (!media.length) return "0";
  const saved = media.filter((item) => item.status === "saved").length;
  const linked = media.filter((item) => item.status === "linked").length;
  const failed = media.filter((item) => item.status === "failed").length;
  return `总数 ${media.length} / 已保存 ${saved} / 链接 ${linked} / 失败 ${failed}`;
}

function articleSaveDir(article) {
  if (article.articleDir) return article.articleDir;
  if (article.assetDir) return path.basename(article.assetDir).toLowerCase() === "assets"
    ? path.dirname(article.assetDir)
    : article.assetDir;
  return "";
}

function articleRows(articles) {
  return (articles || []).map((article) => [
    article.title,
    article.nickname || article.biz,
    (article.matchedKeywords || []).join(", "),
    article.publishTime ? new Date(article.publishTime * 1000).toLocaleString("zh-CN", { hour12: false }) : "",
    mediaSummary(article),
    articleSaveDir(article),
    article.link,
  ]);
}

function articleHeaders() {
  return ["标题", "公众号", "命中关键词", "发布时间", "媒体", "保存目录", "链接"];
}

function formatArticlesMarkdown(title, articles) {
  const rows = articleRows(articles);
  if (!rows.length) return `## ${title}\n\n未找到符合条件的文章。`;
  return [
    `## ${title}`,
    "",
    formatMarkdownTable(rows, articleHeaders()),
  ].join("\n");
}

function formatSyncResultMarkdown(result) {
  if (result.accounts) {
    const accountRows = result.accounts.map((item) => [
      item.nickname || item.biz,
      item.biz,
      item.keywords.join(", "),
      item.fetched,
      item.matched,
      item.inserted,
      item.updated,
      item.pages,
    ]);
    const articleRowsForAll = result.accounts.flatMap((item) => articleRows(item.articles));
    return [
      "## 微信公众号文章同步结果",
      "",
      `- 公众号数：${result.accounts.length}`,
      `- 抓取文章：${result.summary.fetched}`,
      `- 关键词匹配：${result.summary.matched}`,
      `- 新增入库：${result.summary.inserted}`,
      `- 更新去重：${result.summary.updated}`,
      "",
      formatMarkdownTable(accountRows, ["公众号", "Biz/FakeID", "关键词", "抓取", "匹配", "新增", "更新", "页数"]),
      "",
      articleRowsForAll.length
        ? formatMarkdownTable(articleRowsForAll, articleHeaders())
        : "未在已启用公众号中找到包含任一关键词的文章。",
    ].join("\n");
  }

  const lines = [
    "## 微信公众号文章同步结果",
    "",
    `- 公众号：${result.nickname || result.biz}`,
    `- Biz/FakeID：${result.biz}`,
    `- 关键词：${result.keywords.join(", ")}`,
    `- 抓取文章：${result.fetched}`,
    `- 关键词匹配：${result.matched}`,
    `- 新增入库：${result.inserted}`,
    `- 更新去重：${result.updated}`,
    `- 分页数：${result.pages}`,
  ];
  if (!result.matched) {
    lines.push("", "未在指定公众号中找到包含任一关键词的文章。");
    return lines.join("\n");
  }
  lines.push("", formatMarkdownTable(articleRows(result.articles), articleHeaders()));
  return lines.join("\n");
}

module.exports = {
  formatArticlesMarkdown,
  formatSyncResultMarkdown,
  getLatestArticles,
  searchArticles,
  syncAccountArticles,
  syncAllAccounts,
  syncMpBackendAccount,
};
