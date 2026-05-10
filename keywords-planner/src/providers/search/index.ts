import * as cheerio from 'cheerio';
import type { SearchResultEntry } from '../../types/index.js';

async function fetchHtml(url: string): Promise<string> {
  const response = await fetch(url, {
    headers: {
      'user-agent':
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36'
    }
  });

  if (!response.ok) {
    throw new Error(`Search request failed: ${response.status}`);
  }

  return response.text();
}

function normalizeDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

function parseBingResults(html: string): SearchResultEntry[] {
  const $ = cheerio.load(html);
  return $('li.b_algo')
    .slice(0, 10)
    .map((_, element) => {
      const link = $(element).find('h2 a').first();
      const title = link.text().trim();
      const url = link.attr('href') ?? '';
      const snippet = $(element).find('.b_caption p').text().trim();
      return {
        title,
        url,
        snippet,
        domain: normalizeDomain(url)
      };
    })
    .get()
    .filter((entry) => entry.title && entry.url && !entry.domain.includes('bing.com'));
}

function parseGoogleResults(html: string): SearchResultEntry[] {
  const $ = cheerio.load(html);
  return $('div.g')
    .slice(0, 10)
    .map((_, element) => {
      const link = $(element).find('a').first();
      const title = $(element).find('h3').first().text().trim();
      const url = link.attr('href') ?? '';
      const snippet = $(element).find('div[data-sncf="1"], .VwiC3b').first().text().trim();
      return {
        title,
        url,
        snippet,
        domain: normalizeDomain(url)
      };
    })
    .get()
    .filter((entry) => entry.title && entry.url.startsWith('http') && !entry.domain.includes('google.com'));
}

function parseDuckDuckGoResults(html: string): SearchResultEntry[] {
  const $ = cheerio.load(html);
  return $('div.result')
    .slice(0, 10)
    .map((_, element) => {
      const link = $(element).find('a.result__a').first();
      const title = link.text().trim();
      const url = link.attr('href') ?? '';
      const snippet = $(element).find('.result__snippet').text().trim();
      return {
        title,
        url,
        snippet,
        domain: normalizeDomain(url)
      };
    })
    .get()
    .filter((entry) => entry.title && entry.url && !entry.domain.includes('duckduckgo.com'));
}

export async function fetchSearchResults(keyword: string): Promise<SearchResultEntry[]> {
  const query = encodeURIComponent(keyword);

  const attempts: Array<() => Promise<SearchResultEntry[]>> = [
    async () => parseBingResults(await fetchHtml(`https://www.bing.com/search?q=${query}&setlang=en`)),
    async () => parseGoogleResults(await fetchHtml(`https://www.google.com/search?hl=en&q=${query}&num=10`)),
    async () => parseDuckDuckGoResults(await fetchHtml(`https://html.duckduckgo.com/html/?q=${query}`))
  ];

  for (const attempt of attempts) {
    try {
      const results = await attempt();
      if (results.length > 0) {
        return results;
      }
    } catch {
      // Try the next engine.
    }
  }

  return [];
}
