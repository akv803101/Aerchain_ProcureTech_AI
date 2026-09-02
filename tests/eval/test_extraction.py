"""Integration tests: extraction + normalization vs ground truth.

Scoring formula (per vendor):
  price_pct = # lines where price_match / total_lines
  flag_pct  = # lines where flag_set_match / total_lines
  overall   = price_pct + flag_pct   (max 2.0)
  threshold = 1.8

Price match rules:
  - GT price_inr_normalized is None  → always matched (price is undefined for that line)
  - GT price_inr_normalized is float → |extracted - GT| / GT < 0.05

Flag match:
  - GT flags empty → strict equality (no spurious flags allowed)
  - GT flags non-empty → subset match (all GT flags must be present; extras permitted)
  LLM outputs are non-deterministic; the GT lists MINIMUM required flags only.

Two-model design: Claude extracts, GPT-4o-mini judges flag quality at vendor level.
Requires ANTHROPIC_API_KEY. Skips when key absent.
"""
import json
import os
import pytest

from pathlib import Path
from src.ingestion.extractor import (
    extract_docx,
    extract_excel,
    extract_image,
    extract_pdf,
    extract_text,
)
from src.ingestion.normaliser import normalise_currency, normalise_unit

GROUND_TRUTH_DIR = Path("tests/eval/ground_truth")
VENDOR_DIR       = Path("data/vendor_responses")
RFX_PATH         = "data/rfx/RFX-001.json"
PASS_THRESHOLD   = 1.8

needs_anthropic = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


def _load_rfx() -> dict:
    with open(RFX_PATH) as f:
        return json.load(f)


def _load_gt(vendor_id: str) -> dict:
    path = GROUND_TRUTH_DIR / f"{vendor_id}_ground_truth.json"
    with open(path) as f:
        return json.load(f)


def _normalize_line(item) -> dict:
    """Apply currency then unit normalisation to one extracted LineItemRaw."""
    price_inr, currency_flags = normalise_currency(item.price, item.currency)
    price_norm, _, unit_flags = normalise_unit(price_inr, item.unit, item.description or "")
    all_flags = list(item.flags)
    for f in currency_flags + unit_flags:
        if f not in all_flags:
            all_flags.append(f)
    return {
        "line_id":  item.line_id,
        "price_inr": price_norm,
        "flags":     all_flags,
    }


def _score(extracted_output, gt: dict) -> tuple[float, dict]:
    """Return (overall_score, detail_dict)."""
    gt_items = {item["line_id"]: item for item in gt["line_items"]}
    total     = len(gt_items)

    # Build a by-line-id index from extracted items (after normalisation)
    ext_items: dict[int, dict] = {}
    for item in extracted_output.line_items:
        norm = _normalize_line(item)
        lid  = norm["line_id"]
        if lid is not None:
            ext_items[lid] = norm

    price_matches = 0
    flag_matches  = 0
    detail: dict[int, dict] = {}

    for lid, gt_item in gt_items.items():
        gt_price = gt_item.get("price_inr_normalized")
        gt_flags = set(gt_item.get("flags", []))
        ext      = ext_items.get(lid)

        # Price match
        if gt_price is None:
            pm = True  # GT says price is undefined for this line
        elif ext is None:
            pm = False
        else:
            ext_price = ext["price_inr"]
            if ext_price is None:
                pm = (gt_price is None)
            else:
                pm = abs(ext_price - gt_price) / max(gt_price, 0.01) < 0.05

        # Flag match: strict equality when GT is empty, subset when non-empty
        ext_flags = set(ext["flags"]) if ext else set()
        if gt_flags:
            fm = gt_flags.issubset(ext_flags)
        else:
            fm = ext_flags == gt_flags

        if pm:
            price_matches += 1
        if fm:
            flag_matches += 1

        detail[lid] = {
            "price_match": pm,
            "flag_match":  fm,
            "ext_flags":   list(ext_flags),
            "gt_flags":    list(gt_flags),
        }

    price_pct = price_matches / total
    flag_pct  = flag_matches / total
    overall   = price_pct + flag_pct
    return overall, {"price_pct": price_pct, "flag_pct": flag_pct, "detail": detail}


# ── Per-vendor tests ─────────────────────────────────────────────────────────

@needs_anthropic
@pytest.mark.integration
async def test_vendor_a_excel():
    vfile = VENDOR_DIR / "vendor_a_response.xlsx"
    if not vfile.exists():
        pytest.skip("vendor_a_response.xlsx not found — run scripts/create_vendor_data.py")
    rfx = _load_rfx()
    gt  = _load_gt("vendor_a")
    out = await extract_excel(str(vfile), rfx)
    score, info = _score(out, gt)
    print(f"\nvendor_a: price={info['price_pct']:.2f} flag={info['flag_pct']:.2f} overall={score:.2f}")
    assert score >= PASS_THRESHOLD, (
        f"vendor_a scored {score:.2f} < {PASS_THRESHOLD}. "
        f"price_pct={info['price_pct']:.2f}, flag_pct={info['flag_pct']:.2f}"
    )


@needs_anthropic
@pytest.mark.integration
async def test_vendor_b_pdf():
    vfile = VENDOR_DIR / "vendor_b_response.pdf"
    if not vfile.exists():
        pytest.skip("vendor_b_response.pdf not found — run scripts/create_vendor_data.py")
    rfx = _load_rfx()
    gt  = _load_gt("vendor_b")
    out = await extract_pdf(str(vfile), rfx)
    score, info = _score(out, gt)
    print(f"\nvendor_b: price={info['price_pct']:.2f} flag={info['flag_pct']:.2f} overall={score:.2f}")

    # Specific check: discount must be extracted
    assert out.discount_condition is not None, "vendor_b discount_condition not extracted (check p.3 footnote)"
    assert out.discount_pct == pytest.approx(5.0, abs=0.1), f"vendor_b discount_pct={out.discount_pct}, expected 5.0"

    assert score >= PASS_THRESHOLD, (
        f"vendor_b scored {score:.2f} < {PASS_THRESHOLD}. "
        f"price_pct={info['price_pct']:.2f}, flag_pct={info['flag_pct']:.2f}"
    )


@needs_anthropic
@pytest.mark.integration
async def test_vendor_c_docx():
    vfile = VENDOR_DIR / "vendor_c_response.docx"
    if not vfile.exists():
        pytest.skip("vendor_c_response.docx not found — run scripts/create_vendor_data.py")
    rfx = _load_rfx()
    gt  = _load_gt("vendor_c")
    out = await extract_docx(str(vfile), rfx)
    score, info = _score(out, gt)
    print(f"\nvendor_c: price={info['price_pct']:.2f} flag={info['flag_pct']:.2f} overall={score:.2f}")

    # Lines 11-20 must be normalised from per-100-units (raw 15000 → 150.0 per box)
    norm_by_id = {
        item.line_id: _normalize_line(item)
        for item in out.line_items
        if item.line_id is not None
    }
    line13_price = (norm_by_id.get(13) or {}).get("price_inr")
    assert line13_price is not None, "vendor_c line 13 price not extracted"
    assert abs(line13_price - 150.0) < 10.0, f"vendor_c line 13 price_inr={line13_price}, expected ~150"

    assert score >= PASS_THRESHOLD, (
        f"vendor_c scored {score:.2f} < {PASS_THRESHOLD}. "
        f"price_pct={info['price_pct']:.2f}, flag_pct={info['flag_pct']:.2f}"
    )


@needs_anthropic
@pytest.mark.integration
async def test_vendor_d_image():
    vfile = VENDOR_DIR / "vendor_d_response.jpg"
    if not vfile.exists():
        pytest.skip("vendor_d_response.jpg not found — run scripts/generate_vendor_d_image.py")
    rfx = _load_rfx()
    gt  = _load_gt("vendor_d")
    out = await extract_image(str(vfile), rfx)
    score, info = _score(out, gt)
    print(f"\nvendor_d: price={info['price_pct']:.2f} flag={info['flag_pct']:.2f} overall={score:.2f}")

    # Lines 29-30 must be PRICE_MISSING (cut off in image)
    norm_by_id = {
        item.line_id: item
        for item in out.line_items
        if item.line_id is not None
    }
    for lid in (29, 30):
        item = norm_by_id.get(lid)
        if item:
            assert item.price is None or "PRICE_MISSING" in item.flags, (
                f"vendor_d line {lid}: expected PRICE_MISSING, got flags={item.flags}"
            )

    assert score >= PASS_THRESHOLD, (
        f"vendor_d scored {score:.2f} < {PASS_THRESHOLD}. "
        f"price_pct={info['price_pct']:.2f}, flag_pct={info['flag_pct']:.2f}"
    )


@needs_anthropic
@pytest.mark.integration
async def test_vendor_e_text():
    vfile = VENDOR_DIR / "vendor_e_response.txt"
    if not vfile.exists():
        pytest.skip("vendor_e_response.txt not found")
    rfx = _load_rfx()
    gt  = _load_gt("vendor_e")
    out = await extract_text(str(vfile), rfx)
    score, info = _score(out, gt)
    print(f"\nvendor_e: price={info['price_pct']:.2f} flag={info['flag_pct']:.2f} overall={score:.2f}")

    # Freight must be flagged as unquantified
    assert out.freight_unquantified, "vendor_e: freight_unquantified should be True"

    # Lines 11-30 should all have TEMPORAL_REFERENCE
    norm_by_id = {
        item.line_id: item
        for item in out.line_items
        if item.line_id is not None
    }
    temporal_ok = all(
        "TEMPORAL_REFERENCE" in (norm_by_id.get(lid, None) and norm_by_id[lid].flags or [])
        for lid in range(11, 31)
        if lid in norm_by_id
    )
    assert temporal_ok, "vendor_e: lines 11-30 should all have TEMPORAL_REFERENCE flag"

    assert score >= PASS_THRESHOLD, (
        f"vendor_e scored {score:.2f} < {PASS_THRESHOLD}. "
        f"price_pct={info['price_pct']:.2f}, flag_pct={info['flag_pct']:.2f}"
    )


# ── GPT-4o-mini judge: flag quality check (breaks circularity) ───────────────

@needs_anthropic
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
@pytest.mark.integration
async def test_flag_quality_gpt_judge():
    """GPT-4o-mini assesses whether vendor_a extraction flags are semantically correct.

    Claude extracted, GPT-4o-mini judges — two-model design to break circularity.
    """
    import openai

    vfile = VENDOR_DIR / "vendor_a_response.xlsx"
    if not vfile.exists():
        pytest.skip("vendor_a_response.xlsx not found")

    rfx = _load_rfx()
    out = await extract_excel(str(vfile), rfx)

    sample_flags = [
        {"line_id": item.line_id, "flags": item.flags}
        for item in out.line_items[:10]
    ]
    prompt = (
        "You are a procurement data quality assessor. "
        "A clean Excel quote from a single vendor was extracted for a corrugated packaging RFx. "
        "All prices should be in INR per box with no anomalies. "
        "Review the extracted flags below and return JSON: "
        '{"verdict": "pass" or "fail", "reasoning": "..."}. '
        "Pass if flags are empty or clearly justified. Fail only for egregious hallucination.\n\n"
        f"Extracted flags (first 10 lines):\n{json.dumps(sample_flags, indent=2)}"
    )

    client = openai.AsyncOpenAI()
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            response_format={"type": "json_object"},
        )
    except openai.RateLimitError as e:
        if "credit" in str(e).lower() or "quota" in str(e).lower():
            pytest.skip(f"OpenAI account has no credits: {e}")
        raise
    result = json.loads(response.choices[0].message.content)
    print(f"\nGPT-4o-mini flag verdict: {result}")
    assert result.get("verdict") == "pass", f"GPT-4o-mini flagged extraction quality issue: {result}"
