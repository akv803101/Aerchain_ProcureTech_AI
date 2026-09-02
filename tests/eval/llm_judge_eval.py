"""LLM-as-Judge end-to-end eval for Aerchain Procurement AI.

Design:
  Agent  : Claude Sonnet (claude-sonnet-4-6)  — answers procurement questions
  Judge  : Claude Haiku  (claude-haiku-4-5)   — scores the answers
  Dataset: 10 questions drawn from the spec's demo script, covering all 6 query tools

Scoring:
  2 = fully correct, complete
  1 = partially correct, missing detail or minor inaccuracy
  0 = wrong or no relevant answer

Run:
  cd /Users/aakash/Desktop/aerchain
  venv/bin/python -m tests.eval.llm_judge_eval
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

import anthropic
from src.agents.query_agent import run_query

RFX_ID = "RFX-001"

# ── Eval dataset ──────────────────────────────────────────────────────────────
# Each entry:
#   question    : sent to the query agent as-is
#   criteria    : what the judge checks for (shown verbatim in judge prompt)
#   must_contain: optional list of strings the answer MUST contain (cheap pre-check)
QUESTIONS = [
    {
        "id": "Q01",
        "label": "Cheapest overall",
        "question": "Who is cheapest overall across all 30 lines?",
        "criteria": (
            "The answer names one or more vendors as cheapest, provides total INR cost figures, "
            "and ranks vendors by total price. "
            "Accept any answer that names the cheapest vendor AND provides INR totals — "
            "the agent may legitimately filter out low-confidence vendors, which is correct behaviour."
        ),
        "must_contain": ["vendor_"],
    },
    {
        "id": "Q02",
        "label": "Cheapest for a specific line",
        "question": "Which vendor has the lowest price for line item 7?",
        "criteria": (
            "The answer names the cheapest vendor for line 7 and states the price in INR."
        ),
        "must_contain": ["vendor_", "₹"],
    },
    {
        "id": "Q03",
        "label": "Freight terms",
        "question": "What are the freight terms for each vendor?",
        "criteria": (
            "The answer covers freight cost or 'unquantified'/'on actuals' for all or most vendors. "
            "vendor_e freight should be noted as unquantified."
        ),
        "must_contain": ["freight", "vendor_"],
    },
    {
        "id": "Q04",
        "label": "Discount terms",
        "question": "Which vendors offer volume discounts and what are the conditions?",
        "criteria": (
            "The answer mentions vendor_b's 5% discount for orders above ₹2L (200,000 INR). "
            "It should state the percentage and the order threshold."
        ),
        "must_contain": ["vendor_b", "%"],
    },
    {
        "id": "Q05",
        "label": "Questionnaire — ISO certification",
        "question": "Which vendors are ISO certified according to the quality questionnaire?",
        "criteria": (
            "The answer correctly identifies which vendors are ISO certified (Yes) and which are not, "
            "based on the questionnaire data. Should cover all 5 vendors."
        ),
        "must_contain": ["iso", "vendor_"],
    },
    {
        "id": "Q06",
        "label": "Questionnaire — lead time",
        "question": "Who has the shortest lead time and is ISO certified?",
        "criteria": (
            "The answer joins questionnaire data (lead_time_days + iso_certified) to identify "
            "the vendor with shortest lead time that is also ISO certified. "
            "A numeric lead time in days should be stated."
        ),
        "must_contain": ["vendor_", "day"],
    },
    {
        "id": "Q07",
        "label": "Flag summary",
        "question": "What issues were found in vendor responses? Summarise all flags.",
        "criteria": (
            "The answer lists data quality flags across vendors such as LOW_LEGIBILITY, "
            "PRICE_MISSING, CURRENCY_CONVERTED, TEMPORAL_REFERENCE, VALUE_IN_PROSE, UNIT_MISMATCH. "
            "Should name at least 3 distinct flag types."
        ),
        "must_contain": ["vendor_"],
    },
    {
        "id": "Q08",
        "label": "Confidence filter",
        "question": "Which lines have low confidence (below 0.5) and should be verified before awarding?",
        "criteria": (
            "The answer identifies specific lines with confidence below 0.5, "
            "names the vendors, and recommends verification. Should include line numbers."
        ),
        "must_contain": ["vendor_"],
    },
    {
        "id": "Q09",
        "label": "Price delta between two vendors",
        "question": "What is the percentage price difference between vendor_a and vendor_b across all lines?",
        "criteria": (
            "The answer provides a percentage difference (delta) between vendor_a and vendor_b "
            "for multiple lines, states which is cheaper per line, and includes numeric figures."
        ),
        "must_contain": ["vendor_a", "vendor_b", "%"],
    },
    {
        "id": "Q10",
        "label": "VP question — split award with questionnaire filter",
        "question": (
            "What if we split it — cheapest per line — but only among vendors "
            "who cleared the quality questionnaire (ISO certified with rejection rate < 1%)?"
        ),
        "criteria": (
            "This is the hardest question. The answer must: "
            "(1) identify which vendors passed the questionnaire filter (ISO certified + low rejection rate), "
            "(2) for each of the 30 lines, pick the cheapest among qualifying vendors only, "
            "(3) produce a split-award plan or summary table. "
            "The answer should not recommend vendors who failed the filter."
        ),
        "must_contain": ["vendor_"],
    },
    # ── Cross-combination questions ────────────────────────────────────────────
    {
        "id": "Q11",
        "label": "Cheapest — confidence filter applied",
        "question": "Who is cheapest if we only consider lines where confidence is 0.9 or above?",
        "criteria": (
            "The answer applies the confidence threshold first, eliminating vendor_d (0.70) "
            "and vendor_e (0.50) from consideration, then ranks the remaining vendors by price. "
            "vendor_b should be named as cheapest among high-confidence vendors."
        ),
        "must_contain": ["vendor_b", "vendor_"],
    },
    {
        "id": "Q12",
        "label": "ISO-certified + lowest price join",
        "question": "Among ISO-certified vendors, which one has the lowest total quoted price?",
        "criteria": (
            "The answer filters vendors to ISO-certified only (vendor_a, vendor_b, vendor_d), "
            "then ranks by total price. vendor_b should be named as cheapest among ISO vendors "
            "with reliable pricing. vendor_d's lower nominal price should be flagged as unconfirmed "
            "due to CURRENCY_CONVERTED flags."
        ),
        "must_contain": ["vendor_b", "iso"],
    },
    {
        "id": "Q13",
        "label": "Exclude flagged vendors — clean vendor pricing",
        "question": "If I exclude all vendors with any data quality flags, who qualifies and what is their total cost?",
        "criteria": (
            "The only qualifying vendors with zero data quality flags are vendor_a and vendor_b. "
            "vendor_b should be named as cheapest among them with its total price stated. "
            "Award full marks if the answer correctly identifies vendor_a and vendor_b as the only qualifiers "
            "and names vendor_b as cheapest, even if it does not enumerate the eliminated vendors explicitly."
        ),
        "must_contain": ["vendor_a", "vendor_b"],
    },
    {
        "id": "Q14",
        "label": "Discount threshold trap",
        "question": (
            "What is vendor_b's effective total cost after applying their volume discount? "
            "Does that make them cheaper than vendor_d?"
        ),
        "criteria": (
            "CRITICAL: The discount condition is orders above ₹2,00,000. vendor_b's total is "
            "well below this threshold so the discount does NOT apply. "
            "The answer must recognise that the discount threshold is not met and state that "
            "the effective price is unchanged (₹1,723.50 or similar). "
            "Applying the 5% discount to a sub-threshold order is a failure."
        ),
        "must_contain": ["vendor_b", "vendor_d"],
    },
    {
        "id": "Q15",
        "label": "Split award — rejection rate + ISO dual filter",
        "question": (
            "Split the award by cheapest per line — but only among vendors who are ISO certified "
            "AND have a rejection rate below 1%. Who wins each line, and what is the total?"
        ),
        "criteria": (
            "The answer applies both filters: ISO certified AND rejection rate < 1%. "
            "vendor_b fails (rejection rate 1.2%), vendor_c fails (not ISO), vendor_d fails (unknown rejection rate), "
            "vendor_e fails (no questionnaire). Only vendor_a qualifies (ISO + 0.5% rejection). "
            "All 30 lines are awarded to vendor_a. vendor_b must be explicitly rejected despite being cheaper overall."
        ),
        "must_contain": ["vendor_a", "vendor_b"],
    },
]

# ── Judge ─────────────────────────────────────────────────────────────────────

def _make_judge_client() -> anthropic.Anthropic:
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else {}
    return anthropic.Anthropic(default_headers=headers)


def judge_response(client: anthropic.Anthropic, question: str, criteria: str, answer: str) -> tuple[int, str]:
    """Ask Claude Haiku to score the answer 0/1/2 against the criteria.

    Returns (score, reasoning).
    """
    prompt = f"""You are a strict procurement AI evaluator. Score the following answer.

QUESTION:
{question}

WHAT A CORRECT ANSWER MUST DO:
{criteria}

ANSWER TO EVALUATE:
{answer}

Return JSON only:
{{"score": 0|1|2, "reasoning": "one sentence"}}

Scoring:
2 = fully correct, complete, all criteria met
1 = partially correct, at least one criterion met but missing details or minor inaccuracy
0 = wrong, irrelevant, or refuses to answer
"""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # strip fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()
    result = json.loads(raw)
    return int(result["score"]), result["reasoning"]


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_eval():
    print(f"\n{'='*70}")
    print(f"  Aerchain LLM-as-Judge Eval  |  RFX: {RFX_ID}")
    print(f"  Agent: claude-sonnet-4-6   |  Judge: claude-haiku-4-5")
    print(f"{'='*70}\n")

    judge_client = _make_judge_client()
    results = []

    for q in QUESTIONS:
        print(f"[{q['id']}] {q['label']}")
        print(f"       Q: {q['question'][:80]}{'…' if len(q['question'])>80 else ''}")

        # 1. Run query agent
        try:
            answer = await run_query(q["question"], rfx_id=RFX_ID)
        except Exception as exc:
            print(f"       AGENT ERROR: {exc}\n")
            results.append({**q, "score": 0, "reasoning": f"Agent error: {exc}", "answer": ""})
            continue

        # 2. Cheap pre-check — must_contain strings
        lower_answer = answer.lower()
        must_fail = [m for m in q.get("must_contain", []) if m.lower() not in lower_answer]
        if must_fail:
            score, reasoning = 0, f"Answer missing required terms: {must_fail}"
        else:
            # 3. LLM judge
            try:
                score, reasoning = judge_response(judge_client, q["question"], q["criteria"], answer)
            except Exception as exc:
                score, reasoning = 0, f"Judge error: {exc}"

        label = {2: "✅ PASS", 1: "⚠  PARTIAL", 0: "❌ FAIL"}[score]
        print(f"       {label}  ({score}/2)  {reasoning}")
        print(f"       Agent answer (first 200c): {answer[:200].replace(chr(10), ' ')}\n")

        results.append({**q, "score": score, "reasoning": reasoning, "answer": answer})

    # ── Scorecard ──────────────────────────────────────────────────────────────
    total_score  = sum(r["score"] for r in results)
    max_score    = len(results) * 2
    pct          = total_score / max_score * 100
    passes       = sum(1 for r in results if r["score"] == 2)
    partials     = sum(1 for r in results if r["score"] == 1)
    fails        = sum(1 for r in results if r["score"] == 0)

    print(f"\n{'='*70}")
    print(f"  SCORECARD")
    print(f"{'='*70}")
    print(f"  Total score  : {total_score}/{max_score}  ({pct:.1f}%)")
    print(f"  Full passes  : {passes}/{len(results)}")
    print(f"  Partials     : {partials}/{len(results)}")
    print(f"  Failures     : {fails}/{len(results)}")
    print(f"{'='*70}")

    print("\n  Per-question breakdown:")
    for r in results:
        bar = {2: "██", 1: "█░", 0: "░░"}[r["score"]]
        print(f"  {bar} [{r['id']}] {r['label']:<42} {r['score']}/2")

    # Save JSON report
    report_path = Path("tests/eval/last_judge_report.json")
    report_path.write_text(json.dumps(
        {"rfx_id": RFX_ID, "total": total_score, "max": max_score, "pct": pct, "results": [
            {"id": r["id"], "label": r["label"], "score": r["score"], "reasoning": r["reasoning"]}
            for r in results
        ]},
        indent=2, ensure_ascii=False
    ))
    print(f"\n  Full report saved → {report_path}\n")

    return total_score, max_score


if __name__ == "__main__":
    asyncio.run(run_eval())
