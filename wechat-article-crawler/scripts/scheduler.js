"use strict";

const path = require("path");

function buildWechatArticleSyncJob(config = {}) {
  const scriptPath = path.resolve(__dirname, "index.js");
  const intervalMinutes = Math.max(1, Number(config.interval) || 30);
  return {
    name: "wechatArticleSyncJob",
    description: "Sync WeChat Official Account history articles via mp.weixin.qq.com profile_ext.",
    enabled: true,
    schedule: {
      kind: "every",
      everyMs: intervalMinutes * 60 * 1000,
    },
    sessionTarget: "isolated",
    wakeMode: "now",
    payload: {
      kind: "agentTurn",
      timeoutSeconds: 900,
      message: [
        "使用 wechat-article-crawler skill 同步所有已启用公众号文章。",
        "运行以下命令，并把 Markdown 结果展示给用户：",
        `node "${scriptPath}" sync-all --format markdown`,
      ].join("\n"),
    },
    delivery: {
      mode: "none",
    },
    agentId: "main",
    sessionKey: "wechatArticleSyncJob",
  };
}

module.exports = { buildWechatArticleSyncJob };
