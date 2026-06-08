"use strict";

const http = require("http");
const https = require("https");
const zlib = require("zlib");

const {
  DEFAULT_COUNT,
  DEFAULT_RETRY,
  DEFAULT_TIMEOUT_MS,
  PROFILE_EXT_URL,
} = require("./constants");
const { randomUserAgent, sleep } = require("./utils");

function decompress(buffer, encoding) {
  const enc = String(encoding || "").toLowerCase();
  try {
    if (enc.includes("br")) return zlib.brotliDecompressSync(buffer);
    if (enc.includes("gzip")) return zlib.gunzipSync(buffer);
    if (enc.includes("deflate")) return zlib.inflateSync(buffer);
  } catch {
    return buffer;
  }
  return buffer;
}

function resolveRedirectUrl(currentUrl, location) {
  try {
    return new URL(location, currentUrl).toString();
  } catch {
    return "";
  }
}

function requestBody(url, headers, timeoutMs, redirectLimit = 5) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const client = target.protocol === "http:" ? http : https;
    const req = client.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || undefined,
        path: target.pathname + target.search,
        method: "GET",
        headers,
      },
      (res) => {
        const redirect = res.headers.location && res.statusCode >= 300 && res.statusCode < 400
          ? resolveRedirectUrl(url, res.headers.location)
          : "";
        if (redirect && redirectLimit > 0) {
          res.resume();
          resolve(requestBody(redirect, headers, timeoutMs, redirectLimit - 1));
          return;
        }
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          resolve({
            status: res.statusCode || 0,
            headers: res.headers,
            buffer: Buffer.concat(chunks),
            url,
          });
        });
      }
    );
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("timeout"));
    });
    req.end();
  });
}

async function requestText(url, headers, timeoutMs) {
  const response = await requestBody(url, headers, timeoutMs);
  const body = decompress(response.buffer, response.headers["content-encoding"]);
  return {
    status: response.status,
    headers: response.headers,
    text: body.toString("utf8"),
    url: response.url,
  };
}

async function requestBuffer(url, headers, timeoutMs) {
  const response = await requestBody(url, headers, timeoutMs);
  const body = decompress(response.buffer, response.headers["content-encoding"]);
  return {
    status: response.status,
    headers: response.headers,
    buffer: body,
    url: response.url,
  };
}

function cookieError(message) {
  const error = new Error(message);
  error.cookieInvalid = true;
  return error;
}

class WechatHttpClient {
  constructor(options) {
    this.cookieManager = options.cookieManager;
    this.log = options.logger;
    this.retry = Number.isFinite(options.retry) ? options.retry : DEFAULT_RETRY;
    this.timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : DEFAULT_TIMEOUT_MS;
  }

  buildHistoryUrl(params) {
    const search = new URLSearchParams({
      action: "getmsg",
      __biz: params.biz,
      offset: String(params.offset || 0),
      count: String(params.count || DEFAULT_COUNT),
      f: "json",
    });
    return `${PROFILE_EXT_URL}?${search.toString()}`;
  }

  async getHistoryPage({ biz, offset = 0, count = DEFAULT_COUNT }) {
    const url = this.buildHistoryUrl({ biz, offset, count });
    let lastError = null;

    for (let attempt = 0; attempt <= this.retry; attempt += 1) {
      const cookie = this.cookieManager.getCookie();
      if (!cookie) {
        throw cookieError(`Missing WeChat cookie. Put {"cookie":"..."} in ${this.cookieManager.getCookieFile()}`);
      }

      const headers = {
        Accept: "application/json,text/plain,*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        Cookie: cookie,
        Referer: `https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=${encodeURIComponent(biz)}`,
        "User-Agent": randomUserAgent(),
      };

      this.log?.info(`GET profile_ext biz=${biz} offset=${offset} attempt=${attempt + 1}`);
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

        let payload;
        try {
          payload = JSON.parse(response.text);
        } catch {
          throw new Error(`Invalid JSON response: ${response.text.slice(0, 160)}`);
        }

        if (this.cookieManager.isInvalidResponse(payload, response.text)) {
          throw cookieError(`Cookie invalid or verification required: ret=${payload.ret ?? "unknown"} errmsg=${payload.errmsg || ""}`);
        }

        if (Number(payload.ret || 0) !== 0) {
          throw new Error(`WeChat profile_ext ret=${payload.ret} errmsg=${payload.errmsg || ""}`);
        }

        return payload;
      } catch (error) {
        lastError = error;
        const isLast = attempt >= this.retry;
        this.log?.warn(`profile_ext request failed biz=${biz} offset=${offset} attempt=${attempt + 1}: ${error.message}`);
        if (error.cookieInvalid) break;
        if (isLast) break;
        const base = error.rateLimited ? 5000 : 1000;
        await sleep(base * Math.pow(2, attempt));
      }
    }

    throw lastError || new Error("profile_ext request failed");
  }
}

module.exports = { WechatHttpClient, cookieError, requestBuffer, requestText };
