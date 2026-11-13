"""Small text helpers shared by ingestion (chunker) and guardrails.
Money detection lives here so both sides agree on what a "price" is."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CODES = "USD|EUR|GBP|MXN|CAD|AUD|AED|INR|TRY|THB|CHF|JPY|CNY|SAR"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_PREFIX = rf"(?:US\$|[$€£¥₹]|\b(?:{_CODES})\b)"
_SUFFIX = rf"(?:\s?(?:[$€£¥₹]|\b(?:{_CODES})\b|(?i:\bdollars?\b|\beuros?\b|\bpesos?\b)))"

# An amount WITH a currency marker: "USD 4,500", "$4,500", "4,500 USD", "50 dollars".
AMOUNT_RE = re.compile(rf"{_PREFIX}\s?(?P<a>{_NUM})|(?P<b>{_NUM}){_SUFFIX}")
# Any bare number (used to see which numbers exist in the evidence text).
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").rstrip("."))
    except (InvalidOperation, ValueError):
        return None


def has_price(text: str) -> bool:
    return AMOUNT_RE.search(text) is not None


def amounts_in(text: str) -> list[Decimal]:
    out: list[Decimal] = []
    for m in AMOUNT_RE.finditer(text):
        d = to_decimal(m.group("a") or m.group("b"))
        if d is not None:
            out.append(d)
    return out


def numbers_in(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in NUMBER_RE.finditer(text):
        d = to_decimal(m.group(0).rstrip(","))
        if d is not None:
            out.add(d)
    return out
