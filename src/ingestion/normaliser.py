import re

USD_TO_INR = 83.5


def normalise_currency(
    price: float | None,
    currency: str | None,
) -> tuple[float | None, list[str]]:
    """Return (price_inr, extra_flags)."""
    if price is None:
        return None, []
    if currency and currency.upper() == "USD":
        return round(price * USD_TO_INR, 2), ["CURRENCY_CONVERTED"]
    return price, []


def _estimate_box_weight_kg(description: str, ply_hint: int | None = None) -> float | None:
    """Estimate box weight from dimensions embedded in description.

    Returns kg or None if dimensions cannot be parsed.
    """
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*(mm|cm)?",
        description,
    )
    if not match:
        return None

    l_raw, w_raw, h_raw = float(match.group(1)), float(match.group(2)), float(match.group(3))
    unit_str = (match.group(4) or "cm").lower()
    if unit_str == "mm":
        l_raw, w_raw, h_raw = l_raw / 10, w_raw / 10, h_raw / 10  # convert to cm

    # Surface area in m²
    surface_m2 = 2 * (l_raw * w_raw + l_raw * h_raw + w_raw * h_raw) / 10_000

    # Determine ply count from description
    ply = ply_hint
    if ply is None:
        if re.search(r"7[\s-]?ply", description, re.IGNORECASE):
            ply = 7
        elif re.search(r"5[\s-]?ply", description, re.IGNORECASE):
            ply = 5
        else:
            ply = 3

    paper_gsm = {3: 120, 5: 150, 7: 200}.get(ply, 120)
    flute_kg_per_m2 = 0.050 * (ply // 2)
    kg_per_m2 = ply * paper_gsm / 1000 + flute_kg_per_m2

    return round(surface_m2 * kg_per_m2, 4)


_UNIT_PER_N = re.compile(r"per\s+(\d+)\s+(?:units?|pcs?|pieces?|boxes?)", re.IGNORECASE)
_UNIT_PER_DOZEN = re.compile(r"per\s+dozen", re.IGNORECASE)
_UNIT_KG = re.compile(r"per\s*kg|/\s*kg|\bkg\b", re.IGNORECASE)


def normalise_unit(
    price: float | None,
    unit_raw: str | None,
    description: str = "",
) -> tuple[float | None, str | None, list[str]]:
    """Return (price_per_box, unit_normalized, extra_flags).

    extra_flags contains flags added by the normaliser (beyond Claude's extraction flags).
    UNIT_INFERRED is added when a conversion is estimated (uncertain weight).
    """
    if price is None:
        return None, unit_raw, []

    unit = (unit_raw or "").strip().lower()

    # Already per-box (or implicitly per unit)
    if unit in ("per box", "per unit", "per piece", "per pcs", "each", "box", "unit", ""):
        return price, "per box", []

    # per N units → divide
    m = _UNIT_PER_N.search(unit)
    if m:
        n = int(m.group(1))
        if n > 1:
            return round(price / n, 4), "per box", []

    # per dozen
    if _UNIT_PER_DOZEN.search(unit):
        return round(price / 12, 4), "per box", []

    # per kg — attempt weight estimation; always add UNIT_INFERRED
    if _UNIT_KG.search(unit):
        weight_kg = _estimate_box_weight_kg(description)
        if weight_kg and weight_kg > 0:
            return round(price * weight_kg, 4), "per box", ["UNIT_INFERRED"]
        return None, "per box", ["UNIT_INFERRED"]

    return price, unit_raw, []
