# Model selection brief

Written 2 October 2026, for research into which OpenAI models SayTrade should use.

## What we need the models for

Three jobs. Every note goes through the first two, then the third.

### 1. Transcription — audio to text

- Input: the recorded audio. The browser records WebM, 30 seconds to 2 minutes.
- Output: the trader's words, Arabic (Egyptian colloquial) or English.
- Runs: once per voice note, before anything else. Nothing else can start until it finishes.
- Today: `gpt-transcribe`, $0.0045 per minute.
- The hard part: Egyptian colloquial Arabic, numbers spoken as words ("مية" = a hundred),
  and company names ("السويدي").
- Status: settled. Verified on the trader's own recordings — whisper-1 lost the share
  count in a note, gpt-transcribe read it correctly. The raw browser WebM must be
  converted to 16 kHz mono WAV first; gpt-transcribe rejects it otherwise.

### 2. Extraction — note text to a structured proposal

- Input: the transcript, plus the market it was recorded into (its name, its currency,
  and the tickers already held there).
- Output: strict JSON. One `symbol` (or null), and a list of position changes —
  OPEN, ADD, TRIM, STOP, TARGET, CLOSE — each with shares, price, stop, target.
- Runs: once per note. The trader then reviews and approves the result before it
  touches their journal.
- Today: `gpt-4o-mini` — a 2024-generation model, two generations back.
- The hard parts, in order of difficulty:
  1. **World knowledge.** Map a spoken company name, Arabic or English, to the ticker
     that company trades under *in the selected market*. The model must know that
     "السويدي" is SWDY on the Egyptian Exchange, and must not answer with a ticker
     from another exchange. This is the crux and the reason for this brief.
  2. **Arabic.** Read Egyptian colloquial correctly, including numbers as words.
  3. **Discipline.** Never invent a number, never do arithmetic, never infer a reason
     or an emotion. A null is acceptable; a wrong value is not.
- Status: under review. The nulls we were seeing were caused by our own prompt
  (it named example tickers, and the model returned only those). That is fixed.
  The model's knowledge of the EGX is still unverified.

### 3. Summary — the changes to one line

- Input: the structured changes. Never the raw transcript.
- Output: one sentence, in the language of the note, Arabic or English.
- Runs: once per note, and again when the trader approves it.
- Today: the same OpenAI key, a small model.
- The hard part: natural Arabic that matches the trader's own register, describing only
  what the changes say, inventing no move that did not happen.
- Status: works. Not recently re-evaluated.

## The questions

### Extraction — the important one

1. Which current models have reliable **world knowledge of company names to tickers**
   for the **Egyptian Exchange (EGX)** specifically? And for US markets?
2. Does **model tier** affect that knowledge? Tiers are usually described as differences
   in reasoning and instruction-following, not world knowledge. I need to know whether
   the cheapest current-generation model (e.g. gpt-6-luna) knows the EGX as well as a
   mid-tier (e.g. gpt-6.1-sol) or a flagship (gpt-6-astra).
3. Does the **knowledge cutoff date** matter for this? The gpt-4.1 family's cutoff is
   June 2024; the GPT-5 and GPT-6 families are newer. For a ticker lookup, does a newer
   cutoff mean better coverage of a smaller exchange?
4. Which current models are strongest at **Egyptian colloquial Arabic** input?
5. Which current models support **strict structured outputs / JSON schema** on the
   **Chat Completions** API? (We use Chat Completions, not the Responses API.)
6. What is the **price** of each candidate — input and output per 1M tokens — and its
   **context window**?
7. Is any model materially better at the discipline of "**return null rather than
   guess**"?

### Transcription

8. Is `gpt-transcribe` still the best choice for **Egyptian colloquial Arabic with
   numbers**, or is there a better current option?
9. How do the per-minute prices compare: `gpt-transcribe`, `gpt-4o-transcribe`,
   `gpt-4o-mini-transcribe`, `gpt-live-transcribe`?

### Summary

10. Which cheap current model writes the most natural **Egyptian Arabic** one-liners?
    And English?
11. What is the cheapest model that reliably follows "use only these facts, invent
    nothing"?

### Cost and operations

12. What does each candidate cost **per note** at our token profile — roughly 2,000
    input and 200 output tokens for extraction, 500 and 60 for the summary?
13. Are there **latency** differences that matter when the trader is waiting for the
    review screen?
14. Are any candidate models **deprecated** or scheduled for retirement?

## What we do not need

- A model chosen to show prices to users. We show no price anywhere in the product.
- A reasoning model for extraction, unless the research says the reading task needs one.
  The task is reading, not solving.
- Multilingual coverage beyond Arabic and English.

## How the decision gets made

Whatever the research says, the final choice is made by running the trader's own notes
through the candidates and reading the tickers that come back. The trader is the only
one who can say whether a ticker is right. The research narrows the list; the notes
settle it.
