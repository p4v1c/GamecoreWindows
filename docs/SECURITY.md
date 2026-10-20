# GameCore — Security model

> **Status: the four phases below are shipped and in production.** This started
> as a rollout plan; it now reads as a description of what is in place. Mentions
> of "this PR" are historical.
>
> Quick check on a box: `ss -tlnp` must show **exactly one** non-loopback
> GameCore listener, Caddy on `:8443`. One UDP socket is expected alongside it —
> avahi on `5353`, which serves discovery and no data; see "mDNS" below.

The goal: **one port on the LAN — Caddy `:8443` over HTTPS** — everything else on
loopback, behind a single shared login enforced by the reverse proxy
(`forward_auth`). The TV (Electron → `http://localhost:8765`) is deliberately
**untouched** by all of it: no login, no TLS.

## Target architecture

```
LAN (phone / laptop)
  └─ https://IP:8443  ── Caddy (tls internal, local CA)
        ├─ /login*, /api/auth/*, /gc/addons, /gc/ca.crt   → core :8765 (no auth)
        ├─ [forward_auth → core /api/auth/verify]
        │    ├─ /roms/*    → 127.0.0.1:8770  (rom-manager)
        │    ├─ /saves/*   → 127.0.0.1:8772  (save-manager)
        │    ├─ /rpcs3/*   → 127.0.0.1:8771  (rpcs3-manager)
        │    ├─ /bios/*    → 127.0.0.1:8773  (bios-manager)
        │    ├─ /twitch/*  → 127.0.0.1:8097  (EmberTV)
        │    ├─ /api/*     → 403 (the core is NEVER exposed to the LAN)
        │    └─ /…         → core :8765 (statics: /assets/*, /covers/*)
        └─ /  → redirect to /roms/
TV (the box itself)
  └─ Electron → http://localhost:8765 (loopback, no auth — physical access is trust)
```

Decisions taken: a **single shared password** (no multi-user); client CA trust via
a **QR code** pointing at `/gc/ca.crt`; Caddy on port **8443**; delivery as **one
branch and one PR per phase**.

## Phases

### Phase 1 — Everything on loopback

- The backend listens on `127.0.0.1:8765` (systemd unit written by
  `install/arch.sh`, and Electron's uvicorn fallback in `electron/main.js`).
- Every addon listens on `127.0.0.1` (see `docs/SECURITY.md` in the
  [gamecore-addons](https://github.com/p4v1c/gamecore-addons) repo).
- `CORSMiddleware allow_origins=["*"]` removed: behind Caddy everything is
  same-origin, and the TV already was.
- On an existing install the live unit
  `/etc/systemd/system/gamecore-backend.service` has to be aligned by hand (OTA
  does not rewrite units): `--host 0.0.0.0` → `--host 127.0.0.1`, then
  `daemon-reload` and restart.

### Phase 2 — Caddy: reverse proxy + TLS

- `install/system/Caddyfile` → `/etc/caddy/Caddyfile`; `pacman -S caddy`,
  `systemctl enable --now caddy`, then `caddy trust` (root CA into the box's own
  trust store, so the kiosk browser gets no warning).
- `tls internal`: Caddy's local CA acts as a mini-CA. LAN clients install it once
  from `https://IP:8443/gc/ca.crt` (link + QR on the login page and on the TV's
  Security page).
- The core gains `GET /gc/addons` (the addon registry payload, consumed by the
  shared nav — proxied without auth).
- Until Phase 3 was deployed, `forward_auth` failed because the endpoint did not
  exist: **deny-all by default**, never a temporary hole.

### Phase 3 — Shared login

- `config/auth.json` (`{argon2id hash, generation}`, 0600) + `config/auth_secret`
  (32 bytes, 0600, HMAC key for cookies). `config/` is excluded from the OTA
  rsync, so both survive updates. Neither is ever committed (`.gitignore`).
- `backend/services/auth.py`: argon2-cffi; cookie
  `expiry.generation.HMAC-SHA256(secret, "expiry.generation")`; in-memory
  anti-bruteforce (per IP via `X-Forwarded-For`, 5 failures → exponential
  backoff).
- `backend/routers/auth.py` — exempt from `forward_auth`, therefore reachable
  without a session: `POST /api/auth/login` (cookie `gc_session`, HttpOnly,
  Secure, SameSite=Lax, 30 days), `GET /api/auth/verify` (200 + `X-GC-User` /
  302 to `/login?next=…` / 401 — consumed by Caddy's `forward_auth`),
  `POST /api/auth/logout`.
- `POST /api/auth/change-password` (bumps `generation`, killing every session) is
  **behind** `forward_auth`: the old broad `/api/auth/*` exemption published it
  to the whole LAN. On a box with no password it answers 503 — it changes a
  password, it does not set the first one.
- A self-contained `/login` page served by the core; the password is set by the
  prompt in `arch.sh` (the graphical installer requires it) or by
  `gamecore-addon auth-reset`.
- The core enforces **no** auth on its own routes (it is only reachable on
  loopback): enforcement is exclusively Caddy's job.

### Phase 4 — Addons behind a path prefix

- Each addon gets its `root_path` from the `ADDON_BASE` env var (`/roms`,
  `/saves`, `/rpcs3`); `addon.json` gains a `path` field, copied into the
  registry by the `gamecore-addon` CLI and exposed through `/gc/addons`.
- The shared nav and the addon pages reference no port at all: links and fetches
  are relative (or go through `/gc/addons`), and browser→core traffic goes via
  the `/assets/*` statics or an addon-side passthrough.
- Addons contain **no auth code whatsoever**: Caddy protects them, and all they
  receive is the `X-GC-User` header.

---

## Defence in depth on top of the four phases

Phases 1-4 answer "who may reach the box from the LAN". The following close the
gaps that remain once something is *already* inside — a browser running on the
box, a page in the kiosk, or a client that has legitimately logged in.

### The core's cross-origin guard (`backend/main.py`)

The core has no authentication of its own and the box runs browsers that can
reach it: the Firefox kiosk profiles `arch.sh` installs, and Stremio. A page in
one of those could auto-submit

```html
<form action="http://127.0.0.1:8765/api/games/kill" method="post">
```

and kill the running game, unsaved progress included. Only endpoints that take
**no Pydantic body** are reachable that way — an HTML form can only send
`x-www-form-urlencoded`, `multipart/form-data` or `text/plain`, and FastAPI
answers 422 to anything else — which still left `POST /api/games/kill`,
`POST /api/update/apply`, `POST /api/addons/{name}/install` and
`POST /api/standby/exit`. `games/launch` and `addons/notify` were never
reachable.

A middleware now refuses any non-GET/HEAD/OPTIONS request whose `Origin` names
somewhere we are not serving, or whose `Sec-Fetch-Site` is `cross-site`. Two
things it deliberately is **not**:

- **Not a localhost allowlist.** `/login` and `/api/auth/*` are proxied from
  `https://<whatever address the client used>:8443`, and the box has no fixed
  name — the Caddyfile mints certificates on demand precisely because the
  address changes. The rule is therefore same-origin against the forwarded
  `Host`. An allowlist would have 403'd every LAN login.
- **Not a blanket loopback pass.** `localhost` and `127.0.0.1` are accepted as
  the same machine *on the backend's own port*, because Electron says one where
  the socket reports the other. Another local application on another port is not
  the UI and does not get in.

Requests with no `Origin` still pass: curl, the addon CLI and the install
scripts have none, and a browser always attaches one to a cross-origin write.

### `/ws` (`backend/main.py`)

A WebSocket handshake is a GET and is **not subject to CORS at all**, so any page
in any browser on the box could open `ws://127.0.0.1:8765/ws` and read every
event the UI sees — what launched, what is installed, controller activity. The
endpoint applies the same origin rule and closes with 1008 otherwise.

### On-demand TLS gate (`/api/auth/tls-ask`)

`on_demand_tls { ask … }` used to point at Caddy's own admin API, which answers
200 to anything: any LAN client could open a handshake with an arbitrary SNI and
have the box mint a certificate for it, without limit.

The core now answers that question. Approved: loopback, this machine's addresses
on any interface (the LAN address moves with the network, and a Tailscale address
only appears there), its hostname and `<hostname>.local`, and any name that
resolves to one of those — the last is what keeps MagicDNS working.

### EmberTV (`/twitch`)

EmberTV bound `0.0.0.0:8097` and authenticates nothing. Once the owner has signed
in through the device-code flow, the OAuth token (`chat:edit`) lives in
`.twitch-user.json` and **every route acts as that account** — a single `curl`
from anywhere on the Wi-Fi could post to chat as the owner, read their profile
and following list, or sign them out. It was the second GameCore port on the LAN,
against the rule stated at the top of this document, and it appeared in no
Caddyfile.

It is loopback-only now, and reachable at `/twitch/` behind the same
`forward_auth` as the addons. Two mechanical details matter:

- EmberTV takes `BASE_PATH=/twitch` (set in its unit by `install/arch.sh`) and
  strips the prefix itself, exactly like the addons' `root_path`. Its client is
  built from absolute URLs, so a Caddy-side `handle_path` strip would break every
  asset.
- The route sets `header_up Host {hostport}`. Caddy replaces `Host` with the
  upstream address when the upstream is `https://` (it preserves it for
  `http://`), and EmberTV accepts a POST only when the `Origin` host matches
  `Host` — without it, sending a chat message from a LAN browser would 403 every
  time.

EmberTV's own routes were hardened upstream too: the ones with a side effect
(`auth/device/start`, `auth/signout`, `chat/send`) are POST-only and must carry a
matching `Origin`. The old guard skipped the check whenever `Origin` was absent,
which is to say for everything that was not a browser.

### Login rate limiting is a slowdown, not a door

The global circuit breaker was applied to **every** caller, so 25 failed logins
spread over throwaway keys — something any unauthenticated LAN client can
produce — returned 429 to an address that had never tried once and was
presenting the correct password. Replayed every 60 seconds, that locked the
owner out of the ROM, save and RPCS3 managers for as long as an attacker cared
to keep going. (The TV is unaffected; it reaches the core on loopback.)

The breaker now only weighs on addresses already known to have failed. It still
slows a distributed spray — every sprayer is in that set by construction — while
a client that has not got a password wrong is not treated as part of it.

### Path containment

`/api/covers/{system_id}/{filename:path}` passed `filename` straight into
`roms_root / filename`, and the `:path` converter accepts slashes and `..`. The
rule the codebase already applies in `launch_game` — resolve, then check against
the root — now also applies in `cover_pipeline` and `metadata`.

In the save-manager addon, an entry id of `"."` resolved to the collection
directory itself (`PurePosixPath(".").parts` is empty, so neither the
`is_absolute()` nor the `".."` check fired), and `DELETE` then backed up and
`rmtree`'d the whole collection. For mgba and melonDS that collection *is* the
ROM directory.

### The catalogue is code, and it is treated as such

`catalog/<id>/` can carry `generator.py`, a systemd unit, and shell run at
install time. `config/catalog.d/` — the local override directory, writable on the
box and excluded from OTA — can carry the same JSON. The two are not trusted the
same way.

`backend/services/catalog/loader.py` **strips** `postInstall`, `services`,
`sources` and `packages` from any pack found in `config/catalog.d/`, and ignores
its `generator.py`, logging the fact at every load. Without that rule, dropping a
directory on a box would be arbitrary code execution as root, and the install CLI
would make it reachable from the UI. `GAMECORE_TRUST_LOCAL_PACKS=1` lifts it for
an operator who means it; the warning is printed on every load, not once.

Three more rules the applier enforces (`backend/services/installer/applier.py`):

- **A pack may only read its own directory.** `src`, `template`, `unit` and
  `run` are resolved against the pack directory and refused when they resolve
  outside it — `..` in a `pack.json` is not a use case.
- **`postInstall` never runs as root.** It runs as the gaming user, with a
  timeout the schema caps at 300 s, and a failure is a warning: a certificate
  hiccup must never be the reason an install is reported as failed.
- **Secrets never reach `argv`.** `sudo -u <user> env KEY=value …` would put
  every value in `/proc/<pid>/cmdline`, which is world-readable, and one of them
  is a Twitch client secret. `--preserve-env` names the variables and lets sudo
  carry them from an environment instead. The graphical installer writes them to
  a `0600` temp file which it deletes when it closes — including when the window
  is closed mid-install, which it previously did not.

## mDNS (avahi) — the one deliberate exception to "one port"

`install/arch.sh` installs `avahi` + `nss-mdns`, enables `avahi-daemon.service`,
and adds `mdns_minimal [NOTFOUND=return]` to the `hosts:` line of
`/etc/nsswitch.conf`. The ISO carries both packages and enables the unit through
`multi-user.target.wants/`, like NetworkManager.

**This is a second LAN-facing socket: UDP `5353`, on every interface.** It is the
only one besides Caddy `:8443`, and the rule at the top of this document is
otherwise unchanged — nothing new is *served*, and no GameCore data crosses it.

Why it is worth the exception: the box is reachable at exactly one address and
that address is a DHCP lease. When the lease changes, every noted URL breaks at
once — the login page, `/roms/`, `/saves/`, `/rpcs3/`, `/bios/`. mDNS replaces the address
with `<hostname>.local`. **The installer does not set the hostname** — the box
answers under whatever name the machine already had, so `gamecore.local` holds
only if `/etc/hostname` says `gamecore`.

What it exposes, precisely:

- **The hostname and the IP behind it**, to anyone on the same layer-2 segment.
  That is the entire protocol; mDNS has no authentication and cannot have any.
  Someone already on the LAN can find the box by scanning it in any case — mDNS
  makes discovery convenient, not possible.
- **No service records.** GameCore installs nothing in `/etc/avahi/services/`, so
  the daemon advertises the host address only. It does not announce that :8443
  exists, what runs behind it, or that this is a games console. Adding a
  `.service` file would change that, and is a decision to take here first.
- **Nothing about authentication.** The shared login on :8443 is untouched:
  resolving the name gets a client to the same Caddy, facing the same
  `forward_auth`.

Also true, and easy to miss: **`ss -tlnp` does not show it.** `-t` is TCP and
mDNS is UDP, so the verification below stays literally correct. Use
`ss -ulnp | grep 5353` to see it.

**Boxes installed before this change never get it.** OTA (`update/linux.sh`) is
an rsync of files; it does not re-run `arch.sh`, so no update installs a package,
enables a unit or edits `/etc/nsswitch.conf`. Such a box keeps working exactly as
before, reachable by IP — the change is additive and breaks no existing route.
Retrofitting one is three manual commands as root, and re-running `arch.sh` does
the same thing idempotently:

```sh
pacman -S --needed avahi nss-mdns
systemctl enable --now avahi-daemon.service
# then add `mdns_minimal [NOTFOUND=return]` to the hosts: line of
# /etc/nsswitch.conf, BEFORE `resolve` and `dns` — after them it is never
# consulted, and the symptom is identical to the daemon being stopped.
```

## Operational notes

- **OTA**: `update/linux.sh` excludes `config/`, `emu/`, `emu-configs/` and
  `assets/logos|overlays/` (the Linux bezels), so the box's local state (library, auth, addon
  registry) survives updates. OTA touches neither `/etc/systemd/*` nor
  `/etc/caddy/*`.
- **`/etc/caddy/Caddyfile` is not updated by OTA.** It is written only by
  `install/arch.sh`, which templates the backend port into it, and it needs root.
  A security fix to the shipped Caddyfile therefore reaches no installed box on
  its own. `update/linux.sh` now compares the two and prints the command to
  apply it — but applying it is a manual step.
- **Password reset**: `gamecore-addon auth-reset` (regenerates the secret and the
  hash — every session dies).
- **Sudoers**: every rule in `/etc/sudoers.d/gamecore-power` is argument-narrow —
  `systemctl poweroff|reboot`, `udevadm trigger`, `systemctl start` on the two
  GameCore units, `gamecore-session-select` with its two literal arguments, and
  the two `cpupower` governors GameCore uses. Nothing is wildcarded — which is
  why `gamecore-session-select` takes no third argument: a rule that had to
  accept one would have to accept any.
- **Verification**: `ss -tlnp` must show, for GameCore, only Caddy on `:8443`;
  `8765`, `8097`, `8770`, `8771`, `8772` and `8773` on `127.0.0.1` only. On UDP,
  `ss -ulnp` shows avahi on `5353` — expected, and the only one (see "mDNS").
