"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  DEFAULT_COOKIE_REL_PATH,
  DEFAULT_DB_FILENAME,
  DEFAULT_OUTPUT_DIR,
  LOG_PREFIX,
  USER_AGENTS,
} = require("./constants");

function logger(scope = LOG_PREFIX) {
  const format = (level, args) => {
    const ts = new Date().toISOString();
    return [ts, scope, level, ...args];
  };
  return {
    debug: (...args) => console.error(...format("DEBUG", args)),
    info: (...args) => console.error(...format("INFO", args)),
    warn: (...args) => console.error(...format("WARN", args)),
    error: (...args) => console.error(...format("ERROR", args)),
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(min, max) {
  const lo = Math.ceil(Math.min(min, max));
  const hi = Math.floor(Math.max(min, max));
  return Math.floor(Math.random() * (hi - lo + 1)) + lo;
}

function randomUserAgent() {
  return USER_AGENTS[randomInt(0, USER_AGENTS.length - 1)];
}

function sha1(value) {
  return crypto.createHash("sha1").update(String(value || "")).digest("hex");
}

function safeFilename(value, fallback = "item") {
  const name = String(value || "")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return name || fallback;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function readJsonFile(filePath, fallback = undefined) {
  try {
    const raw = fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, "");
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeJsonFile(filePath, payload) {
  ensureDir(path.dirname(filePath));
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, filePath);
}

function getProjectRoot() {
  return path.resolve(__dirname, "..", "..", "..");
}

function looksLikeProjectRoot(dir) {
  return Boolean(
    dir &&
    fs.existsSync(path.join(dir, "package.json")) &&
    fs.existsSync(path.join(dir, "SKILLs")),
  );
}

function findProjectRootFrom(startDir) {
  let current = path.resolve(startDir || ".");
  while (current && current !== path.dirname(current)) {
    if (looksLikeProjectRoot(current)) return current;
    current = path.dirname(current);
  }
  return "";
}

function readProjectRootMarker() {
  const marker = path.resolve(__dirname, "..", ".project-root");
  try {
    const value = fs.readFileSync(marker, "utf8").trim();
    return value ? path.resolve(value) : "";
  } catch {
    return "";
  }
}

function getOutputRootDir() {
  if (process.env.WECHAT_CRAWLER_PROJECT_ROOT) return path.resolve(process.env.WECHAT_CRAWLER_PROJECT_ROOT);
  if (process.env.BAICLAW_PROJECT_ROOT) return path.resolve(process.env.BAICLAW_PROJECT_ROOT);
  const markerRoot = readProjectRootMarker();
  if (markerRoot) return markerRoot;
  return (
    findProjectRootFrom(process.cwd()) ||
    findProjectRootFrom(__dirname) ||
    getProjectRoot()
  );
}

function getUserDataDirFromSkillPath() {
  let current = path.resolve(__dirname);
  while (current && current !== path.dirname(current)) {
    if (path.basename(current).toLowerCase() === "skills") {
      const userData = path.dirname(current);
      if (!fs.existsSync(path.join(userData, "package.json"))) {
        return userData;
      }
    }
    current = path.dirname(current);
  }
  return "";
}

function findExistingUserDataDir(baseDir) {
  const names = ["BaiClaw", "baiclaw", "baizAI", "baizAI-dev", "BaiClaw Dev", "LobsterAI"];
  for (const name of names) {
    const candidate = path.join(baseDir, name);
    if (
      fs.existsSync(path.join(candidate, "SKILLs", "skills.config.json")) ||
      fs.existsSync(path.join(candidate, "SKILLs", "wechat-article-crawler"))
    ) {
      return candidate;
    }
  }
  return "";
}

function getUserDataDir() {
  if (process.env.BAICLAW_USER_DATA_DIR) return path.resolve(process.env.BAICLAW_USER_DATA_DIR);
  const fromSkillPath = getUserDataDirFromSkillPath();
  if (fromSkillPath) return fromSkillPath;
  if (process.platform === "win32") {
    const appdata = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
    return findExistingUserDataDir(appdata) || path.join(appdata, "BaiClaw");
  }
  if (process.platform === "darwin") {
    const appSupport = path.join(os.homedir(), "Library", "Application Support");
    return findExistingUserDataDir(appSupport) || path.join(appSupport, "BaiClaw");
  }
  const xdg = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return findExistingUserDataDir(xdg) || path.join(xdg, "BaiClaw");
}

function resolveDbPath(input) {
  if (input) return path.resolve(input);
  if (process.env.BAICLAW_DB_PATH) return path.resolve(process.env.BAICLAW_DB_PATH);
  const userData = getUserDataDir();
  for (const filename of [DEFAULT_DB_FILENAME, "lobsterai.sqlite"]) {
    const candidate = path.join(userData, filename);
    if (fs.existsSync(candidate)) return candidate;
  }
  return path.join(userData, DEFAULT_DB_FILENAME);
}

function resolveCookieFile(input) {
  if (input) {
    return path.isAbsolute(input) ? path.resolve(input) : path.join(getUserDataDir(), input);
  }
  if (process.env.WECHAT_CRAWLER_COOKIE_FILE) {
    const fromEnv = process.env.WECHAT_CRAWLER_COOKIE_FILE;
    return path.isAbsolute(fromEnv) ? path.resolve(fromEnv) : path.join(getUserDataDir(), fromEnv);
  }
  return path.join(getUserDataDir(), ...DEFAULT_COOKIE_REL_PATH);
}

function resolveOutputDir(input) {
  if (process.env.WECHAT_CRAWLER_OUTPUT_DIR) {
    const fromEnv = process.env.WECHAT_CRAWLER_OUTPUT_DIR;
    return path.isAbsolute(fromEnv) ? path.resolve(fromEnv) : path.join(getOutputRootDir(), fromEnv);
  }
  if (input) return path.isAbsolute(input) ? path.resolve(input) : path.join(getOutputRootDir(), input);
  return path.join(getOutputRootDir(), DEFAULT_OUTPUT_DIR);
}

function normalizeKeyword(value) {
  return String(value || "").trim();
}

function splitList(value) {
  if (Array.isArray(value)) return value.flatMap(splitList);
  if (value == null) return [];
  return String(value)
    .split(/[,\uFF0C;\n\r]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniq(values) {
  return Array.from(new Set(values.map((value) => String(value).trim()).filter(Boolean)));
}

function extractBiz(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw);
    return url.searchParams.get("__biz") || raw;
  } catch {
    const match = raw.match(/__biz=([^&#\s]+)/);
    return match ? decodeURIComponent(match[1]) : raw;
  }
}

function parseIntOption(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseBoolOption(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  const normalized = String(value).toLowerCase();
  return ["1", "true", "yes", "y", "on"].includes(normalized);
}

function formatMarkdownTable(rows, headers) {
  const escape = (value) => String(value ?? "").replace(/\r?\n/g, " ").replace(/\|/g, "\\|").trim();
  return [
    `| ${headers.map(escape).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.map(escape).join(" | ")} |`),
  ].join("\n");
}

async function promisePool(items, limit, worker) {
  const results = [];
  let index = 0;
  const concurrency = Math.max(1, Math.min(limit || 1, items.length || 1));
  async function runWorker() {
    while (index < items.length) {
      const currentIndex = index;
      index += 1;
      results[currentIndex] = await worker(items[currentIndex], currentIndex);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, runWorker));
  return results;
}

module.exports = {
  ensureDir,
  extractBiz,
  formatMarkdownTable,
  getOutputRootDir,
  getProjectRoot,
  getUserDataDir,
  logger,
  normalizeKeyword,
  parseBoolOption,
  parseIntOption,
  promisePool,
  randomInt,
  randomUserAgent,
  readJsonFile,
  resolveCookieFile,
  resolveDbPath,
  resolveOutputDir,
  safeFilename,
  sha1,
  sleep,
  splitList,
  uniq,
  writeJsonFile,
};
