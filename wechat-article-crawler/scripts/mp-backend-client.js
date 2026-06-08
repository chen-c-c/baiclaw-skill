"use strict";

const {
  DEFAULT_RETRY,
  DEFAULT_TIMEOUT_MS,
  MP_APPMSG_URL,
  MP_SEARCH_BIZ_URL,
} = require("./constants");
const { cookieError, requestText } = require("./http-client");
const { decodeHtml, normalizeLink } = require("./parser");
const { randomUserAgent, sleep } = require("./utils");

function mpError(message, payload) {
  const error = new Error(message);
  error.payload = payload;
  return error;
}

function parseJson(text, label) {
  try {
    return JSON.parse(text);
  } catch {
    throw mpError(`Invalid JSON from ${label}: ${String(text || "").slice(0, 160)}`);
  }
}

function baseResp(payload) {
  return payload?.base_resp || payload?.baseResp || {};
}

function assertOk(payload, label) {
  const resp = baseResp(payload);
  const ret = Number(resp.ret || 0);
  if (ret === 0) return;
  const errMsg = String(resp.err_msg || resp.errmsg || payload?.errmsg || "");
  if (ret === 200003 || ret === 200013 || ret === -3 || /login|cookie|token|invalid|session/i.test(errMsg)) {
    throw cookieError(`${label} requires a valid WeChat mp cookie/token: ret=${ret} errmsg=${errMsg}`);
  }
  throw mpError(`${label} failed: ret=${ret} errmsg=${errMsg}`, payload);
}

function normalizeAccountInfo(item) {
  if (!item || typeof item !== "object") return null;
  return {
    alias: String(item.alias || ""),
    fakeid: String(item.fakeid || ""),
    nickname: decodeHtml(item.nickname || ""),
    roundHeadImg: normalizeLink(item.round_head_img || ""),
    serviceType: Number(item.service_type || 0),
    signature: decodeHtml(item.signature || ""),
  };
}

function normalizeAppMsg(item, account = {}) {
  if (!item || typeof item !== "object") return null;
  const title = decodeHtml(item.title || "").trim();
  const link = normalizeLink(item.link || "");
  if (!title || !link) return null;
  return {
    biz: account.biz || account.fakeid || "",
    fakeid: account.fakeid || "",
    title,
    link,
    digest: decodeHtml(item.digest || item.summary || "").trim(),
    cover: normalizeLink(item.cover || item.cover_url || ""),
    publishTime: Number(item.create_time || item.update_time || 0) || 0,
    author: decodeHtml(item.author || item.nickname || account.nickname || "").trim(),
    raw: item,
  };
}

function stripHtml(html) {
  return String(html || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

class WechatMpBackendClient {
  constructor(options) {
    this.cookieManager = options.cookieManager;
    this.log = options.logger;
    this.retry = Number.isFinite(options.retry) ? options.retry : DEFAULT_RETRY;
    this.timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : DEFAULT_TIMEOUT_MS;
  }

  credentials(tokenOverride) {
    const cookie = this.cookieManager.getCookie();
    if (!cookie) {
      throw cookieError(`Missing WeChat cookie. Put {"cookie":"...","token":"..."} in ${this.cookieManager.getCookieFile()}`);
    }
    const token = String(tokenOverride || this.cookieManager.getToken() || "").trim();
    if (!token) {
      throw cookieError(`Missing WeChat mp token. Put {"cookie":"...","token":"..."} in ${this.cookieManager.getCookieFile()}`);
    }
    return { cookie, token };
  }

  headers(cookie, referer = "https://mp.weixin.qq.com/") {
    return {
      Accept: "application/json,text/plain,*/*",
      "Accept-Encoding": "gzip, deflate, br",
      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
      Cookie: cookie,
      Referer: referer,
      "User-Agent": randomUserAgent(),
    };
  }

  async requestJson(url, headers, label) {
    let lastError = null;
    for (let attempt = 0; attempt <= this.retry; attempt += 1) {
      this.log?.info(`GET ${label} attempt=${attempt + 1}`);
      try {
        const response = await requestText(url, headers, this.timeoutMs);
        if (response.status === 429) {
          const err = new Error("HTTP 429 rate limited");
          err.rateLimited = true;
          throw err;
        }
        if (response.status < 200 || response.status >= 300) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = parseJson(response.text, label);
        assertOk(payload, label);
        return payload;
      } catch (error) {
        lastError = error;
        this.log?.warn(`${label} failed attempt=${attempt + 1}: ${error.message}`);
        if (error.cookieInvalid) break;
        if (attempt >= this.retry) break;
        const base = error.rateLimited ? 5000 : 1000;
        await sleep(base * Math.pow(2, attempt));
      }
    }
    throw lastError || new Error(`${label} request failed`);
  }

  async searchBiz(nickname, options = {}) {
    const { cookie, token } = this.credentials(options.token);
    const params = new URLSearchParams({
      action: "search_biz",
      query: String(nickname || ""),
      count: String(options.count || 5),
      lang: "zh_CN",
      f: "json",
      token,
    });
    const payload = await this.requestJson(
      `${MP_SEARCH_BIZ_URL}?${params.toString()}`,
      this.headers(cookie, `https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&token=${encodeURIComponent(token)}&lang=zh_CN`),
      "searchbiz",
    );
    const list = Array.isArray(payload.list) ? payload.list.map(normalizeAccountInfo).filter(Boolean) : [];
    return {
      total: Number(payload.total || list.length || 0),
      list,
      best: list.find((item) => item.nickname === nickname) || list[0] || null,
    };
  }

  async listAppMsg(fakeid, options = {}) {
    const { cookie, token } = this.credentials(options.token);
    const params = new URLSearchParams({
      action: "list_ex",
      begin: String(options.begin || 0),
      count: String(options.count || 5),
      fakeid,
      type: "9",
      query: "",
      token,
      lang: "zh_CN",
      f: "json",
    });
    const payload = await this.requestJson(
      `${MP_APPMSG_URL}?${params.toString()}`,
      this.headers(cookie, `https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg&token=${encodeURIComponent(token)}&lang=zh_CN`),
      "appmsg",
    );
    const account = { fakeid, nickname: options.nickname || "" };
    return {
      count: Number(payload.app_msg_cnt || payload.count || 0),
      list: (Array.isArray(payload.app_msg_list) ? payload.app_msg_list : [])
        .map((item) => normalizeAppMsg(item, account))
        .filter(Boolean),
    };
  }

  async fetchArticleContent(link) {
    const headers = {
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Encoding": "gzip, deflate, br",
      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
      "User-Agent": randomUserAgent(),
    };
    const response = await requestText(link, headers, this.timeoutMs);
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`HTTP ${response.status}`);
    }
    return {
      html: response.text,
      text: stripHtml(response.text),
    };
  }
}

module.exports = { WechatMpBackendClient, normalizeAppMsg, stripHtml };
