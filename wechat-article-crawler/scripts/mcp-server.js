"use strict";

const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = require("zod");

const {
  getLatestArticles,
  searchArticles,
  syncAccountArticles,
  syncAllAccounts,
  syncMpBackendAccount,
} = require("./service");

function jsonText(payload) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, 2),
      },
    ],
  };
}

async function runMcpServer() {
  const server = new McpServer({
    name: "wechat-article-crawler",
    version: "1.0.0",
  });

  server.tool(
    "syncAccountArticles",
    "Sync one configured WeChat Official Account by biz via mp.weixin.qq.com/mp/profile_ext.",
    {
      biz: z.string().describe("WeChat Official Account __biz"),
      full: z.boolean().optional().describe("Ignore incremental cursor and scan from offset 0"),
      maxPages: z.number().optional().describe("Optional page cap for this run"),
    },
    async ({ biz, full, maxPages }) => jsonText(await syncAccountArticles(biz, { full, maxPages })),
  );

  server.tool(
    "syncAllAccounts",
    "Sync all enabled accounts in wechatCrawler config.",
    {
      full: z.boolean().optional(),
      maxPages: z.number().optional(),
    },
    async ({ full, maxPages }) => jsonText(await syncAllAccounts({ full, maxPages })),
  );

  server.tool(
    "syncMpBackendAccount",
    "Sync one WeChat Official Account through mp.weixin.qq.com/cgi-bin/searchbiz and appmsg APIs. Requires cookie and token.",
    {
      nickname: z.string().optional(),
      fakeid: z.string().optional(),
      keywords: z.union([z.string(), z.array(z.string())]),
      token: z.string().optional(),
      full: z.boolean().optional(),
      maxPages: z.number().optional(),
      noFetchBody: z.boolean().optional(),
    },
    async (args) => jsonText(await syncMpBackendAccount(args)),
  );

  server.tool(
    "searchArticles",
    "Search locally stored WeChat articles by title or digest.",
    {
      keyword: z.string(),
      biz: z.string().optional(),
      limit: z.number().optional(),
    },
    async ({ keyword, biz, limit }) => jsonText(searchArticles(keyword, { biz, limit })),
  );

  server.tool(
    "getLatestArticles",
    "Read latest locally stored WeChat articles.",
    {
      biz: z.string().optional(),
      limit: z.number().optional(),
    },
    async ({ biz, limit }) => jsonText(getLatestArticles(biz, { limit })),
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

module.exports = { runMcpServer };
