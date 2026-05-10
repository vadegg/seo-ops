import fs from 'node:fs';
import path from 'node:path';
import { chromium, type BrowserContext, type Page } from 'playwright';
import { waitForEnter } from '../../lib/cli.js';
import type { VolumeResult } from '../../types/index.js';

function getChromeExecutablePath(): string | undefined {
  const candidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium'
  ];

  return candidates.find((candidate) => fs.existsSync(candidate));
}

async function waitForGoogleAdsReady(page: Page): Promise<void> {
  const readySignals = [
    /keyword planner/i,
    /get search volume and forecasts/i,
    /discover new keywords/i,
    /start with keywords/i
  ];

  for (let attempt = 0; attempt < 6; attempt += 1) {
    const bodyText = (await page.textContent('body').catch(() => '')) ?? '';

    if (readySignals.some((signal) => signal.test(bodyText))) {
      return;
    }

    if (/not secure|try using a different browser|sign in again/i.test(bodyText)) {
      throw new Error(
        'Google Ads blocked the current browser session as unsupported. Open Keyword Planner manually in the same Chrome profile once, then rerun keyword:agent:resume.'
      );
    }

    await page.waitForTimeout(1500);
  }
}

async function getPage(context: BrowserContext): Promise<Page> {
  const page = context.pages()[0] ?? (await context.newPage());
  await page.bringToFront();
  return page;
}

async function findKeywordPlannerResults(page: Page, keywords: string[]): Promise<boolean> {
  const content = (await page.textContent('body'))?.toLowerCase() ?? '';
  return keywords.some((keyword) => content.includes(keyword.toLowerCase()));
}

async function tryAutoFill(page: Page, keywords: string[]): Promise<boolean> {
  const joined = keywords.join('\n');

  const candidateSelectors = ['textarea', '[role="textbox"]', 'input[type="text"]'];

  for (const selector of candidateSelectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count()) === 0) {
      continue;
    }

    try {
      await locator.click({ timeout: 1500 });
      await locator.fill(joined, { timeout: 1500 });
      const button = page.getByText(/get results|see results|get started|forecast/i).first();
      if ((await button.count()) > 0) {
        await button.click({ timeout: 1500 });
      }
      await page.waitForTimeout(3000);
      return true;
    } catch {
      // Continue trying other selectors.
    }
  }

  return false;
}

function parseVolumeValue(value: string): number | null {
  const cleaned = value.replace(/,/g, '').trim();
  if (!cleaned) {
    return null;
  }

  const rangeMatch = cleaned.match(/(\d+(?:\.\d+)?)\s*([KM])?/i);
  if (!rangeMatch) {
    return null;
  }

  const base = Number(rangeMatch[1]);
  const suffix = rangeMatch[2]?.toUpperCase();

  if (suffix === 'K') {
    return Math.round(base * 1000);
  }
  if (suffix === 'M') {
    return Math.round(base * 1000000);
  }

  return Math.round(base);
}

function parseAverageFromRange(range: string): number | null {
  const matches = [...range.matchAll(/(\d+(?:\.\d+)?)\s*([KM])?/gi)];
  if (matches.length === 0) {
    return parseVolumeValue(range);
  }

  const values = matches.map((match) => parseVolumeValue(match[0])).filter((value): value is number => value !== null);
  if (values.length === 0) {
    return null;
  }

  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}

function rowToVolume(keyword: string, rowText: string): VolumeResult {
  const rangeMatch = rowText.match(/(\d+(?:\.\d+)?\s*[KM]?\s*-\s*\d+(?:\.\d+)?\s*[KM]?|\d+(?:\.\d+)?\s*[KM]?)/i);
  const changeMatches = rowText.match(/[+-]?\d+%/g) ?? [];
  const volumeRangeRaw = rangeMatch?.[1]?.replace(/\s+/g, ' ').trim() ?? null;

  return {
    keyword,
    volume_avg_monthly: volumeRangeRaw ? parseAverageFromRange(volumeRangeRaw) : null,
    volume_range_raw: volumeRangeRaw,
    three_month_change: changeMatches[0] ?? null,
    yoy_change: changeMatches[1] ?? null,
    source: 'keyword_planner_browser',
    status: volumeRangeRaw ? 'ok' : 'missing'
  };
}

async function parseResults(page: Page, keywords: string[]): Promise<VolumeResult[]> {
  const rows = await page.locator('table tr').allInnerTexts().catch(() => []);
  const pageText = rows.join('\n');

  return keywords.map((keyword) => {
    const row = rows.find((candidate) => candidate.toLowerCase().includes(keyword.toLowerCase()));
    if (row) {
      return rowToVolume(keyword, row);
    }

    const line = pageText
      .split('\n')
      .find((candidate) => candidate.toLowerCase().includes(keyword.toLowerCase()));
    if (line) {
      return rowToVolume(keyword, line);
    }

    return {
      keyword,
      volume_avg_monthly: null,
      volume_range_raw: null,
      three_month_change: null,
      yoy_change: null,
      source: 'keyword_planner_browser',
      status: 'missing',
      notes: 'Keyword row not found in current Keyword Planner view.'
    };
  });
}

export async function collectKeywordPlannerVolumes(
  keywords: string[],
  artifactDir: string
): Promise<VolumeResult[]> {
  const userDataDir = path.join(artifactDir, 'browser-profile');
  fs.mkdirSync(userDataDir, { recursive: true });

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    executablePath: getChromeExecutablePath(),
    args: ['--start-maximized', '--disable-dev-shm-usage']
  });

  try {
    const page = await getPage(context);
    await page.goto('https://ads.google.com/aw/keywordplanner/home', { waitUntil: 'domcontentloaded' });
    await waitForGoogleAdsReady(page);

    await waitForEnter(
      'A Chrome window is open for Google Ads Keyword Planner. If you are not logged in, log in now. If Google Ads needs account or consent setup, finish it once in this browser profile.'
    );

    const foundResults = await findKeywordPlannerResults(page, keywords);
    if (!foundResults) {
      const autoFilled = await tryAutoFill(page, keywords);

      if (!autoFilled || !(await findKeywordPlannerResults(page, keywords))) {
        await waitForEnter(
          `The tool could not reliably navigate Keyword Planner by itself.\nOpen a Keyword Planner results page for this batch in the browser and make sure these keywords are visible:\n${keywords.join(
            ', '
          )}`
        );
      }
    }

    await page.screenshot({ path: path.join(artifactDir, `keyword-planner-${Date.now()}.png`), fullPage: true });
    return await parseResults(page, keywords);
  } finally {
    await context.close();
  }
}
