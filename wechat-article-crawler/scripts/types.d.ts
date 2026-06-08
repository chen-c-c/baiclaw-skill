export interface WechatCrawlerAccount {
  nickname: string;
  biz: string;
  fakeid?: string;
  keywords: string[];
  enabled: boolean;
}

export interface WechatCrawlerConfig {
  interval: number;
  maxConcurrency: number;
  requestDelay: [number, number];
  retry: number;
  cookieFile: string;
  dbPath: string;
  outputDir: string;
  count: number;
  timeoutMs: number;
  maxPages: number;
  accounts: WechatCrawlerAccount[];
}

export interface WechatArticle {
  id?: string;
  biz: string;
  title: string;
  link: string;
  digest: string;
  cover: string;
  publishTime: number;
  author: string;
  contentHtml?: string;
  contentText?: string;
  media?: Array<{
    type: string;
    url: string;
    filePath?: string;
    status?: string;
    poster?: string;
    alt?: string;
  }>;
  assetDir?: string;
  articleDir?: string;
  nickname?: string;
  matchedKeywords?: string[];
  raw?: unknown;
}

export interface WechatAccountSyncResult {
  nickname: string;
  biz: string;
  fakeid?: string;
  keywords: string[];
  pages: number;
  fetched: number;
  matched: number;
  inserted: number;
  updated: number;
  previousNewest: number;
  newestSeen: number;
  articles: WechatArticle[];
}

export interface WechatAllSyncResult {
  accounts: WechatAccountSyncResult[];
  summary: {
    fetched: number;
    matched: number;
    inserted: number;
    updated: number;
    pages: number;
  };
}
