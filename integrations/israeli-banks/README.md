# israeli-banks

Internal service that lets Securo read Israeli banks and credit cards with
[israeli-bank-scrapers](https://github.com/eshaham/israeli-bank-scrapers).
Israeli open banking APIs are only open to licensed providers, so individuals
have no API; this drives the companies' own websites in a headless Chrome.

| Provider | Login | Stored by Securo |
|----------|-------|------------------|
| Bank Leumi (`leumi`) | Scan Leumi's QR code with the Leumi app, in Securo's connect dialog | Nothing. Each sync needs a fresh scan; a read is kept here for an hour |
| Max (`max`) | Username + password | Encrypted on the connection (key derived from `SECRET_KEY`) |
| Isracard (`isracard`) | ID number + last 6 card digits + password | Encrypted on the connection |

## Enabling

```env
ISRAELI_BANKS_ENABLED=true
ISRAELI_BANKS_TOKEN=<openssl rand -hex 32>
COMPOSE_PROFILES=israeli-banks
```

The same token goes to Securo and to this container. The container is reached
only by the backend over the compose network; every request but `/health`
needs `Authorization: Bearer $ISRAELI_BANKS_TOKEN`.

## API (backend only)

- `POST /sessions` `{user_id, start_date}` → `{id}`: start a Leumi QR login.
- `GET /sessions/:id?user_id=` → `{status, qr, error}`: `qr` is a PNG (base64) while waiting for the scan.
- `GET /sessions/:id/result` → `{scraped_at, accounts}` once `status` is `ready`.
- `POST /scrape` `{company, credentials, start_date}` → `{scraped_at, accounts}`, or `422 {error_type, error}`.
  A result is reused for 10 minutes so one sync logs in once. Credentials are
  never written or logged; the cache is keyed by a hash of them.

Dates are converted to the calendar date in `Asia/Jerusalem` (the library
returns midnight Israel time as a UTC instant).

## Notes

- One browser runs at a time.
- Isracard is behind Cloudflare. The container uses the classic headless shell
  with a regular user agent and waits for Cloudflare's browser check before the
  library logs in.
- These are websites, not APIs: when a company changes its login page, the
  library needs an update. Max and Isracard may also ask for an SMS code on a
  new device, which a headless login cannot answer.
- `SCRAPE_DEBUG_DIR` (troubleshooting only) saves a screenshot of the last
  screen when a card login fails. It can show account details; leave it unset
  in normal use.
