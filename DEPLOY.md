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
| `CLERK_FRONTEND_API` | plain |
| `OPENAI_API_KEY` | secret |
| `CLOUDINARY_CLOUD_NAME` | plain |
| `CLOUDINARY_API_KEY` | secret |
| `CLOUDINARY_API_SECRET` | secret |
| `CORS_ORIGINS` | plain — `https://saytrade-web.vercel.app` |

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
DATABASE_URL="$NEON_URL" ./.venv/bin/alembic current      # expect c1e93b7a4d20 (head)
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

`NEXT_PUBLIC_*` values are baked into the client bundle at build time. Proven
locally: a build without the variable carries `localhost:8000` in four chunks, a
build with it carries the host in those same four. **Change the value and you must
redeploy.**

## 4. Close the loop

1. **CORS.** `CORS_ORIGINS` on the API is already the Vercel URL. Several origins
   are allowed, comma separated.
2. **Clerk.** Add the Vercel domain under the instance's allowed origins in the
   Clerk dashboard. Without it, sign-in fails on the live site — the Clerk
   development instance trusts only localhost out of the box.
3. **Check the full path:** sign in, record a note, confirm the note reaches
   `EXTRACTED` or `APPROVED`, not `RECEIVED` forever.

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
