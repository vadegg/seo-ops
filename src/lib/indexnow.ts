const DEFAULT_INDEXNOW_ENDPOINT = 'https://api.indexnow.org/indexnow';
const DEFAULT_INDEXNOW_KEY = '129ebf08-3db2-4d2f-bf33-9ea41ef4cc90';

function normalizeBaseUrl(url: string): string {
  return url.replace(/\/+$/, '');
}

function isAllowedKey(value: string): boolean {
  return /^[A-Za-z0-9-]{8,128}$/.test(value);
}

function ensureAbsoluteUrl(value: string, label: string): string {
  try {
    return new URL(value).toString();
  } catch {
    throw new Error(`${label} must be an absolute URL. Received: ${value}`);
  }
}

export interface IndexNowConfig {
  key?: string;
  endpoint?: string;
  keyLocation?: string;
}

export interface SubmitIndexNowUrlOptions {
  siteUrl: string;
  url: string;
  config?: IndexNowConfig;
}

export function getIndexNowConfig(siteUrl: string, config?: IndexNowConfig): IndexNowConfig | undefined {
  const key = config?.key ?? process.env.INDEXNOW_KEY ?? DEFAULT_INDEXNOW_KEY;
  if (!key) {
    return undefined;
  }

  if (!isAllowedKey(key)) {
    throw new Error('INDEXNOW_KEY must be 8-128 characters using only letters, numbers, and hyphens.');
  }

  const normalizedSiteUrl = normalizeBaseUrl(ensureAbsoluteUrl(siteUrl, 'siteUrl'));
  const keyLocation = ensureAbsoluteUrl(
    config?.keyLocation ?? process.env.INDEXNOW_KEY_LOCATION ?? `${normalizedSiteUrl}/${key}.txt`,
    'INDEXNOW_KEY_LOCATION'
  );
  const endpoint = ensureAbsoluteUrl(
    config?.endpoint ?? process.env.INDEXNOW_ENDPOINT ?? DEFAULT_INDEXNOW_ENDPOINT,
    'INDEXNOW_ENDPOINT'
  );

  return {
    key,
    keyLocation,
    endpoint
  };
}

export async function submitIndexNowUrl(options: SubmitIndexNowUrlOptions): Promise<void> {
  const config = getIndexNowConfig(options.siteUrl, options.config);
  if (!config) {
    return;
  }

  const pageUrl = ensureAbsoluteUrl(options.url, 'url');
  const siteHost = new URL(options.siteUrl).host;
  const pageHost = new URL(pageUrl).host;

  if (siteHost !== pageHost) {
    throw new Error(`IndexNow URL host mismatch. Expected ${siteHost}, received ${pageHost}.`);
  }

  const response = await fetch(config.endpoint!, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json; charset=utf-8'
    },
    body: JSON.stringify({
      host: siteHost,
      key: config.key,
      keyLocation: config.keyLocation,
      urlList: [pageUrl]
    })
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`IndexNow submission failed with ${response.status} ${response.statusText}: ${body}`.trim());
  }
}
