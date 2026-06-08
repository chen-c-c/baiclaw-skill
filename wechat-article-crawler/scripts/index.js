#!/usr/bin/env node
"use strict";

const { CookieManager } = require("./cookie-manager");
const { loadCrawlerConfig } = require("./config");
const { formatLoginMarkdown, loginWechat } = require("./login");
const { buildWechatArticleSyncJob } = require("./scheduler");
const {
  formatArticlesMarkdown,
  formatSyncResultMarkdown,
  getLatestArticles,
  searchArticles,
  syncAccountArticles,
  syncAllAccounts,
  syncMpBackendAccount,
} = require("./service");

function isCookieActionError(error) {
  const message = String(error?.message || error || "");
  return (
    message.includes("Missing WeChat cookie") ||
    message.includes("Missing WeChat mp token") ||
    message.includes("valid WeChat mp cookie/token") ||
    message.includes("Cookie invalid") ||
    message.includes("invalid session") ||
    message.includes("verification required") ||
    message.includes("ret=200013") ||
    message.includes("参数错误")
  );
}

function isAccountConfigError(error) {
  const message = String(error?.message || error || "");
  return message.includes("No enabled account config found") || message.includes("Missing keywords");
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("-")) {
      args._.push(token);
      continue;
    }
    const key = token.replace(/^-+/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    if (!next || next.startsWith("-")) {
      args[key] = true;
    } else if (args[key] !== undefined) {
      args[key] = Array.isArray(args[key]) ? [...args[key], next] : [args[key], next];
      i += 1;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function print(payload, format) {
  if (format === "json") {
    process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
    return;
  }
  process.stdout.write(`${payload}\n`);
}

function formatCookieHelp(config, reason = "") {
  return [
    "## WeChat login required",
    "",
    ...(reason ? [`- Reason: ${reason}`, ""] : []),
    `- Cookie file: ${config.cookieFile}`,
    "",
    "推荐：让 Skill 打开微信公众平台登录页，用户扫码登录后自动保存 Cookie 和 token。",
    "",
    "```bash",
    'node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" login --format markdown',
    "```",
    "",
    "兜底：也可以手动复制 mp.weixin.qq.com 后台请求里的 Cookie 和 token。",
    "",
    "```bash",
    'node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" cookie --set "full Cookie header" --format markdown',
    'node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" cookie --set-token "token from backend URL" --format markdown',
    "```",
    "",
    "不要扫描文章页弹出的二维码，那个二维码只是在微信里打开文章，不能生成后台 Cookie，容易出现参数错误。",
  ].join("\n");
}

function formatAccountConfigHelp(config, reason = "") {
  return [
    "## Account config required",
    "",
    ...(reason ? [`- Reason: ${reason}`, ""] : []),
    `- Cookie file: ${config.cookieFile}`,
    "",
    "Run a one-off sync like this:",
    "",
    "```bash",
    'node "$SKILLS_ROOT/wechat-article-crawler/scripts/index.js" mp-sync \\',
    '  --nickname "Official Account Name" \\',
    '  --keywords "keyword1,keyword2" \\',
    "  --format markdown",
    "```",
  ].join("\n");
}

function formatCookieStatusMarkdown(status) {
  return [
    "## WeChat credential status",
    "",
    `- Cookie file: ${status.cookieFile}`,
    `- Cookie configured: ${status.present ? "yes" : "no"}`,
    `- Cookie length: ${status.length}`,
    `- Backend token configured: ${status.tokenPresent ? "yes" : "no"}`,
    `- Backend token length: ${status.tokenLength || 0}`,
  ].join("\n");
}

function commandCanAutoLogin(command) {
  return ["sync", "sync-all", "mp-sync"].includes(command);
}

function autoLoginDisabled(args) {
  return args.noAutoLogin === true || String(args.autoLogin || "").toLowerCase() === "false";
}

function loginOptionsFromArgs(args, config) {
  return {
    browserPath: args.browserPath,
    cookieFile: config.cookieFile,
    port: args.loginPort || args.port,
    profileDir: args.loginProfileDir || args.profileDir,
    timeoutMs: args.loginTimeoutMs || args.loginTimeout || 180000,
  };
}

function formatAutoLoginNotice(error) {
  return [
    "## 微信登录已失效，正在重新登录",
    "",
    `- 原因：${error.message || error}`,
    "- 已打开微信公众平台登录页，请在浏览器里扫码登录，也可以用账号密码登录。",
    "- 登录成功后，Skill 会自动读取新的 Cookie/token，并继续执行刚才的采集任务。",
  ].join("\n");
}

function handleCookieCommand(args, format) {
  const config = loadCrawlerConfig(args);
  const manager = new CookieManager({ cookieFile: config.cookieFile });
  if (args.clear) {
    const status = manager.clearCookie();
    print(format === "json" ? status : formatCookieStatusMarkdown(status), format);
    return;
  }
  let changed = false;
  if (args.set) {
    const raw = Array.isArray(args.set) ? args.set.join("; ") : args.set;
    manager.setCookie(raw);
    changed = true;
  }
  if (args.setToken) {
    const raw = Array.isArray(args.setToken) ? args.setToken[args.setToken.length - 1] : args.setToken;
    manager.setToken(raw);
    changed = true;
  }
  const status = manager.getStatus();
  print(format === "json" ? status : formatCookieStatusMarkdown(status), format);
  return changed;
}

async function runCommand(args, command, format) {
  if (command === "mcp") {
    await require("./mcp-server").runMcpServer();
    return;
  }

  if (command === "print-config") {
    print(loadCrawlerConfig(args), "json");
    return;
  }

  if (command === "schedule") {
    print(buildWechatArticleSyncJob(loadCrawlerConfig(args)), "json");
    return;
  }

  if (command === "cookie") {
    handleCookieCommand(args, format);
    return;
  }

  if (command === "login") {
    const config = loadCrawlerConfig(args);
    const result = await loginWechat(loginOptionsFromArgs(args, config));
    print(format === "json" ? result : formatLoginMarkdown(result), format);
    return;
  }

  if (command === "sync") {
    const result = await syncAccountArticles(args.biz, {
      ...args,
      full: Boolean(args.full),
    });
    print(format === "json" ? result : formatSyncResultMarkdown(result), format);
    return;
  }

  if (command === "sync-all") {
    const result = await syncAllAccounts({
      ...args,
      full: Boolean(args.full),
    });
    print(format === "json" ? result : formatSyncResultMarkdown(result), format);
    return;
  }

  if (command === "mp-sync") {
    const result = await syncMpBackendAccount({
      ...args,
      full: Boolean(args.full),
      noFetchBody: Boolean(args.noFetchBody),
      downloadMedia: args.noDownloadMedia !== true,
    });
    print(format === "json" ? result : formatSyncResultMarkdown(result), format);
    return;
  }

  if (command === "search") {
    const keyword = args.keyword || args.keywords || args._[1];
    const articles = searchArticles(keyword, args);
    print(format === "json" ? articles : formatArticlesMarkdown(`Search: ${keyword || ""}`, articles), format);
    return;
  }

  if (command === "latest") {
    const articles = getLatestArticles(args.biz, args);
    print(format === "json" ? articles : formatArticlesMarkdown("Latest articles", articles), format);
    return;
  }

  throw new Error(`Unknown command: ${command}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0] || (args.biz ? "sync" : "sync-all");
  const format = args.format || "markdown";

  try {
    await runCommand(args, command, format);
  } catch (error) {
    if (isCookieActionError(error) && commandCanAutoLogin(command) && !autoLoginDisabled(args)) {
      const config = loadCrawlerConfig(args);
      if (format !== "json") {
        print(formatAutoLoginNotice(error), format);
      }
      await loginWechat(loginOptionsFromArgs(args, config));
      if (format !== "json") {
        print("## 登录完成，继续执行原采集任务", format);
      }
      await runCommand(args, command, format);
      return;
    }
    throw error;
  }
}

if (require.main === module) {
  main().catch((error) => {
    const args = parseArgs(process.argv.slice(2));
    const format = args.format || "markdown";
    if (isCookieActionError(error)) {
      print(format === "json"
        ? { requiresAction: "wechatCookie", error: error.message, config: loadCrawlerConfig(args) }
        : formatCookieHelp(loadCrawlerConfig(args), error.message), format);
      process.exitCode = 0;
      return;
    }
    if (isAccountConfigError(error)) {
      print(format === "json"
        ? { requiresAction: "wechatCrawlerConfig", error: error.message, config: loadCrawlerConfig(args) }
        : formatAccountConfigHelp(loadCrawlerConfig(args), error.message), format);
      process.exitCode = 0;
      return;
    }
    console.error(`[WechatCrawler] ERROR ${error.message || error}`);
    process.exitCode = 1;
  });
}

module.exports = {
  getLatestArticles,
  searchArticles,
  syncAccountArticles,
  syncAllAccounts,
  syncMpBackendAccount,
};
