"use strict";

const { DEFAULT_COUNT } = require("./constants");
const { CookieManager } = require("./cookie-manager");
const { WechatHttpClient } = require("./http-client");
const { keywordMatches, parseProfileExtPayload } = require("./parser");
const { WechatArticleStore } = require("./storage");
const { logger, promisePool, randomInt, sleep } = require("./utils");

class WechatArticleCrawler {
  constructor(config, deps = {}) {
    this.config = config;
    this.log = deps.logger || logger();
    this.cookieManager = deps.cookieManager || new CookieManager({
      cookieFile: config.cookieFile,
      logger: this.log,
    });
    this.http = deps.http || new WechatHttpClient({
      cookieManager: this.cookieManager,
      logger: this.log,
      retry: config.retry,
      timeoutMs: config.timeoutMs,
    });
    this.store = deps.store || new WechatArticleStore({ dbPath: config.dbPath });
  }

  async syncAll(options = {}) {
    const accounts = (this.config.accounts || []).filter((account) => account.enabled !== false);
    this.log.info(`sync all accounts started; enabled=${accounts.length}`);
    const results = await promisePool(
      accounts,
      this.config.maxConcurrency,
      (account) => this.syncAccount(account, options),
    );
    const summary = results.reduce(
      (acc, item) => {
        acc.fetched += item.fetched;
        acc.matched += item.matched;
        acc.inserted += item.inserted;
        acc.updated += item.updated;
        acc.pages += item.pages;
        return acc;
      },
      { fetched: 0, matched: 0, inserted: 0, updated: 0, pages: 0 },
    );
    this.log.info(
      `sync all complete; accounts=${results.length} fetched=${summary.fetched} matched=${summary.matched} inserted=${summary.inserted} updated=${summary.updated}`,
    );
    return { accounts: results, summary };
  }

  async syncAccount(account, options = {}) {
    if (!account?.biz) throw new Error("Missing account biz");
    if (!Array.isArray(account.keywords) || account.keywords.length === 0) {
      throw new Error(`Missing keywords for biz=${account.biz}`);
    }

    const full = Boolean(options.full);
    const maxPages = Number(options.maxPages ?? this.config.maxPages) || 0;
    const count = Math.min(Number(options.count ?? this.config.count) || DEFAULT_COUNT, DEFAULT_COUNT);
    const state = this.store.getState(account.biz);
    const previousNewest = full ? 0 : Number(state?.last_publish_time || 0);

    let offset = 0;
    let pages = 0;
    let fetched = 0;
    let matched = 0;
    let inserted = 0;
    let updated = 0;
    let newestSeen = previousNewest;
    let lastOffset = 0;
    const matchedArticles = [];

    this.log.info(`sync account started; nickname=${account.nickname || "-"} biz=${account.biz} full=${full}`);

    while (true) {
      if (maxPages > 0 && pages >= maxPages) break;
      const payload = await this.http.getHistoryPage({ biz: account.biz, offset, count });
      const page = parseProfileExtPayload(payload, account.biz);
      pages += 1;
      fetched += page.articles.length;

      const newArticles = page.articles.filter((article) => {
        if (!article.publishTime || full) return true;
        return article.publishTime > previousNewest;
      });
      const filtered = newArticles
        .map((article) => ({
          ...article,
          nickname: account.nickname || "",
          matchedKeywords: keywordMatches(article, account.keywords),
        }))
        .filter((article) => article.matchedKeywords.length > 0);

      if (filtered.length) {
        const result = this.store.upsertArticles(filtered, account);
        inserted += result.inserted;
        updated += result.updated;
        matched += filtered.length;
        matchedArticles.push(...filtered);
      }

      for (const article of page.articles) {
        newestSeen = Math.max(newestSeen, Number(article.publishTime || 0));
      }

      const pageNewest = Math.max(0, ...page.articles.map((article) => Number(article.publishTime || 0)));
      lastOffset = page.nextOffset || offset + count;

      if (!page.canContinue || page.articles.length === 0) break;
      if (!full && previousNewest > 0 && pageNewest > 0 && pageNewest <= previousNewest) break;

      offset = page.nextOffset > offset ? page.nextOffset : offset + count;
      const [minDelay, maxDelay] = this.config.requestDelay || [1000, 3000];
      await sleep(randomInt(minDelay, maxDelay));
    }

    this.store.updateState({
      biz: account.biz,
      nickname: account.nickname,
      lastPublishTime: newestSeen,
      lastOffset,
    });

    const result = {
      nickname: account.nickname || "",
      biz: account.biz,
      keywords: account.keywords,
      pages,
      fetched,
      matched,
      inserted,
      updated,
      previousNewest,
      newestSeen,
      articles: matchedArticles,
    };
    this.log.info(
      `sync account complete; biz=${account.biz} pages=${pages} fetched=${fetched} matched=${matched} inserted=${inserted} updated=${updated}`,
    );
    return result;
  }

  close() {
    this.store.close();
  }
}

module.exports = { WechatArticleCrawler };
