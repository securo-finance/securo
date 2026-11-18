# 0024. The client IP is read through trusted proxies only

- Status: Accepted
- Date: 2026-11-18
- Origin: follow-up to the chat rate limit review in #1593

## Context

Every rate limiter keys its window by client IP: login, second factor,
registration, password reset and the assistant chat
(`backend/app/core/rate_limit.py`). The same IP is the `Psu-Ip-Address` sent to
Enable Banking on a sync the user starts (`backend/app/providers/psu.py`).

The backend never sees the visitor directly. Two topologies are shipped in
`docker-compose.prod.yml`:

- nginx alone: browser → frontend nginx → backend (`TRUSTED_PROXY_HOPS=1`);
- Traefik in front: browser → Traefik → frontend nginx → backend (`=2`).

`resolve_client_ip` used to take the Nth entry from the right of
`X-Forwarded-For`, with N the hop count, without looking at who sent the
header. Both the backend port (8000) and the frontend port (3000) are published
by the Compose files, so a caller who reached either one directly could write
any address into the header and pick a fresh bucket on every request, escaping
every limiter. Behind Traefik it was enough to skip Traefik and talk to nginx.

## Decision

- **A forwarded hop is only crossed when the address in hand is a trusted
  proxy.** `resolve_client_ip` starts at the socket peer and walks
  `X-Forwarded-For` from the right, at most `TRUSTED_PROXY_HOPS` times. As soon
  as the address it holds is outside `TRUSTED_PROXY_NETWORKS`, that address is
  the client and the walk stops; what is left of the header is ignored.
- **`TRUSTED_PROXY_NETWORKS`** (`backend/app/core/config.py`) lists where a
  proxy can sit, as networks or addresses. The default is loopback and the
  private ranges (IPv4 RFC 1918 and IPv6 unique local addresses), which covers the Docker and Podman networks
  the shipped proxies live on, so existing instances need no change. A value
  that is not a list of networks fails at startup.
- **`TRUSTED_PROXY_HOPS` keeps its meaning** as the number of proxies in front
  of the backend, and a chain shorter than the hops still to cross is still
  not trusted (`"unknown"`).

How the client IP comes out in each case:

| Who connects to the backend | Hops | Key |
|-----------------------------|------|-----|
| a public caller, on port 8000 | any | the caller's own address |
| nginx, for a caller who came straight to port 3000 | 1 or 2 | the address nginx saw |
| nginx, for a caller who came through Traefik | 2 | the address Traefik saw |
| nginx, with `TRUSTED_PROXY_HOPS=1` behind Traefik | 1 | Traefik's address (every visitor shares one bucket: set 2) |

## Consequences

- A caller on a private address (a LAN, a VPN) counts as a proxy under the
  default, so it can still choose its key by sending the header. That only
  matters where untrusted people share those ranges with the proxies; such an
  instance narrows `TRUSTED_PROXY_NETWORKS` to the proxies' own networks.
- When Docker publishes a port through its userland proxy instead of NAT, the
  backend sees the bridge gateway (a private address) as the peer. The default
  then trusts that hop; narrowing the networks to the Compose network closes it.
- Any new code that needs the client's address goes through
  `resolve_client_ip` with both settings, never `X-Forwarded-For` or
  `X-Real-IP` directly.

## Enforced by

- `backend/tests/test_rate_limit.py`: a public peer cannot pick its address,
  a caller skipping Traefik is keyed by what nginx saw, two visitors behind
  either topology get separate login buckets, a direct caller rotating the
  header stays in one bucket, and a bad `TRUSTED_PROXY_NETWORKS` is refused.
- `backend/tests/test_enable_banking_psu_headers.py` checks that the address
  sent as `Psu-Ip-Address` is the one this function resolves.
