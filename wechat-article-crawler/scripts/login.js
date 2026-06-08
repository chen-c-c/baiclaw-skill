"use strict";

const { spawn, spawnSync } = require("child_process");
const http = require("http");
const fs = require("fs");
const net = require("net");
const path = require("path");

const { CookieManager } = require("./cookie-manager");
const { ensureDir, getUserDataDir, sleep } = require("./utils");

function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on("error", reject);
    req.setTimeout(3000, () => req.destroy(new Error("timeout")));
  });
}

function commandExists(command) {
  const where = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(where, [command], { encoding: "utf8" });
  return result.status === 0 ? result.stdout.split(/\r?\n/).find(Boolean) : "";
}

function findBrowserPath() {
  const envCandidates = [
    process.env.CHROME_PATH,
    process.env.EDGE_PATH,
  ].filter(Boolean);
  const windowsCandidates = process.platform === "win32" ? [
    path.join(process.env.PROGRAMFILES || "C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe"),
    path.join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)", "Google", "Chrome", "Application", "chrome.exe"),
    path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
    path.join(process.env.PROGRAMFILES || "C:\\Program Files", "Microsoft", "Edge", "Application", "msedge.exe"),
    path.join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)", "Microsoft", "Edge", "Application", "msedge.exe"),
  ] : [];
  const commandCandidates = process.platform === "win32"
    ? ["chrome.exe", "msedge.exe"]
    : ["google-chrome", "chromium", "chromium-browser", "microsoft-edge"];

  for (const candidate of [...envCandidates, ...windowsCandidates]) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  for (const command of commandCandidates) {
    const found = commandExists(command);
    if (found) return found;
  }
  return "";
}

async function waitForDebugPort(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await httpGetJson(`http://127.0.0.1:${port}/json/version`);
      return true;
    } catch {
      await sleep(500);
    }
  }
  return false;
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

async function pickDebugPort(preferredPort) {
  const preferred = Number(preferredPort || 0);
  if (preferred > 0) return preferred;
  if (await isPortFree(9223)) return 9223;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const candidate = 9300 + Math.floor(Math.random() * 500);
    if (await isPortFree(candidate)) return candidate;
  }
  return 9223;
}

function tokenFromUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return parsed.searchParams.get("token") || "";
  } catch {
    const match = String(rawUrl || "").match(/[?&]token=(\d+)/);
    return match ? match[1] : "";
  }
}

async function cdpCall(webSocketUrl, method, params = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(webSocketUrl);
    const id = 1;
    const timer = setTimeout(() => {
      try {
        ws.close();
      } catch {
        // ignore
      }
      reject(new Error(`CDP ${method} timeout`));
    }, 5000);

    ws.addEventListener("open", () => {
      ws.send(JSON.stringify({ id, method, params }));
    });
    ws.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload.id !== id) return;
      clearTimeout(timer);
      ws.close();
      if (payload.error) reject(new Error(payload.error.message || `CDP ${method} failed`));
      else resolve(payload.result || {});
    });
    ws.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error(`CDP ${method} connection failed`));
    });
  });
}

function cookieString(cookies) {
  const filtered = (cookies || [])
    .filter((cookie) => /(^|\.)mp\.weixin\.qq\.com$/i.test(cookie.domain || ""))
    .filter((cookie) => cookie.name && cookie.value)
    .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  return filtered.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

async function readBrowserSession(port) {
  const pages = await httpGetJson(`http://127.0.0.1:${port}/json`);
  const mpPages = (Array.isArray(pages) ? pages : [])
    .filter((page) => page.type === "page" && /mp\.weixin\.qq\.com/i.test(page.url || ""))
    .filter((page) => page.webSocketDebuggerUrl);

  let token = "";
  for (const page of mpPages) {
    token = tokenFromUrl(page.url);
    if (token) break;
  }

  const page = mpPages[0];
  if (!page) return { token, cookie: "" };
  let result;
  try {
    result = await cdpCall(page.webSocketDebuggerUrl, "Network.getAllCookies");
  } catch {
    result = await cdpCall(page.webSocketDebuggerUrl, "Storage.getCookies");
  }
  return {
    token,
    cookie: cookieString(result.cookies || []),
  };
}

async function loginWechat(options = {}) {
  const port = await pickDebugPort(options.port);
  const timeoutMs = Math.max(30_000, Number(options.timeoutMs || options.timeout || 180_000));
  const profileDir = options.profileDir
    ? path.resolve(options.profileDir)
    : path.join(getUserDataDir(), "storage", "wechat-login-profile");
  const browserPath = options.browserPath || findBrowserPath();
  if (!browserPath) {
    throw new Error("Chrome or Edge was not found. Set CHROME_PATH or EDGE_PATH and retry.");
  }

  ensureDir(profileDir);
  const loginUrl = "https://mp.weixin.qq.com/";
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--new-window",
    loginUrl,
  ];

  const child = spawn(browserPath, args, {
    detached: true,
    stdio: "ignore",
  });
  child.unref();

  const ready = await waitForDebugPort(port, 15_000);
  if (!ready) {
    throw new Error(`Browser DevTools port ${port} did not become ready.`);
  }

  const manager = new CookieManager({ cookieFile: options.cookieFile });
  const deadline = Date.now() + timeoutMs;
  let lastSession = { cookie: "", token: "" };
  while (Date.now() < deadline) {
    lastSession = await readBrowserSession(port);
    if (lastSession.cookie && lastSession.token) {
      manager.setCookie(lastSession.cookie);
      manager.setToken(lastSession.token);
      return {
        cookieFile: manager.getCookieFile(),
        cookieLength: lastSession.cookie.length,
        tokenCaptured: true,
        tokenLength: lastSession.token.length,
        profileDir,
        debugPort: port,
      };
    }
    await sleep(1000);
  }

  throw new Error(
    `Login timed out. Last state: cookie=${Boolean(lastSession.cookie)} token=${Boolean(lastSession.token)}.`,
  );
}

function formatLoginMarkdown(result) {
  return [
    "## WeChat login captured",
    "",
    `- Cookie file: ${result.cookieFile}`,
    `- Cookie length: ${result.cookieLength}`,
    `- Token length: ${result.tokenLength}`,
    `- Browser profile: ${result.profileDir}`,
    "",
    "You can now run `mp-sync` without manually pasting Cookie or token.",
  ].join("\n");
}

module.exports = {
  findBrowserPath,
  formatLoginMarkdown,
  loginWechat,
};
