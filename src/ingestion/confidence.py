FLAG_PENALTIES: dict[str, float] = {
    "PRICE_MISSING":        1.00,
    "TEMPORAL_REFERENCE":   0.90,
    "PRICE_AMBIGUOUS":      0.40,
    "UNIT_MISMATCH":        0.25,
    "LOW_LEGIBILITY":       0.20,
    "UNIT_INFERRED":        0.15,
    "CURRENCY_CONVERTED":   0.10,
    "VALUE_IN_PROSE":       0.10,
    "FREIGHT_UNQUANTIFIED": 0.08,
    "LINE_NOT_IN_RFX":      0.05,
    "EXTRACTION_FAILED":    1.00,
}

FLAG_MESSAGES: dict[str, str] = {
    "PRICE_MISSING":        "No price found for this line item.",
    "TEMPORAL_REFERENCE":   "Vendor referenced prior rates. Cannot price without history. Contact vendor or provide last year's rates.",
    "PRICE_AMBIGUOUS":      "Multiple prices found. Unclear which applies. Manual verification required.",
    "UNIT_MISMATCH":        "Vendor quoted in different unit than requested (per box). Price converted.",
    "LOW_LEGIBILITY":       "Text unclear due to image quality. Verify price with vendor directly.",
    "UNIT_INFERRED":        "Unit not explicitly stated. Inferred from context.",
    "CURRENCY_CONVERTED":   "Price was in foreign currency. Converted to INR at ₹83.5/USD.",
    "VALUE_IN_PROSE":       "Price found inside paragraph text, not a structured table.",
    "FREIGHT_UNQUANTIFIED": "Vendor mentioned freight charges but did not quantify. Total shown is ex-freight.",
    "LINE_NOT_IN_RFX":      "Vendor quoted an item not in the RFx.",
    "EXTRACTION_FAILED":    "Could not extract any line items from this document. File may be unreadable.",
}


def compute_confidence(flags: list[str]) -> float:
    score = 1.0
    for flag in flags:
        score -= FLAG_PENALTIES.get(flag, 0)
    return max(0.0, round(score, 2))


def flags_to_notes(flags: list[str]) -> dict[str, str]:
    return {f: FLAG_MESSAGES[f] for f in flags if f in FLAG_MESSAGES}
