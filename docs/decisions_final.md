# What I Decided — and Why

This is a plain-language walkthrough of the choices I made building this system, what I considered, and what I left on the table on purpose.

---

## Which AI model does the work?

I use **Claude Haiku** (`claude-haiku-4-5`) for both extraction and querying. Haiku is Claude's fastest, cheapest tier — it handles all the heavy lifting here without needing Sonnet or Opus.

Why not the bigger models? Because the tasks don't need them. Extracting "₹450 per unit, 30 units" from a PDF is not a reasoning problem — it's a reading problem. And answering "who is cheapest?" is a lookup problem with some arithmetic, not a research problem. Haiku is more than capable of both, and it's roughly 5× cheaper per token than Sonnet.

The one place I use Sonnet is the RFx builder — when the buyer describes what they want to procure and the system generates a full structured document. That feels more like drafting, so I kept the heavier model there.

---

## How does the system read vendor responses?

Every vendor quotes differently. One sends a clean PDF. One sends a Word doc. One sends a photo of a printed sheet taken at an angle in bad lighting. I can't write a separate parser for each.

So instead of code, I use the AI to read them. Each file goes to Haiku with a single locked instruction: *"Extract the line items, prices, and terms. If something is unclear, say so with a flag. Don't guess."*

The output is always the same shape — a structured list of line items with prices in their original currency, plus any issues noted. From there, the code takes over: it converts currencies, standardises units, and calculates confidence scores.

The key constraint: the instruction to the AI never changes between vendors. I only change the *context* (what RFx category I'm looking at, what units are expected). This means the extraction behaviour is predictable and testable.

---

## What happens when the AI isn't sure?

This is the core trust question. A buyer is about to spend ₹4 crore. They need to know which numbers are solid and which aren't.

I handle this with **flags** and **confidence scores**. Every extracted line gets both.

A flag is a specific problem the AI noticed:
- `LOW_LEGIBILITY` — the source image was blurry or angled
- `PRICE_MISSING` — the vendor didn't quote this line at all
- `CURRENCY_CONVERTED` — the price was in USD and got converted; exchange rate risk applies
- `VALUE_IN_PROSE` — the price was buried in a sentence, not a table cell ("our unit rate is ₹450 per unit, taxes included" — the number is in there, but so is everything else)
- `UNIT_MISMATCH` — the vendor quoted "per box" but the RFx asked for "per piece"

The confidence score is just arithmetic: start at 1.0, subtract a penalty for each flag. A line with `LOW_LEGIBILITY` and `CURRENCY_CONVERTED` might score 0.55. The query agent knows to treat that number as an estimate, not a fact.

When the buyer asks "who is cheapest?", the system doesn't just sort by price. It applies the confidence filter first, excludes lines below 0.5, and tells the buyer exactly which vendors were excluded and why.

---

## How does the chat decide what to do?

When a buyer types a message, something has to decide: is this a question about prices? A request to export? A command to re-read a vendor's file?

I route these with simple pattern matching — no AI involved. The system checks the message against a list of patterns in order:

- Does it mention "ingest" or "upload"? → trigger ingestion
- Does it mention "export" or "download"? → trigger export
- Does it mention "rfx" or "create rfq"? → trigger the RFx builder
- Does it mention "status" or "how many"? → return a quick DB count
- Everything else → send to the query agent

This sounds crude. It works perfectly. Routing doesn't need intelligence — it needs speed and predictability. The AI is expensive and slow relative to a regex check. I only use it when the task actually needs reasoning.

One edge case I caught: "give me a flag summary" was accidentally routing to STATUS (because "summary" was in that pattern) instead of QUERY. I removed "summary" from the STATUS pattern and wrote a test for it.

---

## How does the query agent answer complex questions?

Some questions can't be answered in one lookup. "Who is cheapest among ISO-certified vendors with a rejection rate below 1%?" requires:
1. Fetch questionnaire data — find ISO-certified vendors
2. Filter by rejection rate
3. Fetch price data for the qualifying vendors only
4. Rank by total

The agent handles this by deciding, on the fly, which tools to call and in what order. It has six tools available — price lookup, vendor terms, questionnaire answers, flag summary, confidence filter, price delta calculator. It uses as many as the question needs, then writes up the answer.

This loop can take 3–5 seconds and 3–5 API calls for a complex question. For a simple one ("what's vendor_a's freight cost?") it's one call and done.

The cost of this design: every question hits the API. Common queries like "summarise all flags" are re-executed fresh each time — nothing is cached. For a demo with five vendors, that's fine. At production scale with large RFx datasets, pre-computing flag summaries at ingestion time and caching price totals would cut latency significantly.

---

## Why SQLite?

Five vendors and 30 line items — 150 rows in the main table. SQLite is a file on disk — no server to run, no connection pool to manage, no credentials to rotate.

For this scale, any heavier database would be infrastructure for its own sake. If this ever goes to production with dozens of concurrent users and hundreds of vendors, the migration is a one-line change (swap the database URL). The schema doesn't need to change.

The honest trade-off: SQLite serialises writes — only one process can write at a time. For concurrent ingestion of multiple vendor files in parallel, that creates a bottleneck. At this scale it's invisible. At ten simultaneous RFx ingestions it would be a problem worth solving.

---

## How I tested it

Running the system manually is not a test. The agent could give a plausible-sounding answer that applies a discount it shouldn't, or excludes a vendor it should include, and a quick visual check wouldn't catch it.

I built a 15-question eval suite where each question is sent to the query agent, and the answer is scored by a separate Haiku instance acting as judge. The judge scores 0, 1, or 2 — wrong, partial, or fully correct — against a written criteria for each question.

The key design decision: the judge is a completely separate API call, a different instance from the query agent. The agent never scores its own output. That matters because a model grading itself will almost always find itself correct.

The questions were written to break things, not confirm they work. They include threshold traps (does the discount apply?), dual-filter joins (ISO certified AND rejection rate below 1%), confidence exclusions, and split-award scenarios. Easy questions that any working system would pass weren't included — the suite is calibrated toward the edges where the system is most likely to fail.

Final score: 26/30 (86.7%). Four partials, no hard failures.

---

## What I deliberately left out

**Sending to real vendors.** When an RFx is created, it writes to `data/sent/` on disk. It doesn't actually email anyone or post to a vendor portal. Wiring a real dispatch channel would mean managing API keys, delivery receipts, retry queues, and bounce handling — none of which is relevant to the AI evaluation. I stubbed the pipe; the AI loops are real.

**Login and user accounts.** The prototype is single-user. There's no auth layer, no per-user data isolation. Adding that is standard web engineering work, not AI work.

**Editing an RFx after it's created.** The system generates an RFx from a description in one shot — and that's it. There's no way to say "actually, add a line for packaging" and have it revise. That would need multi-turn state management on the document object. I didn't build that here.

**Watching the AI think.** Responses appear all at once when the agent finishes. Streaming tokens as they arrive would feel faster but adds engineering complexity for no change in what the AI actually does.

**Traces and dashboards.** There's no LangSmith connection or external observability. The LLM-as-Judge eval suite in `tests/eval/` serves that purpose within this scope — it measures output quality on 15 representative questions.

---

## The most interesting problem I found

This prototype solves the comparison problem well. Read vendor responses, normalise them, answer questions about them. That's a real and valuable thing to build.

But building it made the actual hard problem visible.

The gap isn't in reading vendor documents. It's in everything that happens around them. A vendor doesn't just send a quote — they send questions back. They push on scope. They propose substitutions. They go silent and need chasing. Their quote is conditional on a term you haven't agreed to yet. Two vendors are willing to negotiate; one isn't. Someone in legal needs to sign off before you can award. The award itself triggers a contract, which triggers an obligation, which needs tracking through delivery.

None of that is a document-reading problem. It's a relationship management problem, a communication loop, a governance problem. And today it all happens in email threads and shared spreadsheets, with no state machine underneath it.

ProcureOS built one half of the answer — the execution layer: trust, payment integrity, vendor communication, approval governance. The Aerchain assignment builds the other half — the intelligence layer: reading chaos, normalising it, making it queryable.

In a complete procurement platform these two are not separate products. The intelligence layer produces the award recommendation. The execution layer executes it — with the same state machine, the same approval TTL, the same idempotent payment guarantees. The output of the Aerchain query agent — "award line 1-18 to Vendor B, lines 19-30 to Vendor D, total ₹85,400" — is exactly the input to ProcureOS's /goals endpoint.

The interesting problem isn't lakh notation or unit mismatches. It's that procurement is a conversation — with vendors, with finance, with legal — and every current tool treats it as a series of documents. The real build is the layer that manages that conversation with the same rigour that this prototype applies to reading the quotes.
