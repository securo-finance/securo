// One QR login + read of Bank Leumi, built on israeli-bank-scrapers.
// Only the login step is replaced: instead of typing a username and password,
// the page is left on Leumi's "login with the app" QR and we wait for the user
// to scan it. Everything after login is the library's own Leumi code.
import { createRequire } from 'node:module';
import { normalizeAccounts } from './normalize.mjs';

const require = createRequire(import.meta.url);
const LeumiScraper = require('israeli-bank-scrapers/lib/scrapers/leumi/leumi.js').default;

const LOGIN_PAGE = 'https://hb2.bankleumi.co.il/authenticate/logon';
const QR_SELECTOR = '.app-qr-code-col-2';
const LOGGED_IN_SELECTORS = ['a[title="דלג לחשבון"]', 'div.main-content'];

/**
 * Run one session. `hooks.onQr(base64Png)` is called whenever the QR is
 * (re)captured, `hooks.onStatus(status)` on transitions.
 */
export async function runLeumiQr({ startDate, loginTimeoutMs, executablePath, hooks, isCancelled = () => false }) {
  class LeumiQrScraper extends LeumiScraper {
    async login() {
      // Render at 3x so the captured QR (96 CSS px) is sharp enough to scan off a screen.
      await this.page.setViewport({ width: 1280, height: 900, deviceScaleFactor: 3 });
      await this.page.goto(LOGIN_PAGE, { waitUntil: 'networkidle2' });
      hooks.onStatus('waiting_for_scan');
      const deadline = Date.now() + loginTimeoutMs;
      while (Date.now() < deadline) {
        if (isCancelled()) return { success: false, errorType: 'CANCELLED', errorMessage: 'Cancelled' };
        if (/ebanking\/SO\/SPA\.aspx/i.test(this.page.url())) break;
        let loggedIn = false;
        for (const sel of LOGGED_IN_SELECTORS) {
          if (await this.page.$(sel).catch(() => null)) loggedIn = true;
        }
        if (loggedIn) break;
        const qr = await this.page.$(QR_SELECTOR).catch(() => null);
        if (qr) {
          const png = await qr.screenshot({ encoding: 'base64' }).catch(() => null);
          if (png) hooks.onQr(png);
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
      if (Date.now() >= deadline) return { success: false, errorType: 'TIMEOUT', errorMessage: 'QR was not scanned in time' };
      hooks.onQr(null);
      hooks.onStatus('reading');
      return { success: true };
    }
  }

  const scraper = new LeumiQrScraper({
    companyId: 'leumi',
    startDate,
    combineInstallments: false,
    showBrowser: false,
    defaultTimeout: 120000,
    executablePath,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    viewportSize: { width: 1280, height: 900 },
  });
  const result = await scraper.scrape({});
  if (!result.success) {
    throw new Error(`${result.errorType || 'ERROR'}: ${result.errorMessage || 'Leumi read failed'}`);
  }
  return normalizeAccounts(result.accounts);
}
