# Prediction accuracy — analysis

A design analysis for the Trading Prediction Accuracy story. No code written.

## What already exists

**The trade result is already computed, and already reported.** It is derived at
read time from the approved changes, never stored:

    app/domain/position.py   realized_pnl(changes), r_multiple(changes)
    app/services/analytics.py  wins, losses, breakeven, win_rate, win_rate_r

    wins       realized P&L > 0
    losses     realized P&L < 0
    breakeven  realized P&L == 0
    win_rate   wins / (wins + losses + breakeven)

So "WIN / LOSS / BREAKEVEN" needs no new storage, no new column, and no change
to how a trade is applied. That dimension of the story is done.

**The prediction result does not exist in any form.** There is no field, no
table, and nothing in the extraction prompt that reads a prediction out of the
trader's words.

## The finding that shapes the design

The prediction result **cannot be derived**. Everything else in this app is
derived at read time — position size, R multiple, win rate, a note's symbol —
because everything needed is already in the changes. A prediction is a claim
about **what the price did after the trade was closed**, and the app holds no
prices at all. It has entries, stops, targets and exits, all of them typed by
the trader.

So there are exactly two ways to know whether a prediction came true:

1. **The trader tells the app.** He closed at 98 expecting 95; later he saw 95
   print, and says so.
2. **A market data feed.** Something would have to watch the price after the
   close and compare it to the predicted level.

Option 2 is a different product: a data provider, a subscription per market, a
job that watches open predictions, and a new class of failure when the feed is
wrong or the market is closed. Option 1 is what the story actually describes —
"later, the trader observes that price actually reaches $95" — and it is the one
this analysis assumes.

That makes the prediction result **stored, not derived**: the trader's
observation is a fact the journal records, like a change is. It is the one
exception to the derived-values rule, and the reason is that the input is not in
the app.

## The two dimensions, kept apart

    trade result        derived from the changes      WIN | LOSS | BREAKEVEN
    prediction result   observed by the trader        CORRECT | INCORRECT | PENDING

They never interact. A LOSS + CORRECT is a valid, and important, outcome — it is
the whole point of the feature. Nothing about a prediction changes a trade's
result, and nothing about a result changes a prediction.

    NOT_APPLICABLE is not needed as a stored value. A trade with no prediction
    simply has no prediction row. Storing "not applicable" would mean every
    trade carries a row saying nothing happened.

## What a prediction has to say

The story's example is a level: "I exited because I expect it to reach my
failure point", where the failure point is 95. For that to be judged later, the
record needs:

    the trade it is about
    when it was made
    the level the trader expects price to reach      e.g. 95
    which way price has to move to reach it          below the exit, here
    the words the trader used                        his own sentence
    the result                                       PENDING until he says otherwise
    when he resolved it

Without the level there is nothing to confirm. "I think it will fail" on its own
cannot be judged correct or incorrect later, so the level is what makes the
prediction a prediction rather than an opinion. The extraction has to find it,
and the review screen has to show it — the same review-and-approve path the
position changes already take.

## The lifecycle

    PENDING     made, and not yet judged. Excluded from the accuracy percentage.
    CORRECT     the trader confirms the predicted move happened.
    INCORRECT   the trader confirms it did not.

PENDING is the interesting state, and the one with an operational risk: a
prediction can sit pending forever if nothing ever asks. It needs a surface — a
list of unresolved predictions on the trades page — or it becomes a number
nobody ever completes.

## The metric

    prediction accuracy = CORRECT / (CORRECT + INCORRECT)

PENDING is excluded, per the story. That means the denominator grows only as
predictions resolve, so the number is honest but can be thin for a long time —
which is exactly the case the existing analytics rules already cover: the page
states its sample size, and says the number is too thin under five.

Reported beside the win rate, never merged with it. The most useful presentation
is the two-by-two, because the interesting cell is the off-diagonal one:

                          prediction correct    prediction incorrect
    winning trade                 expected            worth reading
    losing trade                  THE CASE            expected

## Where it touches the app

    extraction        reads a prediction out of the note, if there is one.
                      Same shape as security_candidates: a proposal, reviewed.
    review screen     shows the proposed level and asks for approval.
    a new table       one row per prediction, with its result and resolution.
    trades page       a pending-predictions list, with confirm / deny.
    analytics         a prediction-accuracy block, with its sample size.

The note path, the applier, the position maths and the win rate are untouched.
This feature is additive.

## Decisions

    1  A prediction is made only when closing a trade — one moment, one claim.
    2  One prediction per trade. No rule needed for which one counts.
    3  Resolved by a later note, not a button. The trade is already closed, so
       the note's own trade_id identifies which prediction it is about.
    4  The level defaults to the trade's stop, which IS the failure point the
       story describes. The trader names a level only when it is not the stop.
       A trade with no stop has nothing to judge, so it gets no prediction.
    5  The reason is kept as text on the prediction — the trader's own words.
    6  A readable trade number, prefixed "#", for display. Not for identifying a
       trade to the resolution — trade_id already does that — but because "#12"
       is a better handle for a person than a UUID. Nothing in the logic depends
       on it.

Decision 6 as first written was wrong: the number is not needed to resolve a
prediction, because a note always arrives from inside the trade's page with its
trade_id attached. It is kept as pure UX.

### The trade number

    format        "#" + an integer, per trader: #1, #2, #3 — his own, not global
    storage       a `number` column on trades, unique per (user_id, number)
    assignment    the next number for that trader, read inside the transaction
                  that creates the trade; the unique constraint catches a race
    backfill      existing trades numbered by opened_at, oldest first
    display       the trades list and the trade page
    never reused  a closed trade keeps its number forever, like its id

## Verified: a resolution has somewhere to land

`app/domain/errors.py` already states the rule:

    a closed trade takes no more position changes.
    A note with no change (a review, a lesson) is still welcome on a closed
    trade; a new position opens a new trade.

A resolution note is exactly that — a note with no change, on a closed trade.
The path exists today, `trade_id` is stamped on it, and the applier applies
nothing. What is new is only that the extraction may read a resolution out of
such a note, and that the review screen offers to mark the prediction.

## What is actually new

    a predictions table      one row per closed trade: the level, the reason,
                             the result (PENDING | CORRECT | INCORRECT)
    the extraction           proposes a prediction when the note CLOSES a trade,
                             and proposes a resolution when the note is a
                             no-change note on a closed trade that has a
                             pending prediction
    the review screen        shows the proposed level, or the proposed resolution,
                             and asks for approval — the existing pattern
    the analytics            a prediction-accuracy block with its sample size

The note path, the applier, the position maths, the trade result and the win
rate are all untouched.

## Risks

- **The model reads the wrong level.** Mitigated the same way the symbol is: it
  is a proposal, shown at review, approved by the trader before it counts.
- **The trader is the only source of truth.** This measures his judgement of his
  own predictions, not an independent audit. That is what a journal is.
- **A pending prediction can sit forever** if the trader never records the
  follow-up note. There is no list to nag him, by decision 3 — the resolution
  arrives when he next records something on that trade.

