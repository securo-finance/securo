// Minimal internal HTTP API used only by the Securo backend.
//
// Bank Leumi, QR login (no credentials at all): a fresh browser on Leumi's
// "log in with the app" page; the result is kept in memory for a limited time.
//   POST   /sessions                 {user_id, start_date}  -> {id}
//   GET    /sessions/:id?user_id=    -> {status, qr, error}   (qr = base64 PNG while waiting)
//   GET    /sessions/:id/result      -> {scraped_at, accounts}
//   DELETE /sessions/:id
//
// Credit cards (Max, Isracard), credentials sent by the backend per request:
//   POST   /scrape   {company, credentials, start_date} -> {scraped_at, accounts}
//                    or 422 {error_type, error} when the company refuses the login.
// A result is reused for 10 minutes so one Securo sync (accounts, then each
// account's transactions) logs in once. Credentials are never stored or logged;
// the cache is keyed by a hash of them.
//
// Every request needs "Authorization: Bearer $ISRAELI_BANKS_TOKEN".
import http from 'node:http';
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import { runLeumiQr } from './leumi.mjs';
import { CARD_COMPANIES, ScrapeError, scrapeCard } from './cards.mjs';

const TOKEN = process.env.ISRAELI_BANKS_TOKEN || '';
const PORT = Number(process.env.PORT || 8095);
const LOGIN_TIMEOUT_MS = Number(process.env.LEUMI_QR_LOGIN_TIMEOUT_SECONDS || 180) * 1000;
const RESULT_TTL_MS = Number(process.env.LEUMI_QR_RESULT_TTL_MINUTES || 60) * 60 * 1000;
const DEFAULT_HISTORY_DAYS = Number(process.env.LEUMI_QR_DEFAULT_HISTORY_DAYS || 365);
const SCRAPE_CACHE_MS = Number(process.env.SCRAPE_CACHE_SECONDS || 600) * 1000;
if (TOKEN.length < 32) {
  console.error('ISRAELI_BANKS_TOKEN must be set to a random value of at least 32 characters');
  process.exit(1);
}

let executablePath;
try {
  const { createRequire } = await import('node:module');
  executablePath = createRequire(import.meta.url)('puppeteer').executablePath();
} catch { executablePath = undefined; }

const sessions = new Map(); // id -> {userId, status, qr, error, result, finishedAt}
const ACTIVE = new Set(['starting', 'waiting_for_scan', 'reading']);

function authorized(req) {
  const got = Buffer.from((req.headers.authorization || '').replace(/^Bearer /, ''));
  const want = Buffer.from(TOKEN);
  return got.length === want.length && timingSafeEqual(got, want);
}

function send(res, code, body) {
  res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify(body));
}

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) { raw += chunk; if (raw.length > 20_000) throw new Error('body too large'); }
  return raw ? JSON.parse(raw) : {};
}

function startSession(userId, startDateStr) {
  // One login at a time: Chrome is the heaviest thing here and Leumi allows
  // one login per customer anyway. A user who closes the dialog and starts
  // again replaces their own unfinished session instead of waiting it out.
  for (const s of sessions.values()) {
    if (!ACTIVE.has(s.status)) continue;
    if (s.userId !== userId) return null;
    s.cancelled = true;
  }
  const id = randomUUID();
  const s = { userId, status: 'starting', qr: null, error: null, result: null, finishedAt: null, cancelled: false };
  sessions.set(id, s);
  const startDate = startDateStr ? new Date(`${startDateStr}T00:00:00+02:00`)
    : new Date(Date.now() - DEFAULT_HISTORY_DAYS * 86400000);
  runLeumiQr({
    startDate,
    loginTimeoutMs: LOGIN_TIMEOUT_MS,
    executablePath,
    isCancelled: () => s.cancelled,
    hooks: { onQr: (png) => { s.qr = png; }, onStatus: (st) => { s.status = st; } },
  }).then((accounts) => {
    s.result = { scraped_at: new Date().toISOString(), accounts };
    s.status = 'ready';
  }).catch((err) => {
    s.status = 'error';
    s.error = String(err.message || err).slice(0, 300);
    console.error(`session ${id.slice(0, 8)} failed: ${s.error}`);
  }).finally(() => { s.qr = null; s.finishedAt = Date.now(); });
  return id;
}

// --- credit cards ------------------------------------------------------------

const scrapeCache = new Map(); // key -> {at, promise}
let scrapeQueue = Promise.resolve(); // one browser at a time

function scrapeKey(company, credentials, startDate) {
  const fields = CARD_COMPANIES[company]?.fields || [];
  const material = JSON.stringify([company, startDate || '', ...fields.map((f) => String(credentials?.[f] ?? ''))]);
  return createHash('sha256').update(TOKEN).update(material).digest('hex');
}

function cachedScrape(company, credentials, startDateStr) {
  const key = scrapeKey(company, credentials, startDateStr);
  const hit = scrapeCache.get(key);
  if (hit && Date.now() - hit.at < SCRAPE_CACHE_MS) return hit.promise;
  const startDate = startDateStr ? new Date(`${startDateStr}T00:00:00+02:00`)
    : new Date(Date.now() - DEFAULT_HISTORY_DAYS * 86400000);
  const run = scrapeQueue.then(() => scrapeCard({ company, credentials, startDate, executablePath }))
    .then((accounts) => ({ scraped_at: new Date().toISOString(), accounts }));
  scrapeQueue = run.catch(() => {});
  scrapeCache.set(key, { at: Date.now(), promise: run });
  // A failed login is not cached: the user may fix the password and retry.
  run.catch(() => scrapeCache.delete(key));
  return run;
}

setInterval(() => {
  const now = Date.now();
  for (const [id, s] of sessions) if (s.finishedAt && now - s.finishedAt > RESULT_TTL_MS) sessions.delete(id);
  for (const [key, c] of scrapeCache) if (now - c.at > SCRAPE_CACHE_MS) scrapeCache.delete(key);
}, 60_000).unref();

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/health') return send(res, 200, { status: 'ok' });
    if (!authorized(req)) return send(res, 401, { error: 'unauthorized' });
    if (url.pathname === '/scrape' && req.method === 'POST') {
      const body = await readJson(req);
      try {
        return send(res, 200, await cachedScrape(body.company, body.credentials, body.start_date));
      } catch (err) {
        const errorType = err instanceof ScrapeError ? err.errorType : 'GENERIC';
        // The library's messages describe the login step, never the credentials.
        console.error(`scrape ${body.company} failed: ${errorType}`);
        return send(res, 422, { error_type: errorType, error: String(err.message || err).slice(0, 300) });
      }
    }
    const m = url.pathname.match(/^\/sessions(?:\/([0-9a-f-]{36}))?(\/result)?$/);
    if (!m) return send(res, 404, { error: 'not found' });
    const [, id, isResult] = m;

    if (!id && req.method === 'POST') {
      const body = await readJson(req);
      if (!body.user_id) return send(res, 400, { error: 'user_id required' });
      const newId = startSession(String(body.user_id), body.start_date);
      return newId ? send(res, 201, { id: newId }) : send(res, 409, { error: 'busy' });
    }
    const s = id && sessions.get(id);
    if (!s) return send(res, 404, { error: 'session not found or expired' });
    if (req.method === 'DELETE') { s.cancelled = true; sessions.delete(id); return send(res, 204, {}); }
    if (isResult) {
      if (s.status !== 'ready') return send(res, 409, { error: `session is ${s.status}` });
      return send(res, 200, s.result);
    }
    if (url.searchParams.get('user_id') !== s.userId) return send(res, 404, { error: 'session not found or expired' });
    return send(res, 200, { status: s.status, qr: s.qr, error: s.error });
  } catch (err) {
    return send(res, 500, { error: String(err.message || err).slice(0, 200) });
  }
}).listen(PORT, () => console.log(`leumi-qr listening on ${PORT}`));
