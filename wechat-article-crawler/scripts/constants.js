"use strict";

const PROFILE_EXT_URL = "https://mp.weixin.qq.com/mp/profile_ext";
const MP_SEARCH_BIZ_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz";
const MP_APPMSG_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg";
const DEFAULT_COUNT = 10;
const DEFAULT_INTERVAL_MINUTES = 30;
const DEFAULT_MAX_CONCURRENCY = 1;
const DEFAULT_REQUEST_DELAY = [1200, 3200];
const DEFAULT_RETRY = 3;
const DEFAULT_TIMEOUT_MS = 20000;
const DEFAULT_MAX_PAGES = 0;
const DEFAULT_OUTPUT_DIR = "data/wechat-article-crawler";
const DEFAULT_COOKIE_REL_PATH = ["storage", "cookies", "wechat.json"];
const DEFAULT_DB_FILENAME = "baiclaw.sqlite";
const CONFIG_KEY = "wechatCrawler";
const LOG_PREFIX = "[WechatCrawler]";

const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN",
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49",
];

module.exports = {
  CONFIG_KEY,
  DEFAULT_COOKIE_REL_PATH,
  DEFAULT_COUNT,
  DEFAULT_DB_FILENAME,
  DEFAULT_INTERVAL_MINUTES,
  DEFAULT_MAX_CONCURRENCY,
  DEFAULT_MAX_PAGES,
  DEFAULT_OUTPUT_DIR,
  DEFAULT_REQUEST_DELAY,
  DEFAULT_RETRY,
  DEFAULT_TIMEOUT_MS,
  LOG_PREFIX,
  MP_APPMSG_URL,
  MP_SEARCH_BIZ_URL,
  PROFILE_EXT_URL,
  USER_AGENTS,
};
