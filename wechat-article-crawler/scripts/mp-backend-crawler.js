"use strict";

const { CookieManager } = require("./cookie-manager");
const { downloadMediaAssets, extractMedia } = require("./media");
const { WechatMpBackendClient } = require("./mp-backend-client");
const { extractArticleContent, keywordMatches } = require("./parser");
const { WechatArticleStore } = require("./storage");
const { logger, randomInt, sleep } = require("./utils");

class WechatMpBackendCrawler {
  constructor(config, deps = {}) {
    this.config = config;
    this.log = deps.logger || logger();
    this.cookieManager = deps.cookieManager || new CookieManager({
      cookieFile: config.cookieFile,
      logger: this.log,
    });
    this.client = deps.client || new WechatMpBackendClient({
      cookieManager: this.cookieManager,
      logger: this.log,
      retry: config.retry,
      timeoutMs: config.timeoutMs,
    });
    this.store = deps.store || new WechatArticleStore({ dbPath: config.dbPath });
  }

  async resolveAccount(account, options = {}) {
    if (account.fakeid) return { ...account };
    if (!account.nickname) {
      throw new Error("Missing nickname or fakeid for mp backend sync");
    }
    const result = await this.client.searchBiz(account.nickname, {
      token: options.token,
      count: options.searchCount || 5,
    });
    if (!result.best?.fakeid) {
      throw new Error(`No WeChat Official Account matched nickname=${account.nickname}`);
    }
    return {
      ...account,
      nickname: result.best.nickname || account.nickname,
      fakeid: result.best.fakeid,
      biz: account.biz || result.best.fakeid,
      resolvedAccount: result.best,
    };
  }

  async syncAccount(account, options = {}) {
    const resolved = await this.resolveAccount(account, options);
    if (!Array.isArray(resolved.keywords) || resolved.keywords.length === 0) {
      throw new Error(`Missing keywords for nickname=${resolved.nickname || resolved.fakeid}`);
    }

    const full = Boolean(options.full);
    const maxPages = Number(options.maxPages ?? this.config.maxPages) || 0;
    const count = Math.max(1, Math.min(Number(options.count ?? this.config.count) || 5, 10));
    const stateKey = resolved.biz || resolved.fakeid;
    const state = this.store.getState(stateKey);
    const previousNewest = full ? 0 : Number(state?.last_publish_time || 0);

    let begin = 0;
    let pages = 0;
    let fetched = 0;
    let matched = 0;
    let inserted = 0;
    let updated = 0;
    let newestSeen = previousNewest;
    const matchedArticles = [];

    this.log.info(`mp backend sync started; nickname=${resolved.nickname} fakeid=${resolved.fakeid} full=${full}`);

    while (true) {
      if (maxPages > 0 && pages >= maxPages) break;
      const page = await this.client.listAppMsg(resolved.fakeid, {
        token: options.token,
        begin,
        count,
        nickname: resolved.nickname,
      });
      pages += 1;
      fetched += page.list.length;

      const newArticles = page.list.filter((article) => {
        if (!article.publishTime || full) return true;
        return article.publishTime > previousNewest;
      });

      const filtered = newArticles
        .map((article) => ({
          ...article,
          biz: stateKey,
          nickname: resolved.nickname || "",
          matchedKeywords: keywordMatches(article, resolved.keywords),
        }))
        .filter((article) => article.matchedKeywords.length > 0);

      for (const article of filtered) {
        if (options.fetchBody !== false) {
          try {
            const content = await this.client.fetchArticleContent(article.link);
            const articleContent = extractArticleContent(content.html, article);
            article.contentHtml = articleContent.html;
            article.contentText = articleContent.text;
            article.media = extractMedia(articleContent.bodyHtml, article.link);
            if (article.cover) {
              article.media.unshift({
                type: "image",
                url: article.cover,
                alt: "cover",
                order: 0,
              });
            }
            if (options.downloadMedia !== false) {
              const assets = await downloadMediaAssets(article, article.media, {
                cookie: this.cookieManager.getCookie(),
                outputDir: options.outputDir || this.config.outputDir,
                timeoutMs: this.config.timeoutMs,
              });
              article.media = assets.media;
              article.assetDir = assets.assetDir;
              article.articleDir = assets.articleDir;
            }
          } catch (error) {
            this.log?.warn(`fetch article content failed title=${article.title}: ${error.message}`);
          }
        }
      }

      if (filtered.length) {
        const result = this.store.upsertArticles(filtered, resolved);
        inserted += result.inserted;
        updated += result.updated;
        matched += filtered.length;
        matchedArticles.push(...filtered);
      }

      for (const article of page.list) {
        newestSeen = Math.max(newestSeen, Number(article.publishTime || 0));
      }

      const pageOldest = Math.min(...page.list.map((article) => Number(article.publishTime || 0)).filter(Boolean));
      if (!page.list.length || page.list.length < count) break;
      if (!full && previousNewest > 0 && pageOldest > 0 && pageOldest <= previousNewest) break;

      begin += count;
      const [minDelay, maxDelay] = this.config.requestDelay || [1000, 3000];
      await sleep(randomInt(minDelay, maxDelay));
    }

    this.store.updateState({
      biz: stateKey,
      nickname: resolved.nickname,
      lastPublishTime: newestSeen,
      lastOffset: begin,
    });

    const result = {
      source: "mp-backend",
      nickname: resolved.nickname || "",
      biz: stateKey,
      fakeid: resolved.fakeid,
      keywords: resolved.keywords,
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
      `mp backend sync complete; fakeid=${resolved.fakeid} pages=${pages} fetched=${fetched} matched=${matched} inserted=${inserted} updated=${updated}`,
    );
    return result;
  }

  close() {
    this.store.close();
  }
}

module.exports = { WechatMpBackendCrawler };
