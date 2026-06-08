"use strict";

const fs = require("fs");
const path = require("path");

const { ensureDir, readJsonFile, resolveCookieFile, writeJsonFile } = require("./utils");

class CookieManager {
  constructor(options = {}) {
    this.cookieFile = resolveCookieFile(options.cookieFile);
    this.log = options.logger;
    this.cookie = "";
    this.token = "";
    this.lastMtimeMs = 0;
    this.lastLoadAt = 0;
  }

  getCookieFile() {
    return this.cookieFile;
  }

  ensureCookieFile() {
    ensureDir(path.dirname(this.cookieFile));
    if (!fs.existsSync(this.cookieFile)) {
      fs.writeFileSync(this.cookieFile, `${JSON.stringify({ cookie: "" }, null, 2)}\n`, "utf8");
    }
  }

  reloadIfNeeded(force = false) {
    this.ensureCookieFile();
    const stat = fs.statSync(this.cookieFile);
    if (!force && stat.mtimeMs === this.lastMtimeMs && this.cookie) {
      return this.cookie;
    }
    const payload = readJsonFile(this.cookieFile, {});
    const nextCookie = String(payload?.cookie || "").trim();
    const nextToken = String(payload?.token || process.env.WECHAT_MP_TOKEN || "").trim();
    this.cookie = nextCookie;
    this.token = nextToken;
    this.lastMtimeMs = stat.mtimeMs;
    this.lastLoadAt = Date.now();
    if (this.log) {
      this.log.info(`Cookie loaded from ${this.cookieFile}; present=${Boolean(nextCookie)}`);
    }
    return this.cookie;
  }

  getCookie() {
    return this.reloadIfNeeded(false);
  }

  getToken() {
    this.reloadIfNeeded(false);
    return this.token;
  }

  setCookie(cookie) {
    const nextCookie = String(cookie || "").trim();
    this.ensureCookieFile();
    const previous = readJsonFile(this.cookieFile, {});
    writeJsonFile(this.cookieFile, { ...previous, cookie: nextCookie });
    this.cookie = nextCookie;
    const stat = fs.statSync(this.cookieFile);
    this.lastMtimeMs = stat.mtimeMs;
    this.lastLoadAt = Date.now();
    if (this.log) {
      this.log.info(`Cookie saved to ${this.cookieFile}; present=${Boolean(nextCookie)}`);
    }
    return {
      cookieFile: this.cookieFile,
      present: Boolean(nextCookie),
      length: nextCookie.length,
    };
  }

  setToken(token) {
    const nextToken = String(token || "").trim();
    this.ensureCookieFile();
    const previous = readJsonFile(this.cookieFile, {});
    writeJsonFile(this.cookieFile, { ...previous, token: nextToken });
    this.token = nextToken;
    const stat = fs.statSync(this.cookieFile);
    this.lastMtimeMs = stat.mtimeMs;
    this.lastLoadAt = Date.now();
    if (this.log) {
      this.log.info(`Token saved to ${this.cookieFile}; present=${Boolean(nextToken)}`);
    }
    return {
      cookieFile: this.cookieFile,
      present: Boolean(nextToken),
      length: nextToken.length,
    };
  }

  clearCookie() {
    return this.setCookie("");
  }

  getStatus() {
    const cookie = this.reloadIfNeeded(true);
    return {
      cookieFile: this.cookieFile,
      present: Boolean(cookie),
      length: cookie.length,
      tokenPresent: Boolean(this.token),
      tokenLength: this.token.length,
      lastLoadAt: this.lastLoadAt,
    };
  }

  isMissing() {
    return !this.getCookie();
  }

  isInvalidResponse(payload, text = "") {
    const ret = payload && typeof payload === "object" ? Number(payload.ret) : 0;
    const errmsg = payload && typeof payload === "object" ? String(payload.errmsg || "") : "";
    const haystack = `${errmsg}\n${text}`.toLowerCase();
    return (
      ret === 200003 ||
      ret === 200013 ||
      ret === -3 ||
      haystack.includes("cookie") ||
      haystack.includes("login") ||
      haystack.includes("登录") ||
      haystack.includes("验证") ||
      haystack.includes("wappoc_appmsgcaptcha")
    );
  }
}

module.exports = { CookieManager };
