# sayTrade — database schema v3 (long only)

Status: **applied**. This is the DDL that is live in the database (dumped from
`public` after migrations `b7d41c0f9e22` (the long-only rebuild) and
`c1e93b7a4d20` (the plan column), which sit on top of `5d8f9a1c2b4e`). The old
six-table design is gone: users, trades, executions, management_decisions,
journal_entries, trade_outcomes, usage_periods are dropped.

## The model in one paragraph

The trader speaks (or types) a note. The note is transcribed and the AI
proposes changes. The trader reviews and approves. Only then does the backend
apply anything, atomically. **Nothing derived is stored**: shares held, average
price, open P&L, realized P&L, R, the current stop and the current target are
all computed at read time from the change rows by the pure functions in
`app/domain/position.py`.

- Long only. A buy adds, a sell trims. There is no direction anywhere.
- One honest row per thing said: a note holds the trader's own words plus one
  AI summary line. A pure observation note has **zero** change rows.
- A mixed note ("I sold half and moved the stop to 9.5") produces **one** note
  and **two** change rows. Nothing is duplicated and nothing is split.
- Kinds: `OPEN, ADD, TRIM, STOP, TARGET, CLOSE`.
- The change rows are the approved truth — there is no `approved_payload`
  column. Status `APPROVED` on the note is the commit marker, and approval is
  idempotent.
- A missing number is a normal state: the note is extracted, the review screen
  highlights the gap, the trader fills it, and the approval then succeeds.

## Tables

### users

```sql
CREATE TABLE public.users (
    id            uuid DEFAULT uuidv7() NOT NULL,
    clerk_sub     character varying(64) NOT NULL,
    plan          character varying(16) DEFAULT 'FREE'::character varying NOT NULL,
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_clerk_sub UNIQUE (clerk_sub),
    CONSTRAINT ck_users_plan_allowed CHECK (plan IN ('FREE', 'PRO'))
);
```

One row per Clerk identity. The backend verifies the Clerk JWT against the
instance JWKS on every request and never trusts a client-supplied user id.

`plan` is **stored**, not derived: there is no checkout yet, so an operator
switches it by hand. Free holds **3 active trades**; Pro ($5 USD a month) lifts
the limit to unlimited. The limit is enforced only where a trade would be
**created** — a follow-up note on a trade the trader already holds is never
blocked, and a refused note approves the moment a slot frees up. The monthly AI
voice cap (100) is an abuse guard, not the product limit, and typed notes never
count against it.

### trades

```sql
CREATE TABLE public.trades (
    id            uuid DEFAULT uuidv7() NOT NULL,
    user_id       uuid NOT NULL REFERENCES public.users(id),
    symbol        character varying(20) NOT NULL,
    currency      character varying(8),          -- optional, AI-filled or empty
    opened_at     timestamp with time zone NOT NULL,
    closed_at     timestamp with time zone,
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    updated_at    timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trades_pkey PRIMARY KEY (id),
    CONSTRAINT ck_trades_closed_after_open
        CHECK (closed_at IS NULL OR closed_at >= opened_at)
);

CREATE INDEX ix_trades_user_opened_at
    ON public.trades (user_id, opened_at DESC);

-- one open trade per symbol per user
CREATE UNIQUE INDEX ux_trades_one_open_per_symbol
    ON public.trades (user_id, symbol) WHERE closed_at IS NULL;
```

There is **no** `status` column: a trade is open when `closed_at IS NULL`.
A trade is never deleted; it is closed by a `CLOSE` change.

### notes

```sql
CREATE TABLE public.notes (
    id                      uuid DEFAULT uuidv7() NOT NULL,
    user_id                 uuid NOT NULL REFERENCES public.users(id),
    trade_id                uuid REFERENCES public.trades(id),  -- context, may be null
    text                    text,          -- the trader's own words
    summary                 text,          -- ONE AI line, same language as the note
    source                  character varying(10) NOT NULL,     -- VOICE | TEXT
    audio_key               character varying(255),
    mime_type               character varying(64),
    duration_ms             integer,
    byte_size               integer,
    status                  character varying(20) NOT NULL,      -- see below
    failed_stage            character varying(20),              -- RECEIVE|STORAGE|...
    proposal                jsonb,          -- the AI draft, edited by the review
    error_message           text,
    transcription_provider  character varying(30),
    transcription_model     character varying(60),
    extraction_provider     character varying(30),
    extraction_model        character varying(60),
    approved_at             timestamp with time zone,
    created_at              timestamp with time zone DEFAULT now() NOT NULL,
    updated_at              timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notes_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_notes_user_created_at ON public.notes (user_id, created_at DESC);
CREATE INDEX ix_notes_trade_id        ON public.notes (trade_id, created_at);
CREATE INDEX ix_notes_allowance       ON public.notes (user_id, source, created_at);
```

Status is one of `RECEIVED, EXTRACTED, FAILED, APPROVED`. The free plan counts
this month's `VOICE` notes (`ix_notes_allowance` serves that count); the
allowance is consumed at receive, before any AI spend.

### changes

```sql
CREATE TABLE public.changes (
    id            uuid DEFAULT uuidv7() NOT NULL,
    note_id       uuid NOT NULL REFERENCES public.notes(id),
    kind          character varying(10) NOT NULL,   -- OPEN|ADD|TRIM|STOP|TARGET|CLOSE
    occurred_at   timestamp with time zone NOT NULL,
    shares        numeric(20,8),
    price         numeric(20,8),
    stop          numeric(20,8),
    target        numeric(20,8),
    created_at    timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT changes_pkey PRIMARY KEY (id),
    CONSTRAINT ck_changes_shares_positive CHECK (shares IS NULL OR shares > 0),
    CONSTRAINT ck_changes_price_positive  CHECK (price  IS NULL OR price  > 0),
    CONSTRAINT ck_changes_stop_positive   CHECK (stop   IS NULL OR stop   > 0),
    CONSTRAINT ck_changes_target_positive CHECK (target IS NULL OR target > 0)
);

CREATE INDEX ix_changes_note_id ON public.changes (note_id);
```

There is no `fraction` column. A spoken fraction ("sold half", "I have 30%
left") is converted to shares by the applier at approval time and stored as
`shares`.

## Decisions taken (recorded here so they are not re-litigated)

1. **One stop for the trade.** A part stop ("the SL for this portion is 10.5")
   is not a change row; it stays in the trader's own words. Each new stop
   replaces the previous one.
2. **No discipline or rule flag.** The trader's words are the record.
3. **One open trade per symbol per user**, enforced by the partial unique index.
4. **Positive numbers only** when a value is present (the four CHECKs).
5. **`closed_at` never precedes `opened_at`.**
6. **Currency is optional free text** on the trade, AI-filled from the
   trader's words when he says it, else empty.
7. **UTC storage**; date words ("yesterday", "today") are resolved against the
   recording time, with `Africa/Cairo` as the market timezone for date
   arithmetic.
8. **A missing number does not block the note.** It is saved, the review
   screen marks the gap, and the trader fills it in one tap.
9. The four tables above, not a 3-table variant: a mixed note needs the child
   rows to stay honest.
