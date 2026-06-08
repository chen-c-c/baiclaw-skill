"use strict";

function decodeHtml(value) {
  return String(value || "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCharCode(Number.parseInt(code, 16)));
}

function normalizeLink(value) {
  const link = decodeHtml(value).trim();
  if (!link) return "";
  if (link.startsWith("//")) return `https:${link}`;
  return link;
}

function parseGeneralMessageList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  if (typeof value === "object") return Array.isArray(value.list) ? value.list : [];
  try {
    const parsed = JSON.parse(String(value));
    return Array.isArray(parsed?.list) ? parsed.list : [];
  } catch {
    return [];
  }
}

function articleFromExtInfo(ext, comm, biz) {
  if (!ext || typeof ext !== "object") return null;
  const title = decodeHtml(ext.title || ext.video_title || "").trim();
  const link = normalizeLink(ext.content_url || ext.video_url || ext.source_url || "");
  if (!title || !link) return null;
  return {
    biz,
    title,
    link,
    digest: decodeHtml(ext.digest || ext.video_digest || "").trim(),
    cover: normalizeLink(ext.cover || ext.cover_url || ext.multi_cover || ""),
    publishTime: Number(comm?.datetime || ext.datetime || 0) || 0,
    author: decodeHtml(ext.author || "").trim(),
    raw: { comm_msg_info: comm || {}, app_msg_ext_info: ext },
  };
}

function parseMessageItem(item, biz) {
  const comm = item?.comm_msg_info || {};
  const ext = item?.app_msg_ext_info || item?.appmsg_ext_info || {};
  const articles = [];
  const primary = articleFromExtInfo(ext, comm, biz);
  if (primary) articles.push(primary);

  const multi = Array.isArray(ext.multi_app_msg_item_list)
    ? ext.multi_app_msg_item_list
    : [];
  for (const sub of multi) {
    const article = articleFromExtInfo(sub, comm, biz);
    if (article) articles.push(article);
  }

  const video = item?.video_msg_ext_info || ext?.video_msg_ext_info;
  const videoArticle = articleFromExtInfo(video, comm, biz);
  if (videoArticle) articles.push(videoArticle);

  return articles;
}

function parseProfileExtPayload(payload, biz) {
  const list = parseGeneralMessageList(payload?.general_msg_list);
  const articles = [];
  for (const item of list) {
    articles.push(...parseMessageItem(item, biz));
  }

  const nextOffset = Number(payload?.next_offset || payload?.offset || 0) || 0;
  const canContinue = Number(payload?.can_msg_continue || 0) === 1;
  return {
    articles,
    canContinue,
    nextOffset,
    rawCount: list.length,
  };
}

function keywordMatches(article, keywords) {
  const title = String(article?.title || "");
  const digest = String(article?.digest || "");
  return (keywords || []).filter((keyword) => {
    const k = String(keyword || "").trim();
    return k && (title.includes(k) || digest.includes(k));
  });
}

function findElementById(html, id) {
  const source = String(html || "");
  const openTag = new RegExp(`<([a-z0-9]+)\\b[^>]*\\bid=["']${id}["'][^>]*>`, "i").exec(source);
  if (!openTag) return "";
  const tagName = openTag[1].toLowerCase();
  const start = openTag.index;
  const tagPattern = new RegExp(`<\\/?${tagName}\\b[^>]*>`, "gi");
  tagPattern.lastIndex = start;
  let depth = 0;
  let match;
  while ((match = tagPattern.exec(source))) {
    const tag = match[0];
    const isClose = tag.startsWith("</");
    const isSelfClosing = /\/>$/.test(tag);
    if (isClose) depth -= 1;
    else if (!isSelfClosing) depth += 1;
    if (depth === 0) {
      return source.slice(start, tagPattern.lastIndex);
    }
  }
  return source.slice(start);
}

function findRichContent(html) {
  const byId = findElementById(html, "js_content");
  if (byId) return byId;
  const match = String(html || "").match(/<div\b[^>]*class=["'][^"']*rich_media_content[^"']*["'][^>]*>[\s\S]*<\/div>/i);
  return match ? match[0] : String(html || "");
}

function removeElementsByPattern(html, pattern) {
  let output = String(html || "");
  let previous;
  do {
    previous = output;
    output = output.replace(pattern, "");
  } while (output !== previous);
  return output;
}

function absolutizeAttrUrls(html, attr, baseUrl) {
  return String(html || "").replace(
    new RegExp(`\\b${attr}\\s*=\\s*("([^"]*)"|'([^']*)'|([^\\s>]+))`, "gi"),
    (raw, _all, dq, sq, bare) => {
      const value = dq || sq || bare || "";
      if (!value || /^data:/i.test(value)) return raw;
      try {
        return `${attr}="${new URL(normalizeLink(value), baseUrl).toString()}"`;
      } catch {
        return `${attr}="${normalizeLink(value)}"`;
      }
    },
  );
}

function prepareArticleImages(html, baseUrl) {
  let output = String(html || "");
  output = output.replace(/<img\b[^>]*>/gi, (tag) => {
    const srcMatch = tag.match(/\s(?:data-src|data-original|data-backsrc|src)=("([^"]*)"|'([^']*)'|([^\s>]+))/i);
    const src = srcMatch ? normalizeLink(srcMatch[2] || srcMatch[3] || srcMatch[4] || "") : "";
    let next = tag
      .replace(/\sdata-ratio=("([^"]*)"|'([^']*)'|([^\s>]+))/gi, "")
      .replace(/\sdata-w=("([^"]*)"|'([^']*)'|([^\s>]+))/gi, "")
      .replace(/\sdata-type=("([^"]*)"|'([^']*)'|([^\s>]+))/gi, "");
    next = next.replace(/\ssrc=("([^"]*)"|'([^']*)'|([^\s>]+))/i, "");
    if (src) {
      let absolute = src;
      try {
        absolute = new URL(src, baseUrl).toString();
      } catch {
        // keep original
      }
      next = next.replace(/<img/i, `<img src="${absolute}"`);
    }
    return next;
  });
  output = absolutizeAttrUrls(output, "src", baseUrl);
  output = absolutizeAttrUrls(output, "href", baseUrl);
  return output;
}

function cleanArticleBodyHtml(html, baseUrl) {
  let output = findRichContent(html);
  output = output
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, "")
    .replace(/<svg[\s\S]*?<\/svg>/gi, "");

  output = output
    .replace(/\sstyle=("([^"]*)"|'([^']*)')/gi, (raw, quoted, dq, sq) => {
      const style = String(dq || sq || "")
        .replace(/visibility\s*:\s*hidden;?/gi, "")
        .replace(/opacity\s*:\s*0;?/gi, "")
        .trim();
      return style ? ` style="${style}"` : "";
    })
    .replace(/\s(?:on[a-z]+|data-v-[\w-]+)=("([^"]*)"|'([^']*)'|([^\s>]+))/gi, "")
    .replace(/\sid=["']js_content["']/i, "")
    .replace(/<mp-common-[\s\S]*?<\/mp-common-[^>]+>/gi, "")
    .replace(/<mp[^>]*>/gi, "")
    .replace(/<\/mp[^>]*>/gi, "");

  output = prepareArticleImages(output, baseUrl);
  return output.trim();
}

const TEXT_NOISE_PATTERNS = [
  /^预览时标签不可点$/,
  /^微信扫一扫$/,
  /^关注该公众号$/,
  /^继续滑动看下一个$/,
  /^轻触阅读原文$/,
  /^向上滑动看下一个$/,
  /^知道了$/,
  /^使用小程序$/,
  /^取消$/,
  /^允许$/,
  /^分析$/,
  /^视频$/,
  /^小程序$/,
  /^赞$/,
  /^在看$/,
  /^分享$/,
  /^留言$/,
  /^收藏$/,
  /^听过$/,
  /^×$/,
  /^，+$/,
  /微信扫一扫可打开此内容/,
  /使用完整服务/,
  /轻点两下取消/,
  /在小说阅读器读本章/,
  /去阅读/,
  /关注我们/,
];

function htmlToText(html) {
  let text = String(html || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<(br|p|section|div|h[1-6]|li|tr|blockquote)\b[^>]*>/gi, "\n")
    .replace(/<\/(p|section|div|h[1-6]|li|tr|blockquote)>/gi, "\n")
    .replace(/<img\b[^>]*(?:alt|title)=("([^"]*)"|'([^']*)')[^>]*>/gi, (_, _q, dq, sq) => `\n${dq || sq || ""}\n`)
    .replace(/<[^>]+>/g, " ");
  text = decodeHtml(text)
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]+\n/g, "\n");
  const lines = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !TEXT_NOISE_PATTERNS.some((pattern) => pattern.test(line)));
  return lines.join("\n").trim();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildReadableHtml(article, bodyHtml) {
  const title = article?.title || "微信公众号文章";
  const author = article?.author || article?.nickname || "";
  const publishTime = article?.publishTime
    ? new Date(article.publishTime * 1000).toLocaleString("zh-CN", { hour12: false })
    : "";
  return [
    "<!doctype html>",
    '<html lang="zh-CN">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1">',
    `<title>${escapeHtml(title)}</title>`,
    "<style>",
    "body{margin:0;background:#f7f7f7;color:#1f2329;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;line-height:1.78;}",
    ".page{max-width:760px;margin:0 auto;background:#fff;min-height:100vh;padding:32px 24px 56px;box-sizing:border-box;}",
    "h1{font-size:26px;line-height:1.35;margin:0 0 12px;font-weight:700;}",
    ".meta{color:#6b7280;font-size:14px;margin-bottom:28px;}",
    ".content{font-size:16px;overflow-wrap:anywhere;}",
    ".content img{max-width:100%;height:auto;display:block;margin:12px auto;}",
    ".content video,.content iframe{max-width:100%;display:block;margin:12px auto;}",
    ".content p,.content section{margin:0 0 12px;}",
    "a{color:#2563eb;text-decoration:none;}",
    "</style>",
    "</head>",
    "<body>",
    '<main class="page">',
    `<h1>${escapeHtml(title)}</h1>`,
    `<div class="meta">${[author, publishTime].filter(Boolean).map(escapeHtml).join(" · ")}</div>`,
    `<article class="content">${bodyHtml}</article>`,
    "</main>",
    "</body>",
    "</html>",
  ].join("\n");
}

function extractArticleContent(rawHtml, article = {}) {
  const bodyHtml = cleanArticleBodyHtml(rawHtml, article.link || "https://mp.weixin.qq.com/");
  return {
    bodyHtml,
    html: buildReadableHtml(article, bodyHtml),
    text: htmlToText(bodyHtml),
  };
}

module.exports = {
  decodeHtml,
  extractArticleContent,
  htmlToText,
  keywordMatches,
  normalizeLink,
  parseProfileExtPayload,
};
