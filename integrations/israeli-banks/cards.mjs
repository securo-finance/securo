// Credit-card companies that log in with credentials (no QR option): the
// library's own scrapers, unchanged. Credentials come from the Securo backend
// for one request and are never logged or written anywhere here.
import puppeteer from 'puppeteer';
import { createScraper, CompanyTypes } from 'israeli-bank-scrapers';
import { normalizeAccounts } from './normalize.mjs';

export const CARD_COMPANIES = {
  max: { id: CompanyTypes.max, fields: ['username', 'password'] },
  // Isracard is behind Cloudflare: open its login page first and let the
  // browser check finish, so the library's own visit is let straight through.
  isracard: {
    id: CompanyTypes.isracard,
    fields: ['id', 'card6Digits', 'password'],
    warmupUrl: 'https://digital.isracard.co.il/personalarea/Login',
  },
};

export class ScrapeError extends Error {
  constructor(errorType, message) {
    super(message || errorType);
    this.errorType = errorType;
  }
}

// Cloudflare answers a first visit with a "checking your browser" page (403)
// that clears itself after a few seconds of JavaScript. Wait for that here.
async function passBrowserCheck(page, url, timeoutMs = 25000) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => null);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const title = await page.title().catch(() => '');
    if (title && !/just a moment|attention required/i.test(title)) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

export async function scrapeCard({ company, credentials, startDate, executablePath }) {
  const def = CARD_COMPANIES[company];
  if (!def) throw new ScrapeError('UNSUPPORTED', `unsupported company: ${company}`);
  const missing = def.fields.filter((f) => !credentials?.[f]);
  if (missing.length) throw new ScrapeError('INVALID_REQUEST', `missing fields: ${missing.join(', ')}`);
  const picked = Object.fromEntries(def.fields.map((f) => [f, String(credentials[f])]));
  // Opt-in for troubleshooting only: a screenshot of the last screen when a
  // login fails. It can show account details, so leave it off in production.
  const debugDir = process.env.SCRAPE_DEBUG_DIR;
  // Isracard sits behind Cloudflare, which stops the default (new) headless
  // Chrome and announces it via "HeadlessChrome" in the user agent. The
  // classic headless shell with a regular user agent gets through, so the
  // browser is launched here and handed to the library, which closes it.
  const browser = await puppeteer.launch({
    headless: 'shell',
    executablePath,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  const userAgent = (await browser.userAgent()).replace('HeadlessChrome', 'Chrome');
  const scraper = createScraper({
    companyId: def.id,
    browser,
    preparePage: async (page) => {
      await page.setUserAgent(userAgent);
      await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      });
      if (def.warmupUrl) await passBrowserCheck(page, def.warmupUrl);
    },
    ...(debugDir ? { storeFailureScreenShotPath: `${debugDir}/${company}-failure.png` } : {}),
    startDate,
    combineInstallments: false,
    defaultTimeout: 120000,
  });
  let result;
  try {
    result = await scraper.scrape(picked);
  } finally {
    if (browser.connected) await browser.close().catch(() => {});
  }
  if (!result.success) throw new ScrapeError(result.errorType || 'GENERIC', result.errorMessage);
  return normalizeAccounts(result.accounts);
}
