# Trading Journal MVP — Database Schema

## Product Scope

The MVP focuses only on **trades that the trader actually enters**.

The product is not intended to replace TradingView or become a full trade-planning platform.

The core promise is:

> **Record your trades by talking. No spreadsheets, no forms.**

The trader enters a trade, records a short voice note, reviews what the AI extracted, edits anything necessary, and approves it.

---

# Domain Model

The MVP has five trading concepts:

```text
Trade
  │
  ├── Execution (many)
  ├── Management Decision (many)
  ├── Journal Entry (many)
  └── Trade Outcome (zero or one)
```

`Users` is an application-level dependency referenced by the trading tables; it is not part of the trading domain itself.

## Trade

A `Trade` represents a trading opportunity that the trader actually entered.

The MVP does **not** persist opportunities that were only observed but never entered.

The same Trade can contain multiple buys, sells, partial exits, and re-entries until the trader considers the trade completely finished.

## Execution

An actual transaction belonging to a Trade.

Examples:

```text
BUY  100 @ $105
BUY   50 @ $108
SELL  75 @ $115
SELL  75 @ $120
```

## Management Decision

A change to the trader's stop-loss or target while managing a Trade.

Actual exposure changes are represented by **Executions**, not Management Decisions.

## Journal Entry

The trader's original communication, primarily a voice recording in the MVP.

A Journal Entry is also the durable pipeline record for transcription and AI extraction before a Trade exists.

This is important because the first voice note can exist **before approval creates the Trade**.

## Trade Outcome

The final classification and reflection of a completed Trade.

---

# Persistence / Processing Flow

The pipeline is durable:

```text
Record voice
    ↓
Persist Journal Entry
    ↓
Transcribe
    ↓
Extract proposal
    ↓
User reviews / edits proposal
    ↓
Approve
    ↓
Backend validates approved payload
    ↓
Create/update Trade + child records
```

A Journal Entry may therefore exist without a `trade_id` while it is being processed.

This avoids losing:

- the audio
- the transcript
- the AI proposal
- processing state

if the app is backgrounded, the network drops, or the user abandons review.

The AI never writes directly to domain tables.

---

# 1. `users`

This is the minimal contract required by the trading schema.

If authentication already owns a `users` table, use that table rather than creating a duplicate.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | User identifier referenced by trading tables |
| `created_at` | `timestamptz` | NOT NULL | Account creation time |
| `updated_at` | `timestamptz` | NOT NULL | Last account update |

The authentication/profile fields are outside the scope of this MVP schema.

---

# 2. `trades`

The primary trading domain entity.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | Unique trade identifier |
| `user_id` | `uuid` | NOT NULL, FK → `users.id` | Owner |
| `symbol` | `varchar(20)` | NOT NULL | Stock ticker |
| `direction` | `varchar(10)` | NOT NULL | `LONG` / `SHORT` |
| `pattern` | `varchar(50)` | NOT NULL | Trader's identified chart pattern |
| `rationale` | `text` | NULL | Why the trader entered |
| `failure_point` | `numeric(20,8)` | NULL | Trader-defined initial failure/stop level |
| `target_price` | `numeric(20,8)` | NULL | Trader-defined initial target |
| `status` | `varchar(20)` | NOT NULL | `ACTIVE`, `COMPLETED` |
| `opened_at` | `timestamptz` | NOT NULL | When the trader entered |
| `completed_at` | `timestamptz` | NULL | When the trader marked the Trade complete |
| `created_at` | `timestamptz` | NOT NULL | Record creation time |
| `updated_at` | `timestamptz` | NOT NULL | Last update time |

### Constraints

```text
status = COMPLETED  ↔  completed_at IS NOT NULL
```

In PostgreSQL this should be enforced with:

```text
CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL))
```

### Lifecycle

- A Trade is created only when the user approves a voice-note proposal that represents an actual entered trade.
- `ACTIVE` means the Trade is still being managed.
- `COMPLETED` means the trader has explicitly approved that the Trade is finished.
- Partial exits do not complete a Trade.
- Re-entry remains part of the same Trade.
- The approved completion assertion is authoritative for Trade status.
- Derived net position should be checked as a warning before completion, but should not silently change the trader's declared status.
- `failure_point` is trader-defined. The system does not infer it.
- `pattern` represents what the trader saw, not what the AI independently determines.

### Pattern representation

For the MVP, `pattern` is a controlled vocabulary at the **application/API layer**, not a database `CHECK`.

Example values:

```text
CUP_AND_HANDLE
DOUBLE_BOTTOM
ASCENDING_TRIANGLE
...
```

This keeps analytics consistent without coupling the database to the AI extractor's exact vocabulary.

---

# 3. `executions`

Represents actual buy/sell transactions belonging to a Trade.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | Unique execution identifier |
| `trade_id` | `uuid` | NOT NULL, FK → `trades.id` | Related Trade |
| `side` | `varchar(10)` | NOT NULL | `BUY` / `SELL` |
| `quantity` | `numeric(20,8)` | NOT NULL, > 0 | Shares/units |
| `price` | `numeric(20,8)` | NOT NULL, > 0 | Execution price |
| `executed_at` | `timestamptz` | NOT NULL | Execution time |
| `reason` | `text` | NULL | Why the trader made the execution |
| `created_at` | `timestamptz` | NOT NULL | Record creation time |

### Position convention

For `LONG`:

```text
BUY  → increases position
SELL → decreases position
```

For `SHORT`:

```text
SELL → increases short position
BUY  → decreases short position
```

The backend must validate execution sequences against the Trade direction.

The database should not try to encode the complete execution sequence in a simple row-level `CHECK`.

### Exposure

Actual exposure changes are represented by executions.

Therefore:

```text
BUY / SELL = actual exposure change
```

There is no `EXPOSURE_CHANGE` management-decision type in the MVP.

This avoids mixing:

- prices and quantities in the same fields
- actual transactions with intended position sizes
- execution history with planning

---

# 4. `management_decisions`

Represents changes to the Trade's stop-loss or target.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | Unique decision identifier |
| `trade_id` | `uuid` | NOT NULL, FK → `trades.id` | Related Trade |
| `type` | `varchar(30)` | NOT NULL | `STOP_LOSS_CHANGE` / `TARGET_CHANGE` |
| `old_value` | `numeric(20,8)` | NULL | Previous price |
| `new_value` | `numeric(20,8)` | NULL | New price |
| `reason` | `text` | NOT NULL | Why the trader made the change |
| `decided_at` | `timestamptz` | NOT NULL | Decision time |
| `created_at` | `timestamptz` | NOT NULL | Record creation time |

Both `old_value` and `new_value` are **price-per-share values**.

Example:

```text
STOP_LOSS_CHANGE
old_value = 90
new_value = 94
reason = "New higher low formed"
```

The original `trades.failure_point` remains unchanged. Later modifications are preserved as management history.

---

# 5. `journal_entries`

Represents the trader's original voice input and the durable AI-processing state.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | Unique journal entry identifier |
| `user_id` | `uuid` | NOT NULL, FK → `users.id` | Owner |
| `trade_id` | `uuid` | NULL, FK → `trades.id` | Related Trade; NULL before Trade creation |
| `type` | `varchar(20)` | NOT NULL, default `VOICE` | `VOICE` for MVP |
| `status` | `varchar(20)` | NOT NULL | `RECEIVED`, `TRANSCRIBED`, `EXTRACTED`, `APPROVED`, `FAILED` |
| `audio_storage_key` | `text` | NOT NULL | Stable storage key for original recording |
| `mime_type` | `varchar(100)` | NOT NULL | Audio format |
| `duration_ms` | `integer` | NOT NULL, > 0 | Recording duration |
| `byte_size` | `bigint` | NOT NULL, > 0 | Audio size |
| `checksum` | `varchar(128)` | NULL | Optional content checksum |
| `transcript` | `text` | NULL | Transcribed voice note |
| `proposal` | `jsonb` | NULL | Original AI-extracted proposal |
| `approved_payload` | `jsonb` | NULL | User-edited payload that was actually approved |
| `transcription_provider` | `varchar(100)` | NULL | Transcription provider |
| `transcription_model` | `varchar(100)` | NULL | Transcription model/version |
| `extraction_provider` | `varchar(100)` | NULL | LLM/provider used for extraction |
| `extraction_model` | `varchar(100)` | NULL | Extraction model/version |
| `failed_stage` | `varchar(30)` | NULL | Stage that failed, when `status = FAILED` |
| `error_message` | `text` | NULL | Processing error information |
| `approved_at` | `timestamptz` | NULL | Approval time |
| `created_at` | `timestamptz` | NOT NULL | Recording creation time |
| `updated_at` | `timestamptz` | NOT NULL | Last processing/update time |

### Status lifecycle

```text
RECEIVED
   ↓
TRANSCRIBED
   ↓
EXTRACTED
   ↓
APPROVED
```

Any processing stage may transition to:

```text
FAILED
```

`failed_stage` distinguishes whether failure occurred during transcription or extraction.

### Why `proposal` and `approved_payload` both exist

The system needs to preserve the difference between:

```text
AI proposal
     vs.
User-approved data
```

Example:

```text
AI proposal:
target = 120

User edits:
target = 125

approved_payload:
target = 125
```

This provides an audit trail and allows future measurement of extraction accuracy.

### Storage

Use a stable **storage key**, not a permanent URL.

Example conceptually:

```text
voice-journal/{user_id}/{journal_entry_id}.m4a
```

URLs can change when storage/CDN infrastructure changes; storage keys should not.

### `type`

`VOICE` is the only MVP value.

The column can remain because future journal types may include text, import, or broker-generated entries. If the product remains permanently voice-only, it can later be removed.

---

# 6. `trade_outcomes`

Represents the final classification and reflection of a completed Trade.

The previous `opportunity_outcomes` name is intentionally removed. The vocabulary is now consistently based on **Trade**.

| Column | PostgreSQL Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | PK | Unique outcome identifier |
| `trade_id` | `uuid` | NOT NULL, UNIQUE, FK → `trades.id` | Related Trade |
| `type` | `smallint` | NULL, CHECK (`type` BETWEEN 1 AND 4) | Methodology classification |
| `result` | `varchar(20)` | NOT NULL | Trader-declared `WIN`, `LOSS`, `BREAKEVEN` |
| `lesson` | `text` | NULL | Main lesson |
| `notes` | `text` | NULL | Additional reflection |
| `completed_at` | `timestamptz` | NOT NULL | When outcome was recorded |
| `created_at` | `timestamptz` | NOT NULL | Record creation time |

---

# Type 1–4

These classifications describe what happened after the breakout.

- **Type 1:** Breakout → continues to target without retesting neckline.
- **Type 2:** Breakout → retests neckline without breaking it → continues to target.
- **Type 3:** Breakout → breaks neckline but does not reach failure point → recovers → reaches target.
- **Type 4:** Breakout → breaks neckline → reaches failure point.

The trader records the type manually.

The MVP does **not** store `neckline` because the application does not need to reconstruct or validate the chart path. The type is a trader-declared outcome based on what happened in the market.

If future functionality needs to validate or suggest Type 1–4 automatically, `neckline` and market-path data can be added later.

---

# P&L

P&L is derived from executions and is **not stored** as a duplicated column.

## Gross realized P&L

For a `LONG` Trade:

```text
Gross P&L =
Σ(SELL quantity × SELL price)
−
Σ(BUY quantity × BUY price)
```

For a `SHORT` Trade:

```text
Gross P&L =
Σ(SELL quantity × SELL price)
−
Σ(BUY quantity × BUY price)
```

The same cash-flow equation works for both directions; the interpretation of whether the result is profitable depends on the execution sequence and position convention.

For a completed Trade, net position should be zero before treating the result as fully realized.

### Fees and commissions

The MVP currently has **no fee/commission fields**.

Therefore:

> P&L in the MVP is **gross realized P&L**, excluding commissions, fees, taxes, and slippage.

If net P&L becomes important, execution-level costs should be added later rather than hidden inside analytics.

---

# R Multiple

R multiple is derived and is not stored.

For a Trade with an initial entry:

```text
Initial risk per share =
ABS(initial entry price − initial failure point)
```

```text
Initial planned risk =
initial entry quantity × initial risk per share
```

```text
Realized R =
gross realized P&L / initial planned risk
```

### Undefined R

R multiple is undefined when:

- `failure_point` is NULL
- initial entry price is unavailable
- initial planned risk is zero

The analytics layer must return `NULL` rather than inventing a value.

### Scaling and re-entry convention

For the MVP, the R denominator is based on the **initial entry execution and the original failure point**.

Additional scale-ins, partial exits, and re-entries remain part of the same Trade and affect realized P&L, but they do not retroactively change the original risk denominator.

This is a deliberate MVP convention. A more sophisticated risk-adjusted calculation can be introduced later.

---

# Result vs Derived P&L

`trade_outcomes.result` is the **trader-declared classification**.

For example:

```text
result = WIN
gross P&L = -$20
```

The system does **not silently overwrite** the trader's declared result based on the calculated P&L.

Instead, analytics can flag:

```text
Declared result disagrees with derived P&L
```

This preserves the trader's journal while making inconsistencies visible.

---

# Completion Semantics

A Trade is completed when the trader explicitly records and approves that the Trade is finished.

Example:

> "I'm completely out. Sold the remaining shares."

The approved Journal Entry is authoritative for the completion assertion.

The backend should calculate current net position from executions and warn if:

```text
Trader says COMPLETED
but
derived position != 0
```

The system should not silently rewrite the trader's history.

---

# Audit / Correction Policy

The child tables represent historical events and should be treated as **append-oriented history**.

The MVP needs a correction mechanism without silently destroying the original event.

Recommended approach:

- Never silently overwrite a historical execution or management decision.
- If an event is wrong, preserve the original record and mark it as voided/corrected.
- A future correction event can point to the original event.
- Analytics should exclude voided records.

If correction requirements remain very small in the first release, this can initially be implemented as a deliberate service-level rule rather than adding a full correction-event model.

A `voided_at` field is the simplest future extension for executions, management decisions, and journal entries.

---

# Indexes

PostgreSQL does **not** automatically create indexes for foreign-key columns.

Recommended MVP indexes:

```text
trades(user_id, status)
trades(user_id, opened_at DESC)

executions(trade_id)
management_decisions(trade_id)
journal_entries(trade_id)
journal_entries(user_id)
```

`trade_outcomes(trade_id)` is already indexed by the `UNIQUE` constraint.

If journal processing is performed by polling/queue workers, also consider:

```text
journal_entries(status, created_at)
```

---

# Account / Broker

The MVP does not require an account or broker relationship.

A trader with multiple brokerage accounts cannot currently distinguish them at the Trade level.

This is intentionally deferred.

A future nullable field such as:

```text
account_id
```

can be added when broker/account support becomes relevant.

---

# AI Trust Model

The system has a clear hierarchy:

```text
🎙️ Journal Entry
    = What the trader said

🤖 AI Proposal
    = What the AI understood

✋ Approved Payload
    = What the trader actually approved

🗄️ Domain Tables
    = Source of truth for trading history
```

The AI can propose changes.

The user approves or edits them.

The backend validates the approved payload.

Only then are domain tables changed.

The AI never directly writes to PostgreSQL.

---

# Example End-to-End Trade

## 1. Entry

Trader says:

> "I bought 100 AAPL at 105 because the cup and handle breakout confirmed. My stop is 90 and my target is 120."

The Journal Entry is persisted first:

```text
status = RECEIVED
trade_id = NULL
audio_storage_key = ...
```

After transcription:

```text
status = TRANSCRIBED
transcript = ...
```

After extraction:

```text
status = EXTRACTED

proposal:
{
  symbol: AAPL,
  direction: LONG,
  pattern: CUP_AND_HANDLE,
  quantity: 100,
  entry_price: 105,
  failure_point: 90,
  target_price: 120
}
```

The user reviews and approves.

The backend creates:

```text
Trade
Execution → BUY 100 @ 105
Journal Entry → linked to Trade
```

---

## 2. Scale In

Trader says:

> "I bought another 50 at 108 because resistance broke."

Creates:

```text
Execution → BUY 50 @ 108
Journal Entry → linked to Trade
```

---

## 3. Stop Adjustment

Trader says:

> "I moved my stop from 90 to 94 because we formed a higher low."

Creates:

```text
Management Decision
type = STOP_LOSS_CHANGE
old_value = 90
new_value = 94
reason = "New higher low formed"
```

---

## 4. Exit

Trader says:

> "I'm completely out. Sold the remaining 150 at 120. It was a Type 2 trade. My lesson is that I should have held longer."

Creates:

```text
Execution → SELL 150 @ 120

Trade Outcome
type = 2
result = WIN
lesson = "I should have held longer"

Trade
status = COMPLETED
```

The Journal Entry remains linked to the Trade and preserves the original audio, transcript, AI proposal, and approved payload.

---

# Explicitly Out of MVP

The MVP does **not** include:

- Unentered opportunity journaling
- Chart drawing
- Advanced technical analysis
- TradingView replacement
- Trading signals
- AI trading recommendations
- Broker integration
- Automatic market-path classification
- Backtesting
- Social/community features
- Complex planning workflows
- Real-time voice chat
- Vector database
- Persistent `journal_commands` table

The initial architecture is intentionally a **durable voice journal + structured trade history**, not a complete trading platform.


---

# Product & Domain Decisions — Questions and Answers

This section captures the key questions, decisions, and reasoning that led to the current MVP schema. It is intentionally kept in the spec so future implementation decisions stay aligned with the product/domain model.

---

## Q1. What exactly is this product?

**Answer:** A voice-first trading journal.

The user records a short voice note about a trade instead of filling out a spreadsheet or form.

The core positioning is:

> **Record your trades by talking. No spreadsheets, no forms.**

The product is primarily for recording and understanding the trader's own behavior and performance.

It is **not** an AI trading advisor.

---

## Q2. Is the product a voice chatbot?

**Answer:** No.

Voice is the input mechanism, not a conversational AI experience.

The intended interaction is:

```text
Record 30–60 sec
    ↓
Transcribe
    ↓
Extract structured information
    ↓
Review
    ↓
Edit if needed
    ↓
Approve
```

There is no requirement for a real-time voice agent or voice conversation.

This keeps the UX simpler and avoids unnecessary voice-agent cost and complexity.

---

## Q3. Why use voice at all?

**Answer:** Because traders already have information in their heads that is annoying to enter into spreadsheets.

A trader can naturally say:

> "I bought 100 shares of AAPL at 105 after the breakout confirmed. My stop is 90 and target is 120."

The system can turn that into structured data.

The important product advantage is reducing journaling friction.

Voice is the **wedge**. It does not need to be the permanent moat.

A potential long-term advantage is accumulated structured behavioral data that enables personalized analytics, but this should not be overstated as a moat in the MVP.

---

## Q4. What problem is the MVP solving?

**Answer:** Making it easy for a trader to record actual trades and the reasoning behind their decisions.

The MVP should answer questions such as:

- What trades did I take?
- Why did I enter?
- How did I manage them?
- Why did I add or reduce exposure?
- Did I move my stop or target?
- How did the trade finish?
- What did I learn?
- Which patterns or behaviors perform well for me?

It should not try to answer:

> "What stock should I buy?"

---

## Q5. Should the user journal opportunities that they notice but never enter?

**Answer:** No, not in the MVP.

The scope was deliberately narrowed to:

> **Only journal trades that the trader actually enters.**

If a trader sees a Cup & Handle setup but never buys it, there is no Trade record.

This removes a large amount of unnecessary complexity around:

- identified opportunities
- abandoned opportunities
- opportunity lifecycle
- setup tracking before entry

---

## Q6. Should the main entity be called `Opportunity` or `Trade`?

**Answer:** `Trade`.

Once the MVP only records opportunities that were actually entered, `Opportunity` no longer provides useful distinction.

The domain becomes:

```text
Trade
 ├── Executions
 ├── Management Decisions
 ├── Journal Entries
 └── Trade Outcome
```

This also avoids mixed vocabulary such as `trades + opportunity_outcomes`.

---

## Q7. Does a partial exit create a new Trade?

**Answer:** No.

A Trade remains the same Trade until the trader is completely out.

Example:

```text
BUY 100
SELL 50
BUY 50
SELL 100
```

All of these executions belong to the same Trade.

---

## Q8. Does re-entering after a partial exit create a new Trade?

**Answer:** No.

A re-entry remains part of the same Trade.

This is important because the trader is still managing the same original thesis/opportunity.

---

## Q9. When is a Trade considered complete?

**Answer:** When the trader explicitly says/approves that the Trade is completely finished.

For example:

> "I'm completely out."

The approved user action is authoritative for completion.

The backend should still calculate the derived position from executions and warn if it does not equal zero.

The system should not silently change the user's declared history.

---

## Q10. Why should the trader's completion statement be authoritative instead of net position?

**Answer:** Because the product is a journal of the trader's decisions and statements, not a broker reconciliation system.

A missing or incorrectly transcribed execution should not cause the application to silently rewrite the trader's declared state.

Therefore:

```text
Approved trader statement → authoritative
Derived position → validation / warning
```

Broker reconciliation can be introduced later.

---

## Q11. What is an Execution?

**Answer:** An Execution is an actual transaction.

Examples:

```text
BUY 100 @ 105
BUY 50 @ 108
SELL 75 @ 115
SELL 75 @ 120
```

Execution is the correct place to represent actual exposure changes.

---

## Q12. Why not store `position_size` directly on Trade?

**Answer:** Because position size changes over time.

A trader may:

```text
BUY 100
BUY 50
SELL 75
BUY 25
SELL 100
```

A single `position_size` field would lose the historical sequence.

Current position should be derived from executions.

---

## Q13. What does `EXPOSURE_CHANGE` mean?

**Answer:** It was removed.

The earlier design had an `EXPOSURE_CHANGE` management-decision type, but that was ambiguous.

If the trader actually changes exposure:

```text
BUY / SELL
```

that is an Execution.

If it represents an intended future position size, that is planning, and detailed planning is outside the MVP.

There was also a unit problem:

```text
STOP_LOSS_CHANGE → dollars/share
TARGET_CHANGE    → dollars/share
EXPOSURE_CHANGE  → shares
```

Using the same `old_value` / `new_value` columns for different units was not desirable.

Therefore the MVP only has:

```text
STOP_LOSS_CHANGE
TARGET_CHANGE
```

in `management_decisions`.

---

## Q14. What is a Management Decision?

**Answer:** A historical decision to change the management parameters of a Trade.

Example:

> "I moved my stop from 90 to 94 because we formed a higher low."

This becomes:

```text
type = STOP_LOSS_CHANGE
old_value = 90
new_value = 94
reason = "New higher low formed"
```

The original stop/failure point on the Trade is not overwritten.

---

## Q15. Why preserve the original failure point?

**Answer:** Because the original plan matters for performance analysis.

If the trader starts with:

```text
Failure point = 90
```

and later changes it to:

```text
94
```

we need to know both:

```text
Original plan = 90
Management decision = 90 → 94
```

Otherwise future analytics cannot distinguish the initial plan from later behavior.

---

## Q16. What is the failure point?

**Answer:** The trader-defined point at which the original trade thesis has failed.

In the user's trading methodology, this is often related to the last bottom before the breakout, but the application must **not infer it**.

It is a value supplied by the trader.

---

## Q17. Should the system calculate the failure point automatically?

**Answer:** No.

The failure point is a trader decision.

The MVP should record what the trader says rather than attempting to interpret the chart and decide where the failure point should be.

---

## Q18. Should the app store the neckline?

**Answer:** Not in the MVP.

The methodology's Type 1–4 classification refers to behavior around the neckline:

```text
Type 1 → no neckline retest
Type 2 → neckline retest without breaking
Type 3 → neckline breaks but failure point is not reached
Type 4 → failure point is reached
```

However, the trader explicitly records the final Type.

The MVP does not have reliable market-path/chart data and does not attempt to validate or infer the Type.

Therefore `neckline` is intentionally omitted.

If future functionality needs automatic Type suggestions or validation, `neckline` and market-path data can be added later.

---

## Q19. What is the difference between Pattern and Type?

**Answer:**

**Pattern = what the trader saw before/during entry.**

Examples:

```text
Cup & Handle
Double Bottom
Ascending Triangle
```

**Type = what happened after the breakout.**

Examples:

```text
Type 1
Type 2
Type 3
Type 4
```

They answer different questions and should remain separate.

---

## Q20. What are Type 1–4?

**Answer:**

### Type 1

```text
Breakout
  ↓
Continues to target
```

No neckline retest.

### Type 2

```text
Breakout
  ↓
Retests neckline
  ↓
Does not break neckline
  ↓
Continues to target
```

### Type 3

```text
Breakout
  ↓
Retests neckline
  ↓
Breaks neckline
  ↓
Does not reach failure point
  ↓
Recovers
  ↓
Reaches target
```

### Type 4

```text
Breakout
  ↓
Retests neckline
  ↓
Breaks neckline
  ↓
Reaches failure point
```

The user records the Type manually.

---

## Q21. Should the system infer Type 1–4 from executions?

**Answer:** No.

Executions alone do not contain the complete market path.

For example, an execution history cannot reliably tell us whether price temporarily broke the neckline between two executions.

Automatic classification would require market data/chart-path information.

That is outside the MVP.

---

## Q22. Should the app replace TradingView?

**Answer:** No.

TradingView is where the trader performs chart analysis and planning.

The journal should focus on:

```text
What I saw
What I did
Why I did it
What happened
What I learned
```

The MVP should not rebuild:

- chart drawing
- technical-analysis tools
- detailed setup planning
- complex chart workflows

---

## Q23. Should the user enter all the details manually before recording a voice note?

**Answer:** No.

Voice is intended to reduce form-filling.

The trader can simply speak naturally.

The AI extracts the relevant structured fields and presents them for review.

---

## Q24. Should AI be allowed to directly modify the database?

**Answer:** No.

The AI is an extraction layer.

The trust model is:

```text
Trader's voice
    ↓
Transcript
    ↓
AI proposal
    ↓
User review/edit
    ↓
Approved payload
    ↓
Backend validation
    ↓
Domain database
```

The LLM must never have direct database-write authority.

---

## Q25. Why persist the Journal Entry before Trade creation?

**Answer:** Because the first voice note can arrive before the Trade exists.

The actual flow is:

```text
Record
  ↓
Upload audio
  ↓
Transcribe
  ↓
Extract
  ↓
Review
  ↓
Approve
  ↓
Create Trade
```

During the first stages there is no Trade to reference.

Therefore:

```text
journal_entries.trade_id = NULL
```

is valid while processing.

This also protects against:

- app being killed
- mobile app being backgrounded
- network failures
- abandoned review
- transcription failure
- extraction failure

The audio and processing state are not lost.

---

## Q26. Why not create the Trade immediately when recording starts?

**Answer:** Because that would contradict the domain rule:

> A Trade only exists after the trader actually enters the trade.

The user could record a note and then cancel or change their mind.

Creating a domain Trade before approval would create false trading history.

The Journal Entry staging state solves this without violating the domain rule.

---

## Q27. Why not keep AI proposals only in memory?

**Answer:** Because mobile workflows are not reliably continuous.

The user may:

- background the app
- lose connectivity
- kill the application
- return later

A temporary in-memory proposal could be lost.

Persisting the proposal as `jsonb` on `journal_entries` is cheap and makes the pipeline durable.

---

## Q28. Why not create a `journal_commands` table?

**Answer:** The MVP does not need one.

The AI proposal can be persisted directly as JSON on the Journal Entry:

```text
journal_entries.proposal
```

The user's final edited version can be stored as:

```text
journal_entries.approved_payload
```

This gives the necessary audit trail without introducing another domain table.

A dedicated command/event system can be introduced later if the workflow becomes more complex.

---

## Q29. Why store both `proposal` and `approved_payload`?

**Answer:** To preserve the distinction between AI interpretation and user-approved truth.

Example:

```text
AI:
target = 120

User:
changes target to 125

Approved:
target = 125
```

This allows the product to later measure:

- extraction accuracy
- which fields users frequently correct
- which patterns the AI struggles with
- whether a particular model performs better

It also supports the trust claim that AI does not silently change records.

---

## Q30. What does `Journal Entry` mean compared with `AI Extraction`?

**Answer:**

```text
Journal Entry = what the trader said
AI Proposal   = what the AI understood
Approved Data = what the trader accepted
Domain Tables = source of truth
```

They are deliberately different concepts.

---

## Q31. What happens if transcription fails?

**Answer:** The Journal Entry remains persisted.

Its state becomes:

```text
FAILED
```

and the failed stage/error information is recorded.

This is preferable to using:

```text
transcript = NULL
```

to mean both "not processed" and "failed".

---

## Q32. Why store transcription/extraction provider and model?

**Answer:** Because AI behavior changes over time.

Recording provider/model information allows future analysis of:

- extraction quality
- failure rates
- model changes
- migration between providers
- cost/performance

This information is operational metadata, not part of the Trade domain.

---

## Q33. Why store audio metadata?

**Answer:** At minimum, duration and format are useful.

Duration can support:

- UI display
- cost estimation
- validation
- transcription limits

Format matters because transcription services accept specific audio formats.

Byte size helps with storage and upload validation.

A checksum can help detect duplicate uploads.

---

## Q34. Why store an audio storage key instead of a URL?

**Answer:** URLs can change when storage infrastructure changes.

A stable storage key can remain:

```text
voice-journal/{user_id}/{journal_entry_id}.m4a
```

while the public/private URL generation mechanism changes.

This avoids coupling the database to a specific CDN or storage provider.

---

## Q35. Should `journal_entries.type` exist if the only MVP value is VOICE?

**Answer:** It is optional but acceptable.

The MVP has only:

```text
VOICE
```

Today that is somewhat redundant.

Keeping it gives an easy future path for:

```text
TEXT
IMPORT
BROKER
```

If the product remains permanently voice-only, the column can later be removed.

---

## Q36. Should Pattern be free text or an enum?

**Answer:** Use a controlled vocabulary at the application/API layer.

For example:

```text
CUP_AND_HANDLE
DOUBLE_BOTTOM
ASCENDING_TRIANGLE
```

Do not enforce the vocabulary with a database `CHECK` in the MVP.

The AI may produce natural-language variations, so the API/application layer should normalize them.

This gives analytics consistency without tightly coupling the DB to the extractor.

---

## Q37. How should P&L be stored?

**Answer:** It should not be stored as a duplicated value.

P&L is derived from executions.

For both LONG and SHORT trades, gross cash-flow P&L can be expressed as:

```text
Gross P&L =
Σ(SELL quantity × SELL price)
−
Σ(BUY quantity × BUY price)
```

For a completed Trade, net position should be zero before considering the Trade fully realized.

---

## Q38. Does this P&L include fees?

**Answer:** No.

The MVP has no commission/fee fields.

Therefore:

> P&L is **gross realized P&L**.

It excludes:

- commissions
- exchange fees
- taxes
- slippage

If net P&L becomes important, costs should be added explicitly at the execution level.

---

## Q39. Why not store P&L in `trade_outcomes`?

**Answer:** Because it would duplicate information that already exists in executions.

If execution data changes, a stored P&L can become stale.

One source of truth is preferable:

```text
Executions
    ↓
Derived P&L
```

---

## Q40. How is R multiple calculated?

**Answer:**

First:

```text
Initial risk per share =
ABS(initial entry price − initial failure point)
```

Then:

```text
Initial planned risk =
initial entry quantity × initial risk per share
```

Then:

```text
Realized R =
gross realized P&L / initial planned risk
```

---

## Q41. What if failure_point is NULL?

**Answer:** R multiple is undefined.

The analytics layer should return:

```text
NULL
```

rather than inventing a value.

R is also undefined when:

- there is no initial entry price
- initial planned risk is zero

---

## Q42. How does R work with scale-ins, partial exits, and re-entry?

**Answer:** The MVP uses a deliberately simple convention.

The R denominator is based on:

```text
initial entry execution
+
original failure point
```

Additional scale-ins, partial exits, and re-entries remain part of the same Trade and affect realized P&L.

They do not retroactively change the original risk denominator.

This is an MVP convention, not a claim that it is the only valid trading-journal methodology.

---

## Q43. Can `result = WIN` disagree with calculated P&L?

**Answer:** Yes.

`trade_outcomes.result` is trader-declared.

For example:

```text
result = WIN
derived P&L = -$20
```

The system should not silently overwrite the user's journal.

Instead, analytics can flag the inconsistency.

This keeps the user's declaration intact while making data-quality problems visible.

---

## Q44. What determines whether LONG/SHORT executions are valid?

**Answer:** Direction determines the position convention.

For LONG:

```text
BUY  → increases position
SELL → decreases position
```

For SHORT:

```text
SELL → increases short position
BUY  → decreases short position
```

The backend/application layer validates the execution sequence.

A simple database row-level CHECK cannot fully validate a multi-row trading sequence.

---

## Q45. Why support SHORT if the current strategy is LONG-only?

**Answer:** The current strategy is long-only, but the schema can support both without much extra complexity.

This avoids unnecessarily baking the current strategy into the data model.

The application can initially expose only LONG.

---

## Q46. Should the database prevent invalid execution sequences?

**Answer:** Domain validation belongs primarily in the backend service.

For example, the backend should prevent a LONG Trade from selling more shares than are currently held unless the product intentionally supports shorting.

Cross-row validation is more naturally handled by the application transaction than by simple CHECK constraints.

---

## Q47. What happens if a trader says they sold 150 shares but meant 15?

**Answer:** Historical records should not be silently overwritten.

The MVP should have an explicit correction/void stance.

The preferred direction is:

```text
Original event
    ↓
Mark as voided/corrected
    ↓
Create the correct event
```

rather than destroying the original audit trail.

A simple `voided_at` approach can be added if correction requirements remain small.

---

## Q48. Why is append-oriented history important?

**Answer:** Because the product's value includes understanding how the trader behaved.

If a historical decision is simply overwritten, the journal loses evidence of what actually happened.

Preserving history supports:

- behavioral analysis
- AI accuracy measurement
- debugging
- user trust
- future auditability

---

## Q49. Do we need broker integration in the MVP?

**Answer:** No.

The MVP is manually journaled through voice.

Broker integration can be added later.

This keeps the first product focused on proving:

> Does voice make journaling dramatically easier?

---

## Q50. Do we need an account/broker field now?

**Answer:** No.

A trader with multiple brokerage accounts cannot distinguish them in the MVP.

This is acceptable for the first version.

A future nullable:

```text
account_id
```

can be introduced when multi-account or broker integrations become important.

---

## Q51. Do we need a vector database?

**Answer:** No.

The MVP's source of truth is structured PostgreSQL data plus journal text/audio.

PostgreSQL is sufficient for:

- trade history
- analytics
- filtering
- aggregation
- basic journal search

A vector database can be considered later if semantic search or retrieval across large amounts of journal text becomes valuable.

---

## Q52. Do we need 100+ trading statistics in the MVP?

**Answer:** No.

The initial analytics should be based on the canonical data model.

Start with useful derived metrics such as:

- realized P&L
- R multiple
- win rate
- average win
- average loss
- results by pattern
- results by Type
- management behavior
- trade duration

More sophisticated analytics should be added only when the underlying data proves they are useful.

---

## Q53. What is the most important architectural principle?

**Answer:** Separate **what the trader said** from **what the application considers fact**.

The architecture is:

```text
Voice
  ↓
Journal Entry
  ↓
Transcript
  ↓
AI Proposal
  ↓
User Review / Edit
  ↓
Approved Payload
  ↓
Backend Validation
  ↓
Domain Tables
```

The LLM is probabilistic.

The domain database must remain deterministic and validated.

---

## Q54. What are the final MVP tables?

**Answer:**

```text
users
trades
executions
management_decisions
journal_entries
trade_outcomes
```

`users` is an application/auth dependency.

The five trading-domain tables are:

```text
trades
executions
management_decisions
journal_entries
trade_outcomes
```

---

## Q55. What is intentionally NOT represented in the MVP?

**Answer:**

```text
Unentered opportunities
Chart drawings
Advanced chart analysis
Trading signals
AI trade recommendations
Broker integration
Automatic Type 1–4 classification
Backtesting
Social/community features
Complex planning workflows
Real-time voice chat
Vector database
Persistent journal_commands
```

The MVP is intentionally:

> **A durable voice journal + structured trade history + useful personal analytics.**

# Backend Implementation Specification

## Goal
Build the MVP backend for a voice-first trading journal. AI proposes structured actions; the user reviews/edits; the backend validates and atomically applies the approved actions. The LLM must never write directly to domain tables.

Architecture:
VOICE INPUT → JOURNAL ENTRY → TRANSCRIPT → AI EXTRACTION → USER REVIEW/EDIT → APPROVAL → BACKEND VALIDATION → DOMAIN TRANSACTION → POSTGRESQL

## Core application flow
1. Receive voice note and immediately persist a `journal_entries` row with `RECEIVED` and `trade_id = NULL` when unresolved.
2. Transcribe. Save transcript/provider/model and set `TRANSCRIBED`; on failure set `FAILED` with `failed_stage = TRANSCRIPTION`.
3. Extract structured actions. Save proposal/provider/model and set `EXTRACTED`; on failure set `FAILED` with `failed_stage = EXTRACTION`.
4. Client presents proposal for review/editing.
5. Approval sends the edited payload to the backend. Backend authenticates the user, verifies ownership, validates every action, and applies all actions in one PostgreSQL transaction.
6. On success save the exact approved payload, set `APPROVED`, and set `approved_at`.
7. If any action fails validation, roll back the entire transaction. Keep the Journal Entry available for correction/retry.

## AI action model
The extraction layer should return actions, not replacement database objects. MVP actions:

- `CREATE_TRADE`: creates a real Trade and its initial execution. Required: symbol, direction, pattern, opened_at, and at least one execution. Optional: rationale, failure_point, target_price.
- `ADD_EXECUTION`: trade_id, side, quantity, price, executed_at; optional reason.
- `CHANGE_STOP`: trade_id, new_value, reason; optional old_value for conflict detection.
- `CHANGE_TARGET`: trade_id, new_value, reason; optional old_value for conflict detection.
- `ADD_OUTCOME`: trade_id, result; optional type, lesson, notes.
- `COMPLETE_TRADE`: trade_id, completed_at.

Apply actions in the approved payload order. A note may contain several actions. Example: final sell → outcome → completion. All must commit or all must roll back.

## New trade logic
A Trade is created only after the user has actually entered a position. Never create an opportunity record. The first approved `CREATE_TRADE` must contain an actual execution. Persist the Journal Entry before creating the Trade so the original audio/transcript survives processing failures.

## Resolving later notes
Do not rely on the LLM alone to identify the Trade. If recording from an active Trade screen, provide that Trade ID as context. If recording from a general journal screen, a model suggestion can help, but the backend must resolve/verify the Trade and ownership before mutation. Never trust a user-supplied `user_id` or arbitrary Trade ID.

## Domain validation
### Trade
- symbol non-empty and normalized
- direction `LONG` or `SHORT`
- pattern is one of the application's supported values
- opened_at valid
- status/completed_at invariant enforced
- creation requires at least one execution

### Execution
- quantity > 0
- price > 0
- side `BUY` or `SELL`
- executed_at valid
- Trade belongs to authenticated user
- LONG: BUY increases position, SELL decreases it
- SHORT: SELL increases short exposure, BUY decreases it

Calculate implied net position after executions. Do not silently reinterpret the user's completion assertion merely because derived position differs; surface inconsistencies according to product policy.

### Management decision
- type `STOP_LOSS_CHANGE` or `TARGET_CHANGE`
- reason required
- new value valid
- when old_value is supplied, compare it with the authoritative current value to detect stale/conflicting updates
- preserve original Trade failure point/target; management decisions are history, not overwrites

### Outcome
- one outcome per Trade
- result `WIN`, `LOSS`, or `BREAKEVEN`
- type, if supplied, is 1–4
- type and result are trader-declared; never infer them from executions
- if derived P&L disagrees with declared result, flag the discrepancy rather than overwrite the declaration

### Completion
- Trade must be ACTIVE
- completed_at required when completed
- normal new executions/management decisions are rejected after completion
- reopening/correction should be a future explicit workflow, not a silent mutation

## Stop/target semantics
`trades.failure_point` and `trades.target_price` represent the original plan. The latest applicable management decision represents the current value. Never destroy the original plan. Example: 90 → 94 remains historical even when 94 is current.

## Type 1–4
Store the trader's declared type exactly. Do not infer it from executions because executions do not contain market-path information. No neckline field is required in MVP.

- Type 1: breakout continues to target without retesting neckline.
- Type 2: breakout retests neckline without breaking it, then reaches target.
- Type 3: breakout retests/breaks neckline but does not reach failure point, then recovers to target.
- Type 4: breakout retests/breaks neckline and reaches failure point.

## P&L and R
Do not persist duplicated calculated P&L in MVP.

Gross realized P&L for both directions:
`Σ(SELL qty × price) − Σ(BUY qty × price)`

MVP excludes fees, commissions, taxes, and slippage. Fully closed trades should have zero net position.

Initial risk/share:
`ABS(initial entry price − original failure point)`

Initial planned risk:
`initial entry quantity × initial risk/share`

Realized R:
`gross realized P&L / initial planned risk`

Return NULL for R when failure point, initial entry, or non-zero risk is unavailable. Scale-ins/partial exits affect P&L but do not change the initial R denominator in this MVP convention.

## Transaction boundary
Each approved Journal Entry is one domain transaction. Example: ADD_EXECUTION + CHANGE_STOP must both succeed or both roll back. Never partially apply an approved voice note.

## Idempotency
Approval must be idempotent. Use the Journal Entry ID as the identity of the approval operation. Repeated approval requests after a successful approval must not create duplicate executions, outcomes, or management decisions. A timeout/retry must return the existing successful result where possible.

## Concurrency
Prevent stale management updates from silently overwriting newer decisions. For example, two changes based on stop=90 must not both silently win. Use PostgreSQL transaction locking or optimistic concurrency. The invariant is that an approved decision must not silently overwrite a newer state the user did not review.

## History/corrections
Favor append-oriented history. Do not silently rewrite executions, management decisions, journal history, or approved payloads. A future correction workflow may void/replace a record, and analytics should exclude explicitly voided records. Do not implement full event sourcing for MVP.

## Failure/retry handling
Keep original audio and transcript when processing fails. Store `FAILED`, `failed_stage`, and a safe diagnostic. Retry the same Journal Entry where possible instead of creating duplicates.

## Authentication/authorization
Every request is scoped to the authenticated user. Trade ownership is the root authorization boundary; child entities are accessible only through a Trade owned by that user. Never use a user-provided user_id as the authority.

## Application structure
Organize the backend around application use cases/domain services rather than putting domain rules in HTTP handlers.

Conceptual use cases:
- receive voice journal
- transcribe journal
- extract proposal
- get proposal
- approve journal
- retry processing
- list/get trades
- apply approved trade actions

Transport/API details can evolve without changing domain rules.

## AI extraction rules
The extractor must:
1. Never invent facts.
2. Use unknown/null for unstated values.
3. Distinguish actual executions from intentions.
4. Distinguish original plan from later management.
5. Distinguish pattern from Type 1–4.
6. Preserve spoken quantities, prices, dates, and times.
7. Mark ambiguity instead of guessing.
8. Preserve action order.
9. Never invent Trade IDs.
10. Never infer WIN/LOSS/BREAKEVEN from P&L unless the trader explicitly declares it.
11. Never infer Type 1–4.
12. Never create a trade for an unentered opportunity.

## Ambiguous notes
`I bought more Apple.` is insufficient for a safe execution if quantity/price are missing. `I moved my stop higher.` is insufficient if the new value is missing. Such proposals remain editable and must not invent values. A vague statement like `the trade went well` should not automatically become WIN without an explicit outcome policy.

## Voice architecture boundary
This is voice-first, not voice-chat. Do not build realtime conversational voice, interruptible agents, continuous streaming, or other expensive voice-agent infrastructure. The intended flow is record → process → review → approve.

## Analytics
Derive analytics from canonical domain tables. Initial metrics:
- trade count
- wins/losses/breakevens
- win rate
- gross P&L
- average P&L
- average R
- total R
- average holding time
- performance by pattern
- performance by Type
- performance over time
- execution history
- management changes
- journal transcripts/notes

Dashboard analytics and any future “Ask my journal” feature should use the same canonical metric definitions. No vector DB is needed for MVP.

## Explicit MVP exclusions
Do not implement unless later requested:
- broker integrations
- broker/account tables
- automatic market-price ingestion
- charting or TradingView replacement
- signals or trading advice
- automatic Type inference
- backtesting
- social/team features
- subscriptions/billing
- vector DB/RAG
- full event sourcing/audit log
- multi-currency accounting
- fees/taxes/slippage accounting

## End-to-end acceptance scenarios
1. New trade: voice note creates Trade + initial Execution after approval.
2. Scale in: another voice note creates another Execution; no position_size overwrite.
3. Management: voice note creates stop/target history while preserving original plan.
4. Partial exit: creates SELL execution and keeps Trade ACTIVE.
5. Completion: final execution + outcome + completion are atomically committed.
6. Retry: repeated approval creates no duplicates.
7. Processing failure: Journal Entry becomes FAILED and audio remains available.
8. Security: another user's Trade ID is rejected with no mutation.

## Build priority
1. migrations/models/constraints
2. authentication and ownership boundary
3. Trade validation
4. Execution logic and position calculation
5. Management decisions
6. Outcomes/completion
7. Journal persistence
8. transcription integration boundary
9. extraction integration boundary
10. proposal review/approval
11. atomic action processor
12. idempotency
13. retry/failure handling
14. analytics/query services
15. API polish/documentation

## Final rule
**AI understands the user's words. The user approves the interpretation. The backend owns the truth.**

Domain tables are the source of truth. Audio/transcript is the history of what the user said. The AI proposal is an interpretation. The approved payload is explicit user confirmation. Only the backend domain transaction may change trading state.
---

# Monetization & Freemium Specification

## Launch pricing

The product launches with a freemium model. AI voice journaling is the primary limited resource because transcription and AI extraction create variable operating cost.

| Plan | Price | AI voice entries | Manual journaling | Analytics |
|---|---:|---:|---|---|
| Free | $0/month | 5/month | Unlimited | Basic |
| Pro | $9.99/month | 100/month | Unlimited | Advanced |
| Pro Annual | $79/year | 100/month | Unlimited | Advanced |

Do not make AI processing technically unlimited. The Pro allowance should be configurable so pricing can change without modifying the trading schema.

## What counts as an AI voice entry

One successfully submitted voice recording entering the AI processing pipeline consumes one allowance unit.

Do not consume another unit for retries of the same Journal Entry.

An internal transcription/extraction/provider failure should not permanently consume the user's allowance.

Manual trade creation/editing, viewing history, analytics, replaying old journal entries, and reviewing/editing an existing proposal do not consume AI voice allowance.

## Usage periods

Usage resets monthly.

For Pro, the allowance follows the subscription billing period.

For Free, use an account-based monthly period.

The application should expose:

- current plan
- allowance limit
- usage consumed
- remaining allowance
- period start
- period end

Example:

```text
Plan: FREE
AI entries: 3 / 5
Remaining: 2
Period: Sep 1 → Sep 30
```

## Entitlements

Separate billing state from application feature access:

```text
Subscription / Billing
        ↓
Entitlements
        ↓
Application access
```

The application should be able to answer:

```text
Can this user create an AI voice journal entry?
How many AI entries remain?
Does this user have advanced analytics?
Does this user have AI journal analysis?
```

The trading domain must not know whether a user is Free or Pro.

## Billing module

Billing is outside the trading domain. Do not add plan, subscription, usage, or payment-provider fields to:

- trades
- executions
- management_decisions
- journal_entries
- trade_outcomes

When billing is implemented, use a separate billing/subscription module.

Recommended conceptual entities:

### `subscriptions`

Suggested fields:

- id
- user_id
- plan
- status
- provider
- provider_customer_id
- provider_subscription_id
- current_period_start
- current_period_end
- cancel_at_period_end
- created_at
- updated_at

Application plan values:

```text
FREE
PRO
```

Possible normalized statuses:

```text
ACTIVE
TRIALING
PAST_DUE
CANCELED
INCOMPLETE
```

### `usage_periods`

Suggested fields:

- id
- user_id
- period_start
- period_end
- ai_voice_limit
- ai_voice_used
- created_at
- updated_at

Recommended:

```text
UNIQUE(user_id, period_start, period_end)
```

Journal Entries remain the historical evidence of recordings; usage_periods is the entitlement/accounting mechanism.

## Usage concurrency

Allowance reservation must be atomic.

Two simultaneous requests must not both observe `4 / 5` and both succeed.

Conceptually:

```text
Check allowance
    ↓
Reserve one unit atomically
    ↓
Accept Journal Entry
    ↓
Process audio
```

If an internal provider/system failure occurs, release the reservation according to the implementation.

A retry of the same Journal Entry must be idempotent and must not reserve another unit.

## Free plan

Free includes:

- 5 AI voice entries/month
- unlimited manual trade journaling
- unlimited trade history
- basic analytics

When the allowance is exhausted, reject new AI voice-processing requests before expensive AI processing begins.

Do not lock historical data.

The user can still view trades, edit manually, and use basic analytics.

## Pro plan

Pro includes:

- 100 AI voice entries/month
- unlimited manual trade journaling
- unlimited trade history
- advanced analytics
- AI-powered journal analysis
- Ask Your Journal
- richer performance reviews

The feature set can evolve independently from the trading schema.

## Exhausted Pro allowance

When Pro reaches `100 / 100`, do not silently charge the user for extra processing.

Reject new AI processing until the next period or until a future higher tier/add-on is explicitly introduced.

## Subscription lifecycle

Conceptually:

```text
FREE
  ↓
START PRO
  ↓
TRIALING / ACTIVE
  ↓
ACTIVE
  ↓
CANCEL AT PERIOD END
  ↓
FREE
```

Payment failure can transition through `PAST_DUE` according to provider rules.

When Pro ends:

- historical trades remain
- journal entries remain
- analytics/history remain
- Pro-only entitlements are removed
- Free entitlements apply

Never delete trading history because of subscription status.

## Stripe/provider integration

Stripe may be used for subscription billing, but the trading domain must not depend directly on Stripe.

Use:

```text
Stripe
   ↓
Billing Integration
   ↓
Subscription State
   ↓
Entitlements
   ↓
Application Access
```

Provider webhooks must be processed asynchronously and idempotently.

The backend, not the client, is authoritative for Pro access.

## Pricing configuration

Initial configuration:

```text
FREE
  ai_voice_limit = 5

PRO
  monthly_price = 9.99 USD
  annual_price = 79 USD
  ai_voice_limit = 100
```

Keep limits and product/plan configuration outside the trading schema.

Pricing may later change without a database migration, for example:

```text
PRO: 100 → 150 AI entries
```

Existing users can be grandfathered through subscription/entitlement configuration.

## Monetization acceptance scenarios

### Free user, first recording

`0 / 5` → recording accepted → `1 / 5`.

### Free user, limit reached

`5 / 5` → new AI voice request rejected before AI processing.

### Provider failure

`2 / 5` → accepted recording → internal transcription failure → Journal Entry becomes FAILED → allowance is not permanently consumed.

### Retry

Retrying the failed Journal Entry does not create a duplicate Journal Entry or consume another unit.

### Pro user

`37 / 100` → can continue until the configured period allowance is reached.

### Downgrade

When Pro ends, historical data remains accessible and the user receives Free entitlements.

## Implementation priority

Implement:

1. Entitlement abstraction.
2. Free usage enforcement.
3. Configurable monthly allowance.
4. Concurrency-safe usage reservation.
5. Idempotent retries.
6. Pro entitlement.
7. Subscription/billing provider integration.

Full billing can be added after the entitlement boundary exists.

## Final monetization principle

Charge for reduced journaling friction and deeper understanding, not access to the user's own trading history.

The Free plan should be genuinely useful.

Pro primarily sells:

```text
More AI voice journaling
+
Better analytics
+
Deeper AI understanding of the trader
```

Historical trades must never be held hostage by subscription state.
