"""FRED daily-close observations. No intraday quotes or user-supplied URLs."""
from __future__ import annotations

import csv
import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Callable

SOURCE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500,NASDAQCOM,DJIA"
SERIES = {"SP500": "S&P 500", "NASDAQCOM": "Nasdaq Composite", "DJIA": "Dow Jones"}
MAX_BYTES = 1_000_000


def fetch_snapshot(fetch: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """One bounded, fixed-source GET through the configured executor."""
    try:
        response = fetch(SOURCE_URL)
    except Exception:
        return {"ok": False, "error": "market_source_unavailable"}
    if response.get("ok") is not True:
        return {"ok": False, "error": "market_source_unavailable"}
    try:
        return parse_snapshot(response.get("body", ""), datetime.now(timezone.utc))
    except (ValueError, InvalidOperation, TypeError, csv.Error):
        return {"ok": False, "error": "invalid_market_source_data"}


def parse_snapshot(body: str, retrieved_at: datetime) -> dict[str, Any]:
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid response size")
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieval time requires timezone")
    rows = csv.DictReader(StringIO(body.lstrip("\ufeff")))
    if not rows.fieldnames or not {"observation_date", *SERIES} <= set(rows.fieldnames):
        raise ValueError("missing columns")
    observations: dict[str, dict[date, Decimal]] = {series: {} for series in SERIES}
    for row in rows:
        if None in row or any(row.get(key) is None for key in rows.fieldnames):
            raise ValueError("incomplete row")
        observed = date.fromisoformat(row["observation_date"])
        if observed > retrieved_at.astimezone(timezone.utc).date():
            raise ValueError("future observation")
        for series in SERIES:
            raw = row[series].strip()
            if raw in {"", "."}:
                continue
            value = Decimal(raw)
            if (not value.is_finite() or not math.isfinite(float(value)) or value <= 0
                    or observed in observations[series]):
                raise ValueError("invalid or duplicate observation")
            observations[series][observed] = value
    quotes = []
    for series, name in SERIES.items():
        ordered = sorted(observations[series].items())
        if not ordered:
            continue
        day, close = ordered[-1]
        prior = ordered[-2] if len(ordered) > 1 else None
        change = close - prior[1] if prior else None
        quotes.append({
            "series": series, "name": name, "close": float(close),
            "as_of_date": day.isoformat(),
            "previous_date": prior[0].isoformat() if prior else None,
            "change": float(change) if change is not None else None,
            "change_percent": float((change / prior[1] * 100).quantize(Decimal("0.01"))) if prior else None,
            "stale": (retrieved_at.astimezone(timezone.utc).date() - day).days > 7,
            "source_url": f"https://fred.stlouisfed.org/series/{series}",
        })
    if not quotes:
        raise ValueError("no observations")
    return {
        "ok": True, "provider": "FRED", "data_kind": "daily_close", "is_realtime": False,
        "source_url": SOURCE_URL, "retrieved_at": retrieved_at.isoformat(), "quotes": quotes,
        "missing_series": [series for series in SERIES if not observations[series]],
        "summary": "FRED daily closes; observation dates are trading dates, NOT intraday prices. "
                   "Retrieval time is not the market observation time. Report missing/stale values explicitly.",
    }
