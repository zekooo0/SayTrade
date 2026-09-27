# Deploy SayTrade for free

Four services, no credit card, no expiry:

| Piece | Where | Free tier in one line |
| --- | --- | --- |
| API (FastAPI) | Render, free web service | 512 MB, sleeps after 15 min idle, ~1 min to wake, 750 instance hours/month |
| Database (Postgres) | Neon, free plan | 0.5 GB, no expiry, compute sleeps after 5 min idle |
| Web (Next.js) | Vercel, Hobby | unlimited static, serverless functions |
| Audio (recordings) | Cloudinary | already configured, unchanged |

Render's own free Postgres expires 30 days after creation and is then deleted, so
this setup keeps the database on Neon instead.

Order matters: Neon gives the API its database, Render gives the web app its API
URL, Vercel gives the API its allowed origin.

---

## 1. Neon — the database

1. Sign in at neon.com (GitHub login is fine), create a project.
2. Region: **eu-central-1 (Frankfurt)** — same city as the Render service, so
   every query stays inside Europe.
3. Postgres version: 17 or 18. The app uses nothing version-specific.
4. Copy the connection string (Dashboard → Connect). It looks like:

   ```
   postgresql://USER:PASSWORD@ep-something-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```

5. Convert it for this app. Three edits, all required:

   - scheme `postgresql://` → `postgresql+asyncpg://` (async driver)
   - `sslmode=require` → `ssl=require` (asyncpg does not understand `sslmode`)
   - delete `channel_binding=require` if it is present

   ```
   postgresql+asyncpg://USER:PASSWORD@ep-something-123456.eu-central-1.aws.neon.tech/neondb?ssl=require
   ```

6. Take the **direct** host, not the pooled one: remove `-pooler` from the host
   name. SQLAlchemy keeps its own pool; the Neon pooler is for serverless clients.

Leave the database empty. The API creates the schema itself on first boot.

## 2. Render — the API

1. Sign in at render.com with GitHub. No card is asked for.
2. **New → Blueprint**, pick the `SayTrade` repo. Render reads `render.yaml` and
   shows one service, `saytrade-api`, on the free plan.
3. It asks for the values marked `sync: false`. Paste each one **in the Render
   dashboard**, never into a chat or a file:

   | Key | Value |
   | --- | --- |
   | `DATABASE_URL` | the converted Neon string from step 1.5 |
   | `CLERK_FRONTEND_API` | your Clerk instance URL, e.g. `https://mint-stud-8634.clerk.accounts.dev` |
   | `OPENAI_API_KEY` | your OpenAI key |
   | `CLOUDINARY_CLOUD_NAME` | your cloud name |
   | `CLOUDINARY_API_KEY` | your Cloudinary key |
   | `CLOUDINARY_API_SECRET` | your Cloudinary secret |
   | `CORS_ORIGINS` | `https://saytrade-web.vercel.app` — you can fill it in after step 3 |

   These are the same names as your local `.env`. Nothing else is required: the
   plan limits live in `app/config.py` with defaults.

4. Deploy. The start command runs `alembic upgrade head` first, so the tables
   exist before the server answers. Watch the log for
   `Application startup complete`.
5. Your API is at `https://<service-name>.onrender.com` — copy the real URL from
   the dashboard. Check it in a browser: `/health` must answer `{"status":"ok"}`.

Note: the first request after 15 idle minutes takes about a minute. Open the API
`/health` once before you record a note, or accept the wait.

## 3. Vercel — the web app

1. Push the frontend repo (already done: `zekooo0/saytrade-web`, private).
2. Sign in at vercel.com with GitHub, **Add New → Project**, import `saytrade-web`.
   Framework is detected as Next.js; keep the defaults.
3. Add the environment variables **before** the first deploy, because
   `NEXT_PUBLIC_*` values are baked into the build:

   | Key | Value |
   | --- | --- |
   | `NEXT_PUBLIC_API_URL` | `https://<your-render-service>.onrender.com` |
   | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | `pk_test_…` from your Clerk dashboard |
   | `CLERK_SECRET_KEY` | `sk_test_…` from your Clerk dashboard |

4. Deploy. You get `https://saytrade-web.vercel.app`.

## 4. Close the loop

Both ends must learn about the other one:

1. **CORS.** In Render, set `CORS_ORIGINS` to the Vercel URL
   (`https://saytrade-web.vercel.app`). Several origins are allowed, comma
   separated. Save: Render redeploys by itself.
2. **Clerk.** In the Clerk dashboard, add the Vercel domain under the instance's
   allowed origins / domains. Without it, sign-in fails on the live site — the
   Clerk development instance only trusts localhost out of the box.
3. **Check the full path:** open the site, sign in, record a note. If the note
   stays at `RECEIVED`, the API is awake but the pipeline failed — read the
   Render log.

---

## Things worth knowing

- **The free API sleeps.** After 15 idle minutes the next request waits about a
  minute. Fine for one trader, visible to a visitor.
- **750 instance hours/month.** A sleeping service does not spend them, so this
  is enough for one always-waking service.
- **`NEXT_PUBLIC_API_URL` is baked at build time.** Change it and you must
  redeploy the frontend; editing the variable alone is not enough.
- **Vercel Hobby is for non-commercial use.** SayTrade shows a $5 plan, so a
  paying product belongs on Vercel Pro eventually ($20/month). Free while it is
  a private project.
- **Render's free tier is not for production data.** No backups, one instance,
  possible restarts. Neon keeps the data (6-hour restore window on free).
- **Plans are still switched by hand**, as designed — no checkout yet:

  ```
  UPDATE users SET plan = 'PRO' WHERE clerk_sub = 'user_...';
  ```

- **Your local journal stays local.** The cloud database starts empty. To copy
  the local rows up later, use `pg_dump` against the container and `psql` against
  Neon.
- **Every live voice note spends real OpenAI money**, in the cloud exactly as
  locally. The 100/month guard is what protects it.
