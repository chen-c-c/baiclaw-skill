"use strict";

const fs = require("fs");
const path = require("path");

const { requestBuffer } = require("./http-client");
const { decodeHtml, normalizeLink } = require("./parser");
const {
  ensureDir,
  randomUserAgent,
  resolveOutputDir,
  safeFilename,
  sha1,
} = require("./utils");

function attrMap(tag) {
  const attrs = {};
  const pattern = /([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g;
  let match;
  while ((match = pattern.exec(tag))) {
    attrs[match[1].toLowerCase()] = decodeHtml(match[2] || match[3] || match[4] || "");
  }
  return attrs;
}

function isUsableRawUrl(raw) {
  const value = String(raw || "").trim();
  if (!value) return false;
  if (/^(javascript|data):/i.test(value)) return false;
  if (/[<>{}]/.test(value)) return false;
  if (/\bconcat\s*\(|\$\{|['"]\)\./i.test(value)) return false;
  if (/\s/.test(value)) return false;
  return true;
}

function resolveUrl(raw, baseUrl) {
  if (!isUsableRawUrl(raw)) return "";
  const value = normalizeLink(raw);
  if (!value) return "";
  try {
    return new URL(value, baseUrl).toString();
  } catch {
    return value;
  }
}

function firstUrl(attrs, names, baseUrl) {
  for (const name of names) {
    const value = attrs[name];
    const resolved = resolveUrl(value, baseUrl);
    if (resolved) return resolved;
  }
  return "";
}

function inferExt(url, contentType = "") {
  const type = String(contentType || "").toLowerCase();
  if (type.includes("jpeg")) return ".jpg";
  if (type.includes("png")) return ".png";
  if (type.includes("webp")) return ".webp";
  if (type.includes("gif")) return ".gif";
  if (type.includes("mp4")) return ".mp4";
  if (type.includes("mpeg")) return ".mp3";
  try {
    const pathname = new URL(url).pathname;
    const ext = path.extname(pathname).toLowerCase();
    if (/^\.(jpg|jpeg|png|webp|gif|bmp|svg|mp4|mov|m4v|mp3|wav|m4a)$/.test(ext)) {
      return ext === ".jpeg" ? ".jpg" : ext;
    }
  } catch {
    // fall through
  }
  return "";
}

function dedupeMedia(items) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const key = `${item.type}:${item.url}`;
    if (!item.url || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function extractMedia(html, baseUrl) {
  const source = String(html || "");
  const media = [];
  let order = 0;

  for (const match of source.matchAll(/<img\b[^>]*>/gi)) {
    const attrs = attrMap(match[0]);
    const url = firstUrl(attrs, ["data-src", "data-original", "data-backsrc", "src"], baseUrl);
    if (!url || /^data:/i.test(url)) continue;
    media.push({
      type: "image",
      url,
      alt: attrs.alt || attrs.title || "",
      order: order += 1,
    });
  }

  for (const match of source.matchAll(/<video\b[^>]*>/gi)) {
    const attrs = attrMap(match[0]);
    const url = firstUrl(attrs, ["data-src", "src"], baseUrl);
    const poster = firstUrl(attrs, ["poster", "data-poster"], baseUrl);
    if (url || poster) {
      media.push({
        type: "video",
        url: url || poster,
        poster,
        order: order += 1,
      });
    }
  }

  for (const match of source.matchAll(/<source\b[^>]*>/gi)) {
    const attrs = attrMap(match[0]);
    const url = firstUrl(attrs, ["data-src", "src"], baseUrl);
    if (!url) continue;
    const sourceType = String(attrs.type || "").toLowerCase();
    media.push({
      type: sourceType.includes("audio") ? "audio" : "video",
      url,
      order: order += 1,
    });
  }

  for (const match of source.matchAll(/<iframe\b[^>]*>/gi)) {
    const attrs = attrMap(match[0]);
    const url = firstUrl(attrs, ["data-src", "src"], baseUrl);
    if (!url) continue;
    media.push({
      type: /video|readtemplate|vid=|mpvideo/i.test(url) ? "video" : "iframe",
      url,
      order: order += 1,
    });
  }

  for (const match of source.matchAll(/url\((['"]?)([^'")]+)\1\)/gi)) {
    if (!/^(https?:|\/\/|\/|\.{1,2}\/)/i.test(String(match[2] || "").trim())) continue;
    const url = resolveUrl(match[2], baseUrl);
    if (!url || /^data:/i.test(url)) continue;
    media.push({
      type: "image",
      url,
      order: order += 1,
    });
  }

  return dedupeMedia(media);
}

function shouldDownload(media) {
  if (media.type === "image" || media.type === "audio") return true;
  const ext = inferExt(media.url);
  return media.type === "video" && [".mp4", ".mov", ".m4v"].includes(ext);
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function htmlAttrEscape(value) {
  return String(value || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function relativeAssetPath(articleDir, filePath) {
  return path.relative(articleDir, filePath).replace(/\\/g, "/");
}

function replaceMediaUrls(html, media, articleDir) {
  let output = String(html || "");
  for (const item of media || []) {
    if (item.status !== "saved" || !item.filePath || !item.url) continue;
    const localPath = relativeAssetPath(articleDir, item.filePath);
    const variants = Array.from(new Set([
      item.url,
      htmlAttrEscape(item.url),
      encodeURI(item.url),
      htmlAttrEscape(encodeURI(item.url)),
    ]));
    for (const variant of variants) {
      if (!variant) continue;
      output = output.replace(new RegExp(escapeRegExp(variant), "g"), localPath);
    }
  }
  return output;
}

async function downloadMediaAssets(article, media, options = {}) {
  const outputRoot = resolveOutputDir(options.outputDir);
  const articleId = sha1(article.link || article.title);
  const articleDir = path.join(outputRoot, "articles", `${articleId}-${safeFilename(article.title, "article")}`);
  const assetDir = path.join(articleDir, "assets");
  ensureDir(assetDir);

  const downloaded = [];
  for (const item of dedupeMedia(media || [])) {
    if (!shouldDownload(item)) {
      downloaded.push({ ...item, status: "linked" });
      continue;
    }
    const assetId = sha1(item.url);
    try {
      const response = await requestBuffer(
        item.url,
        {
          Accept: "*/*",
          "Accept-Encoding": "gzip, deflate, br",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
          ...(options.cookie ? { Cookie: options.cookie } : {}),
          Referer: article.link || "https://mp.weixin.qq.com/",
          "User-Agent": randomUserAgent(),
        },
        options.timeoutMs || 20000,
      );
      if (response.status < 200 || response.status >= 300) {
        throw new Error(`HTTP ${response.status}`);
      }
      const ext = inferExt(item.url, response.headers["content-type"]) || ".bin";
      const filePath = path.join(assetDir, `${assetId}${ext}`);
      fs.writeFileSync(filePath, response.buffer);
      downloaded.push({
        ...item,
        status: "saved",
        filePath,
        bytes: response.buffer.length,
        contentType: response.headers["content-type"] || "",
      });
    } catch (error) {
      downloaded.push({
        ...item,
        status: "failed",
        error: error.message,
      });
    }
  }

  const manifest = {
    article: {
      title: article.title,
      link: article.link,
      digest: article.digest || "",
      publishTime: article.publishTime || 0,
    },
    assetDir,
    media: downloaded,
  };
  if (article.contentHtml) {
    article.contentHtml = replaceMediaUrls(article.contentHtml, downloaded, articleDir);
    fs.writeFileSync(path.join(articleDir, "content.html"), article.contentHtml, "utf8");
  }
  if (article.contentText) {
    fs.writeFileSync(path.join(articleDir, "content.txt"), article.contentText, "utf8");
  }
  fs.writeFileSync(path.join(articleDir, "article.json"), `${JSON.stringify({
    title: article.title,
    link: article.link,
    digest: article.digest || "",
    cover: article.cover || "",
    publishTime: article.publishTime || 0,
    author: article.author || "",
    matchedKeywords: article.matchedKeywords || [],
    assetDir,
  }, null, 2)}\n`, "utf8");
  fs.writeFileSync(path.join(articleDir, "media.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return {
    articleDir,
    assetDir,
    media: downloaded,
  };
}

module.exports = {
  downloadMediaAssets,
  extractMedia,
  inferExt,
};
