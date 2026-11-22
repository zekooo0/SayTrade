# Deploy SayTrade for free

Four services, no credit card, no expiry:

| Piece | Where | Free tier in one line |
| --- | --- | --- |
| API (FastAPI) | FastAPI Cloud, Hobby | 0.1 vCPU / 512 MB shared, scale-to-zero, autoscale, 3 apps, 1-day logs |
| Database (Postgres) | Neon, free plan | 0.5 GB, no expiry, compute sleeps after 5 min idle |
| Web (Next.js) | Vercel, Hobby | unlimited static, serverless functions |
| Audio (recordings) | Cloudinary | already configured, unchanged |

All three are live:

    API      https://saytrade-api.fastapicloud.dev
    web      https://saytrade-web.vercel.app
    database saytrade / misty-violet-59144510 / aws-eu-central-1 / Postgres 18

**Sign-in runs through the app proxy** at `/__clerk`, because the production
instance's own `clerk.…` host cannot be verified under `vercel.app`. See "A
production instance without a domain you own: the app proxy" in section 4.

---

## 1. Neon — the database

Status: **done.** Project `misty-violet-59144510` (`saytrade`), region
`aws-eu-central-1`, Postgres 18, branch `production` (the default branch), linked
to this repo via `.neon`. The corrected connection string lives in **`.env.neon`**
(gitignored): asyncpg scheme, `ssl=require`, direct host.

> **`neon link` overwrites `DATABASE_URL` in `.env`** with the *pooler* host under
> the plain `postgresql://` scheme plus `channel_binding=require` — three things
> this app cannot use, and your local docker URL is gone. `neon env pull` writes
> into `.env` the same way. Always pull with `--file .env.neon`, or repair `.env`
> afterwards and verify by connecting through `app.config.settings`.

Conversion, if you ever do it by hand: scheme → `postgresql+asyncpg://`,
`sslmode=require` → `ssl=require`, delete `channel_binding`, and take the
**direct** host (drop `-pooler`) because SQLAlchemy keeps its own pool.

## 2. FastAPI Cloud — the API

Status: **done.** App `saytrade-api` (id `8061d9aa-9667-44b2-8784-bda9e6716a1e`),
team `Ahmed Zakaria`, linked through `.fastapicloud/` (gitignored). The API answers
at <https://saytrade-api.fastapicloud.dev/health>.

Redeploy:

```
cd backend/sayTrade
./.venv/bin/fastapi deploy        # or: uv run fastapi deploy
```

The CLI packages the repo (respecting `.gitignore`), builds it in the cloud with
uv from `uv.lock`, and verifies the deployment. The app entrypoint comes from
`[tool.fastapi] entrypoint = "app.main:app"` in `pyproject.toml`.

### Environment variables

Seven, all set. Secrets are marked `--secret` and cannot be read back:

| Key | Kind |
| --- | --- |
| `DATABASE_URL` | secret — the Neon string from `.env.neon` |
| `FIREBASE_PROJECT_ID` | plain — `saytrade-app` (the issuer is `https://securetoken.google.com/<id>`) |
| `OPENAI_API_KEY` | secret |
| `CLOUDINARY_CLOUD_NAME` | plain |
| `CLOUDINARY_API_KEY` | secret |
| `CLOUDINARY_API_SECRET` | secret |
| `CORS_ORIGINS` | plain — `https://saytrade-web.vercel.app` |

Payments add six more, from the LemonSqueezy dashboard (Settings » API keys, and
the webhook's signing secret):

| Key | Kind |
| --- | --- |
| `LEMONSQUEEZY_API_KEY` | secret — the LIVE key |
| `LEMONSQUEEZY_STORE_ID` | plain |
| `LEMONSQUEEZY_VARIANT_MONTHLY` | plain — the monthly variant id |
| `LEMONSQUEEZY_VARIANT_YEARLY` | plain — the yearly variant id |
| `LEMONSQUEEZY_WEBHOOK_SECRET` | secret — the webhook's signing secret |

While `LEMONSQUEEZY_API_KEY` is empty, payments are off: `POST /billing/checkout`
answers 503 and the app keeps saying "Pro is coming". Leave
`LEMONSQUEEZY_TEST_MODE` unset in production — a rehearsal purchase must never
grant a real plan.

```
./.venv/bin/fastapi cloud env list                 # names only
./.venv/bin/fastapi cloud env set KEY VALUE --secret
```

Two traps, both met while setting these up:

- **`env set` cannot read a piped value.** Its prompt is a TUI: piped stdin
  answers "Cancelled. Aborted." Pass the value as an argument, and read it from
  the local files inside a script so it never lands in the conversation.
- **Setting a variable does not redeploy.** Run `fastapi deploy` again.

### Migrations have no hook

FastAPI Cloud has no start command and no release phase, so `alembic upgrade head`
cannot run at boot. Run it **from the machine** against Neon:

```
cd backend/sayTrade
NEON_URL=$(grep -m1 '^DATABASE_URL=' .env.neon | cut -d= -f2- | tr -d '"')
DATABASE_URL="$NEON_URL" ./.venv/bin/alembic upgrade head
DATABASE_URL="$NEON_URL" ./.venv/bin/alembic current      # expect a3f8c21d64be (head)
```

`alembic/env.py` takes the URL from `settings.database_url`, and a real
environment variable wins over `.env`, so the override works.

Do this before every deploy that ships a migration. Do not move migrations into
the app's startup: the app autoscales past one replica, and concurrent runs fight
each other.

## 3. Vercel — the web app

Status: **done.** Project `zekooo0s-projects/saytrade-web`, linked to
`github.com/zekooo0/saytrade-web`, serving <https://saytrade-web.vercel.app>.
All three production variables are set and were verified by hash against the local
`.env.local`:

| Key | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://saytrade-api.fastapicloud.dev` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | from the Clerk dashboard |
| `CLERK_SECRET_KEY` | from the Clerk dashboard |
| `NEXT_PUBLIC_CLERK_PROXY_URL` | `https://saytrade-web.vercel.app/__clerk/` |

`NEXT_PUBLIC_*` values are baked into the client bundle at build time. Proven
locally: a build without the variable carries `localhost:8000` in four chunks, a
build with it carries the host in those same four. **Change the value and you must
redeploy.**

## 4. Close the loop

1. **CORS.** `CORS_ORIGINS` on the API is already the Vercel URL. Several origins
   are allowed, comma separated.
2. **Clerk.** The deployed pair is still the *development* instance (`pk_test` /
   `sk_test` on Vercel, Frontend API `https://mint-stud-8634.clerk.accounts.dev`).
   That is why the live site says development mode.

### Switching Clerk to the production instance

Three values in two dashboards, and **they must change together**. Any one of them
out of step makes every API call answer 401:

| Where | Key | Production value |
| --- | --- | --- |
| Vercel, Production | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | `pk_live_…` (the parked web journal only) |
| Vercel, Production | `CLERK_SECRET_KEY` | `sk_live_…` (the parked web journal only) |
| FastAPI Cloud | `FIREBASE_PROJECT_ID` | `saytrade-app` |

The API is on Firebase now, so it needs no Clerk value at all. The two Vercel keys
belong to the parked web journal and are unrelated to the API. A deployed API
without `FIREBASE_PROJECT_ID` answers 503 on every authenticated route — set it
before the deploy, not after.

Then redeploy both halves — `NEXT_PUBLIC_*` is baked at build time:

```
cd backend/sayTrade && ./.venv/bin/fastapi deploy
cd indie-hacker/say-trade && npx vercel@latest --prod --yes
```

Local development keeps the development keys in `.env` and `.env.local`. Only the
deployed environments move.

### Google sign-in in production

Clerk shares its own Google credentials for development instances. Production
requires **your** OAuth client, so there is no Client ID or Client Secret until
you make one:

1. Clerk Dashboard → **SSO connections** → Add connection → For all users →
   Google. Toggle on **Enable for sign-up and sign-in** and **Use custom
   credentials**. Copy the **Authorized Redirect URI** it shows — for this
   instance it is `https://clerk.saytrade-web.vercel.app/v1/oauth_callback`.
2. Google Cloud Console → APIs & Services → Credentials → Create credentials →
   OAuth client ID → **Web application**.
   - Authorized JavaScript origins: `https://saytrade-web.vercel.app` and
     `http://localhost:3000`.
   - Authorized redirect URIs: the value copied from Clerk.
3. Paste the new **Client ID** and **Client Secret** into the Clerk Google
   connection and save.
4. Google's OAuth consent screen starts in **Testing** (100 users, and testers
   must be listed). Set it **In production** before real users sign in.

### Finishing the production instance

The Clerk Dashboard's home page lists what is still needed. With proxying enabled
no DNS record and no certificate step is involved — Clerk reaches the instance
through this app. Confirm the two items that are easy to miss:

- **Google credentials.** A production instance never uses Clerk's shared
  credentials. Clerk must hold *your* OAuth client, and its Authorized Redirect
  URI must be the proxy's (`https://saytrade-web.vercel.app/__clerk/v1/oauth_callback`).
- **No `NEXT_PUBLIC_CLERK_PROXY_URL` locally.** Clerk does not proxy development
  instances.

Accounts do **not** migrate between the development and production instances — the
cloud database is empty, so nothing is lost.

### A production instance without a domain you own: the app proxy

`clerk.saytrade-web.vercel.app` and `accounts.saytrade-web.vercel.app` resolve to
Vercel's edge (64.29.17.131) and their TLS handshake dies with "unexpected eof".
They resolve at all only because `*.vercel.app` is Vercel's wildcard, and Clerk's
CNAME records cannot be added under a domain Vercel owns. So the CNAME route to a
production instance is closed here.

**The app proxy is the way through.** Clerk serves its Frontend API through this
app's own domain at `/__clerk`, and no DNS record is involved. Three pieces:

| Where | Setting | Value |
| --- | --- | --- |
| Clerk Dashboard | Domain → proxy | `https://saytrade-web.vercel.app/__clerk` |
| `proxy.ts` | `clerkMiddleware` option | `{ frontendApiProxy: { enabled: true } }` |
| `proxy.ts` | matcher | must include `"/__clerk/(.*)"` |
| Vercel, Production | `NEXT_PUBLIC_CLERK_PROXY_URL` | `https://saytrade-web.vercel.app/__clerk/` |
| FastAPI Cloud | `CLERK_FRONTEND_API` | `https://saytrade-web.vercel.app/__clerk` |

The matcher entry matters: the general pattern skips paths that look like files,
and Clerk's own endpoints end in `.json`, so `/__clerk/.well-known/jwks.json`
would never reach the middleware without it.

**The issuer becomes the proxy URL.** Ask Clerk directly instead of assuming:

```
curl -s https://saytrade-web.vercel.app/__clerk/.well-known/openid-configuration
  issuer:   https://saytrade-web.vercel.app/__clerk
  jwks_uri: https://saytrade-web.vercel.app/__clerk/.well-known/jwks.json
```

That is why `CLERK_FRONTEND_API` is the proxy URL and not the `clerk.…` host: the
token's `iss` claim carries it. `HttpJwksFetcher` builds the JWKS URL from the
issuer, and the same URL is reachable, so no code change was needed.

Proof the backend can reach the keys: send a well-formed token with a key id that
does not exist. The reply must be
`401 {"detail":"no signing key found for kid '…'"}` — that message is only
produced after a successful fetch. A `500` with `httpx.ConnectError` in the logs
means the fetch failed.

**Google sign-in.** The Authorized Redirect URI in Google must be the proxy's —
copy the value Clerk shows on the Google connection page. If it points at
`accounts.saytrade-web.vercel.app`, Google rejects the round trip.

Local development is unaffected and must stay unproxied: Clerk does not support
proxying for development instances, so `.env.local` keeps the development keys and
no `NEXT_PUBLIC_CLERK_PROXY_URL`. `npm run dev` and `uvicorn` work as before.

### Checking the full path

Sign in, record a note, and confirm it reaches `EXTRACTED` or `APPROVED`, not
`RECEIVED` forever.

---

## Things worth knowing

- **The free API scales to zero.** The first request after an idle period pays a
  cold start. Hobby's minimum replicas are pinned at 0 and cannot be raised.
- **Background work after a response is the one thing to watch here.** The voice
  pipeline runs in a Starlette `BackgroundTask`, after the response is sent, on an
  app that scales to zero. If an instance is torn down inside that window, the note
  stalls at `RECEIVED`. Retry it — the pipeline is idempotent per stage — and read
  the logs. If it happens often, the pipeline needs a real queue or an always-on
  instance.
- **Logs and metrics keep one day** on Hobby: `./.venv/bin/fastapi cloud logs`.
- **Vercel Hobby is for non-commercial use.** SayTrade shows a $5 plan, so a
  paying product belongs on Vercel Pro eventually ($20/month).
- **Neon's free tier is not a backup service.** 6-hour restore window, 0.5 GB.
- **Plans are still switched by hand**, as designed — no checkout yet:

  ```
  UPDATE users SET plan = 'PRO' WHERE clerk_sub = 'user_...';
  ```

- **Your local journal stays local.** The cloud database starts empty. To copy
  the local rows up later, `pg_dump` the container and `psql` into Neon.
- **Every live voice note spends real OpenAI money**, in the cloud exactly as
  locally. The 100/month guard is what protects it.
