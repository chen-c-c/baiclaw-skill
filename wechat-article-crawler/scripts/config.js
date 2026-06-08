"use strict";

const fs = require("fs");

const {
  CONFIG_KEY,
  DEFAULT_COUNT,
  DEFAULT_INTERVAL_MINUTES,
  DEFAULT_MAX_CONCURRENCY,
  DEFAULT_MAX_PAGES,
  DEFAULT_REQUEST_DELAY,
  DEFAULT_RETRY,
  DEFAULT_TIMEOUT_MS,
} = require("./constants");
const {
  extractBiz,
  parseBoolOption,
  parseIntOption,
  readJsonFile,
  resolveCookieFile,
  resolveDbPath,
  resolveOutputDir,
  splitList,
  uniq,
} = require("./utils");

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

function openReadonlyDatabase(dbPath) {
  try {
    const Database = require("better-sqlite3");
    return new Database(dbPath, { readonly: true, fileMustExist: true });
  } catch {
    try {
      const { DatabaseSync } = requireNodeSqlite();
      return new DatabaseSync(dbPath, { readOnly: true });
    } catch {
      return null;
    }
  }
}

function normalizeDelay(value) {
  if (Array.isArray(value) && value.length >= 2) {
    return [
      Math.max(0, Number(value[0]) || DEFAULT_REQUEST_DELAY[0]),
      Math.max(0, Number(value[1]) || DEFAULT_REQUEST_DELAY[1]),
    ];
  }
  if (typeof value === "string" && value.includes(",")) {
    const parts = value.split(",").map((item) => Number(item.trim()));
    return normalizeDelay(parts);
  }
  return DEFAULT_REQUEST_DELAY.slice();
}

function normalizeAccount(input = {}) {
  const biz = extractBiz(input.biz || input.profileUrl || input.url || input.link);
  const keywords = uniq(splitList(input.keywords || input.keyword));
  return {
    nickname: String(input.nickname || input.account || input.name || "").trim(),
    biz,
    keywords,
    enabled: input.enabled === undefined ? true : Boolean(input.enabled),
  };
}

function mergeAccountList(...sources) {
  const byBiz = new Map();
  for (const source of sources) {
    for (const account of source || []) {
      const normalized = normalizeAccount(account);
      if (!normalized.biz) continue;
      const previous = byBiz.get(normalized.biz) || {};
      byBiz.set(normalized.biz, {
        ...previous,
        ...normalized,
        keywords: uniq([...(previous.keywords || []), ...normalized.keywords]),
      });
    }
  }
  return Array.from(byBiz.values());
}

function normalizeConfig(input = {}) {
  return {
    interval: Math.max(1, Number(input.interval) || DEFAULT_INTERVAL_MINUTES),
    maxConcurrency: Math.max(1, Number(input.maxConcurrency) || DEFAULT_MAX_CONCURRENCY),
    requestDelay: normalizeDelay(input.requestDelay),
    retry: Math.max(0, Number(input.retry) || DEFAULT_RETRY),
    cookieFile: resolveCookieFile(input.cookieFile),
    dbPath: resolveDbPath(input.dbPath),
    outputDir: resolveOutputDir(input.outputDir),
    count: Math.max(1, Math.min(Number(input.count) || DEFAULT_COUNT, 10)),
    timeoutMs: Math.max(1000, Number(input.timeoutMs) || DEFAULT_TIMEOUT_MS),
    maxPages: Math.max(0, Number(input.maxPages) || DEFAULT_MAX_PAGES),
    accounts: mergeAccountList(input.accounts),
  };
}

function readSqliteConfig(dbPath) {
  if (!fs.existsSync(dbPath)) return {};

  let db;
  try {
    db = openReadonlyDatabase(dbPath);
    if (!db) return {};
    const rows = db.prepare("SELECT key, value FROM kv WHERE key IN (?, ?)").all(CONFIG_KEY, "app_config");
    const result = {};
    for (const row of rows) {
      let parsed;
      try {
        parsed = JSON.parse(row.value);
      } catch {
        parsed = undefined;
      }
      if (row.key === CONFIG_KEY && parsed && typeof parsed === "object") {
        Object.assign(result, parsed);
      }
      if (row.key === "app_config" && parsed?.[CONFIG_KEY] && typeof parsed[CONFIG_KEY] === "object") {
        Object.assign(result, parsed[CONFIG_KEY]);
      }
    }
    return result;
  } catch {
    return {};
  } finally {
    try {
      db?.close();
    } catch {
      // ignore close errors
    }
  }
}

function readEnvConfig() {
  const raw = process.env.WECHAT_CRAWLER_CONFIG;
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function readFileConfig(filePath) {
  if (!filePath) return {};
  return readJsonFile(filePath, {});
}

function buildCliConfig(options = {}) {
  const account = normalizeAccount({
    nickname: options.nickname || options.account,
    biz: options.biz || options.profileUrl || options.url,
    keywords: options.keywords || options.keyword,
    enabled: true,
  });
  const accounts = account.biz ? [account] : [];
  return {
    ...(options.interval ? { interval: parseIntOption(options.interval, DEFAULT_INTERVAL_MINUTES) } : {}),
    ...(options.maxConcurrency ? { maxConcurrency: parseIntOption(options.maxConcurrency, DEFAULT_MAX_CONCURRENCY) } : {}),
    ...(options.requestDelay ? { requestDelay: options.requestDelay } : {}),
    ...(options.retry ? { retry: parseIntOption(options.retry, DEFAULT_RETRY) } : {}),
    ...(options.cookieFile ? { cookieFile: options.cookieFile } : {}),
    ...(options.dbPath ? { dbPath: options.dbPath } : {}),
    ...(options.outputDir ? { outputDir: options.outputDir } : {}),
    ...(options.count ? { count: parseIntOption(options.count, DEFAULT_COUNT) } : {}),
    ...(options.timeoutMs ? { timeoutMs: parseIntOption(options.timeoutMs, DEFAULT_TIMEOUT_MS) } : {}),
    ...(options.maxPages ? { maxPages: parseIntOption(options.maxPages, DEFAULT_MAX_PAGES) } : {}),
    ...(accounts.length ? { accounts } : {}),
  };
}

function mergeConfig(base, next) {
  const merged = {
    ...base,
    ...next,
    accounts: mergeAccountList(base.accounts, next.accounts),
  };
  return normalizeConfig(merged);
}

function loadCrawlerConfig(options = {}) {
  const dbPath = resolveDbPath(options.dbPath);
  const defaults = normalizeConfig({ dbPath });
  const envConfig = normalizeConfig(readEnvConfig());
  const sqliteConfig = normalizeConfig(readSqliteConfig(dbPath));
  const fileConfig = normalizeConfig(readFileConfig(options.config));
  const cliConfig = normalizeConfig(buildCliConfig(options));

  let merged = defaults;
  for (const part of [envConfig, sqliteConfig, fileConfig, cliConfig]) {
    merged = mergeConfig(merged, part);
  }

  if (options.enabled !== undefined) {
    const enabled = parseBoolOption(options.enabled, true);
    merged.accounts = merged.accounts.map((account) => ({ ...account, enabled }));
  }

  return merged;
}

module.exports = {
  buildCliConfig,
  loadCrawlerConfig,
  normalizeAccount,
  normalizeConfig,
};
