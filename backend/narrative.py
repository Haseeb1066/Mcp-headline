"""Deterministic data profiling, MoM/YoY comparisons, and highlight ranking."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any, Optional

try:
    from dateutil import parser as date_parser
except ImportError:  # pragma: no cover
    date_parser = None  # type: ignore[assignment]


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Tableau sometimes sends ms/s epoch — never treat small numerics as dates
        try:
            if value > 1e11:
                return datetime.utcfromtimestamp(value / 1000.0).date()
            if value > 1e9:
                return datetime.utcfromtimestamp(float(value)).date()
        except (OverflowError, OSError, ValueError):
            return None
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "nan", "none"):
        return None
    # Reject bare numerics (dateutil would treat "1010" as a year)
    if text.replace(".", "", 1).replace("-", "", 1).isdigit() and not any(
        sep in text for sep in ("/", "-", "T", ":")
    ):
        return None
    if not any(ch in text for ch in ("/", "-", "T", ":")) and " " not in text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    if date_parser is not None:
        try:
            parsed = date_parser.parse(text, fuzzy=False)
            # Avoid loose parses of non-date strings
            if parsed.year < 1900 or parsed.year > 2100:
                return None
            return parsed.date()
        except (ValueError, OverflowError, TypeError):
            pass
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in ("null", "nan", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def _pct_change(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        if current == 0:
            return 0.0
        return None
    return ((current - previous) / abs(previous)) * 100.0


def _fmt_num(n: float) -> str:
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"{n / 1_000:.2f}K"
    if abs_n >= 100 or abs_n == int(abs_n):
        return f"{n:,.0f}"
    return f"{n:,.2f}"


def _classify_columns(columns: list[dict[str, Any]], rows: list[list[Any]]) -> dict[str, Any]:
    measures: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    dates: list[dict[str, Any]] = []

    for col in columns:
        idx = int(col["index"])
        name = str(col.get("fieldName") or col.get("name") or f"col_{idx}")
        data_type = str(col.get("dataType") or "").lower()
        sample = [r[idx] if idx < len(r) else None for r in rows[:200]]

        date_hits = sum(1 for v in sample if _parse_date(v) is not None)
        num_hits = sum(1 for v in sample if _to_float(v) is not None)
        non_null = sum(1 for v in sample if v is not None and str(v).strip() != "")

        explicit_date = data_type in ("date", "date-time", "datetime")
        explicit_measure = data_type in (
            "int",
            "integer",
            "float",
            "real",
            "number",
            "numeric",
        )
        name_l = name.lower()
        looks_date = explicit_date or (
            not explicit_measure
            and (
                "date" in name_l
                or name_l.endswith(" time")
                or name_l.endswith("_time")
                or (non_null > 0 and date_hits / max(non_null, 1) >= 0.7)
            )
        )
        looks_measure = explicit_measure or (
            not looks_date
            and non_null > 0
            and num_hits / max(non_null, 1) >= 0.85
        )

        meta = {"index": idx, "name": name, "dataType": data_type}
        if looks_date:
            dates.append(meta)
        elif looks_measure:
            measures.append(meta)
        else:
            dimensions.append(meta)

    return {"measures": measures, "dimensions": dimensions, "dates": dates}


def _profile_column(
    name: str,
    values: list[Any],
    kind: str,
) -> dict[str, Any]:
    total = len(values)
    nulls = sum(1 for v in values if v is None or str(v).strip() == "" or str(v).lower() == "null")
    profile: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "nullCount": nulls,
        "nullRate": (nulls / total) if total else 0.0,
        "distinctCount": 0,
    }
    if kind == "measure":
        nums = [n for n in (_to_float(v) for v in values) if n is not None]
        profile["distinctCount"] = len(set(nums))
        if nums:
            profile["min"] = min(nums)
            profile["max"] = max(nums)
            profile["mean"] = mean(nums)
            profile["sum"] = sum(nums)
            profile["std"] = pstdev(nums) if len(nums) > 1 else 0.0
    elif kind == "date":
        dates = [d for d in (_parse_date(v) for v in values) if d is not None]
        profile["distinctCount"] = len(set(dates))
        if dates:
            dmin, dmax = min(dates), max(dates)
            profile["min"] = dmin.isoformat()
            profile["max"] = dmax.isoformat()
            profile["minDate"] = dmin.isoformat()
            profile["maxDate"] = dmax.isoformat()
    else:
        texts = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
        profile["distinctCount"] = len(set(texts))
        if texts:
            counts: dict[str, int] = defaultdict(int)
            for t in texts:
                counts[t] += 1
            top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:5]
            profile["topValues"] = [{"value": k, "count": v} for k, v in top]
    return profile


def _aggregate_by_month(
    rows: list[list[Any]],
    date_idx: int,
    measure_idx: int,
) -> dict[date, float]:
    buckets: dict[date, float] = defaultdict(float)
    for row in rows:
        d = _parse_date(row[date_idx] if date_idx < len(row) else None)
        n = _to_float(row[measure_idx] if measure_idx < len(row) else None)
        if d is None or n is None:
            continue
        buckets[_month_start(d)] += n
    return dict(buckets)


def _quarter_start(d: date) -> date:
    q = (d.month - 1) // 3
    return date(d.year, q * 3 + 1, 1)


def _quarter_label(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _add_quarters(d: date, quarters: int) -> date:
    return _add_months(_quarter_start(d), quarters * 3)


def _comparison_block(
    *,
    label: str,
    measure: str,
    current_period: str,
    previous_period: str,
    current: float,
    previous: float,
) -> dict[str, Any]:
    delta = current - previous
    return {
        "label": label,
        "measure": measure,
        "currentPeriod": current_period,
        "previousPeriod": previous_period,
        "current": current,
        "previous": previous,
        "delta": delta,
        "pctChange": _pct_change(current, previous),
        "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
        "pointer": (
            f"{measure} {_fmt_num(abs(delta))} "
            f"({'↑' if delta > 0 else '↓' if delta < 0 else '→'} "
            f"{abs(_pct_change(current, previous) or 0):.1f}%) "
            f"from {previous_period} to {current_period}"
        ),
    }


def _period_comparisons(
    monthly: dict[date, float],
    measure_name: str,
) -> dict[str, Any]:
    empty = {
        "monthOverMonth": None,
        "quarterOverQuarter": None,
        "yearOverYear": None,
        "yearToDate": None,
        "sameMonthPriorYear": None,
        "monthlySeries": [],
        "quarterlySeries": [],
        "monthlyChanges": [],
        "quarterlyChanges": [],
        "pointers": [],
    }
    if not monthly:
        return empty

    months_sorted = sorted(monthly.keys())
    latest = months_sorted[-1]
    today = date.today()
    current_month = _month_start(today)
    if latest == current_month and len(months_sorted) >= 2:
        last_complete = months_sorted[-2]
    else:
        last_complete = latest

    def sum_range(start: date, end: date) -> float:
        total = 0.0
        cur = start
        while cur <= end:
            total += monthly.get(cur, 0.0)
            cur = _add_months(cur, 1)
        return total

    pointers: list[dict[str, Any]] = []

    # MoM
    prior_month = _add_months(last_complete, -1)
    last_val = monthly.get(last_complete, 0.0)
    prior_val = monthly.get(prior_month)
    mom = None
    if prior_val is not None:
        mom = _comparison_block(
            label="Month over month",
            measure=measure_name,
            current_period=last_complete.strftime("%Y-%m"),
            previous_period=prior_month.strftime("%Y-%m"),
            current=last_val,
            previous=prior_val,
        )
        pointers.append(
            {
                "periodType": "monthly",
                "severity": "high" if abs(mom["pctChange"] or 0) >= 10 else "medium",
                "text": mom["pointer"],
                **{k: mom[k] for k in ("current", "previous", "delta", "pctChange", "direction")},
            }
        )

    # Same month prior year
    same_month_prior = _add_months(last_complete, -12)
    smpy = None
    if same_month_prior in monthly:
        smpy = _comparison_block(
            label="Same month prior year",
            measure=measure_name,
            current_period=last_complete.strftime("%Y-%m"),
            previous_period=same_month_prior.strftime("%Y-%m"),
            current=last_val,
            previous=monthly[same_month_prior],
        )
        pointers.append(
            {
                "periodType": "yearly",
                "severity": "high" if abs(smpy["pctChange"] or 0) >= 10 else "medium",
                "text": smpy["pointer"],
                **{k: smpy[k] for k in ("current", "previous", "delta", "pctChange", "direction")},
            }
        )

    # QoQ — compare last complete quarter vs prior quarter
    current_q = _quarter_start(last_complete)
    q_end_candidate = _add_months(current_q, 2)
    complete_q = current_q if last_complete >= q_end_candidate else _add_quarters(current_q, -1)
    prior_q = _add_quarters(complete_q, -1)
    q_end = _add_months(complete_q, 2)
    pq_end = _add_months(prior_q, 2)
    q_current = sum_range(complete_q, q_end)
    q_previous = sum_range(prior_q, pq_end)
    has_prior_q = any(m >= prior_q and m <= pq_end for m in monthly)
    has_curr_q = any(m >= complete_q and m <= q_end for m in monthly)
    qoq = None
    if has_prior_q and has_curr_q:
        qoq = _comparison_block(
            label="Quarter over quarter",
            measure=measure_name,
            current_period=_quarter_label(complete_q),
            previous_period=_quarter_label(prior_q),
            current=q_current,
            previous=q_previous,
        )
        pointers.append(
            {
                "periodType": "quarterly",
                "severity": "high" if abs(qoq["pctChange"] or 0) >= 10 else "medium",
                "text": qoq["pointer"],
                **{k: qoq[k] for k in ("current", "previous", "delta", "pctChange", "direction")},
            }
        )

    # Trailing 12 months YoY
    yoy_end = last_complete
    yoy_start = _add_months(yoy_end, -11)
    prior_end = _add_months(yoy_end, -12)
    prior_start = _add_months(prior_end, -11)
    current_y = sum_range(yoy_start, yoy_end)
    prior_y = sum_range(prior_start, prior_end)
    has_prior = any(m >= prior_start and m <= prior_end for m in monthly)
    yoy = None
    if has_prior:
        yoy = _comparison_block(
            label="Trailing 12 months vs prior 12 months",
            measure=measure_name,
            current_period=f"{yoy_start.strftime('%Y-%m')} → {yoy_end.strftime('%Y-%m')}",
            previous_period=f"{prior_start.strftime('%Y-%m')} → {prior_end.strftime('%Y-%m')}",
            current=current_y,
            previous=prior_y,
        )
        pointers.append(
            {
                "periodType": "yearly",
                "severity": "high" if abs(yoy["pctChange"] or 0) >= 10 else "medium",
                "text": yoy["pointer"],
                **{k: yoy[k] for k in ("current", "previous", "delta", "pctChange", "direction")},
            }
        )

    # YTD
    ytd_year = last_complete.year
    ytd_start = date(ytd_year, 1, 1)
    ytd_end = last_complete
    pytd_start = date(ytd_year - 1, 1, 1)
    pytd_end = date(ytd_year - 1, last_complete.month, 1)
    ytd_current = sum_range(ytd_start, ytd_end)
    ytd_prior = sum_range(pytd_start, pytd_end)
    has_pytd = any(m.year == ytd_year - 1 for m in monthly)
    ytd = None
    if has_pytd:
        ytd = _comparison_block(
            label="Year to date vs prior YTD",
            measure=measure_name,
            current_period=f"{ytd_start.strftime('%Y-%m')} → {ytd_end.strftime('%Y-%m')}",
            previous_period=f"{pytd_start.strftime('%Y-%m')} → {pytd_end.strftime('%Y-%m')}",
            current=ytd_current,
            previous=ytd_prior,
        )
        pointers.append(
            {
                "periodType": "yearly",
                "severity": "medium",
                "text": ytd["pointer"],
                **{k: ytd[k] for k in ("current", "previous", "delta", "pctChange", "direction")},
            }
        )

    # Month-by-month change pointers (last 12 complete months)
    monthly_changes: list[dict[str, Any]] = []
    window = [m for m in months_sorted if m <= last_complete][-13:]
    for i in range(1, len(window)):
        cur_m = window[i]
        prev_m = window[i - 1]
        cur_v = monthly.get(cur_m, 0.0)
        prev_v = monthly.get(prev_m, 0.0)
        delta = cur_v - prev_v
        pct = _pct_change(cur_v, prev_v)
        monthly_changes.append(
            {
                "period": cur_m.strftime("%Y-%m"),
                "previousPeriod": prev_m.strftime("%Y-%m"),
                "value": cur_v,
                "previousValue": prev_v,
                "delta": delta,
                "pctChange": pct,
                "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
                "pointer": (
                    f"{cur_m.strftime('%b %Y')}: {_fmt_num(cur_v)} "
                    f"({('↑' if delta > 0 else '↓' if delta < 0 else '→')} "
                    f"{_fmt_num(abs(delta))}, {abs(pct):.1f}% vs {prev_m.strftime('%b %Y')})"
                    if pct is not None
                    else f"{cur_m.strftime('%b %Y')}: {_fmt_num(cur_v)}"
                ),
            }
        )

    # Quarterly series + changes
    quarters: dict[date, float] = defaultdict(float)
    for m, v in monthly.items():
        quarters[_quarter_start(m)] += v
    q_sorted = sorted(q for q in quarters if q <= current_q)
    quarterly_series = [
        {"period": _quarter_label(q), "value": quarters[q]} for q in q_sorted[-8:]
    ]
    quarterly_changes: list[dict[str, Any]] = []
    recent_q = q_sorted[-5:]
    for i in range(1, len(recent_q)):
        cur_q = recent_q[i]
        prev_q = recent_q[i - 1]
        cur_v = quarters[cur_q]
        prev_v = quarters[prev_q]
        delta = cur_v - prev_v
        pct = _pct_change(cur_v, prev_v)
        quarterly_changes.append(
            {
                "period": _quarter_label(cur_q),
                "previousPeriod": _quarter_label(prev_q),
                "value": cur_v,
                "previousValue": prev_v,
                "delta": delta,
                "pctChange": pct,
                "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
                "pointer": (
                    f"{_quarter_label(cur_q)}: {_fmt_num(cur_v)} "
                    f"({('↑' if delta > 0 else '↓' if delta < 0 else '→')} "
                    f"{_fmt_num(abs(delta))}, {abs(pct):.1f}% vs {_quarter_label(prev_q)})"
                    if pct is not None
                    else f"{_quarter_label(cur_q)}: {_fmt_num(cur_v)}"
                ),
            }
        )

    # Biggest movers among monthly changes
    movers = sorted(
        [c for c in monthly_changes if c.get("pctChange") is not None],
        key=lambda c: abs(c["pctChange"] or 0),
        reverse=True,
    )[:3]
    for m in movers:
        pointers.append(
            {
                "periodType": "monthly",
                "severity": "medium",
                "text": f"Largest monthly move — {m['pointer']}",
                "current": m["value"],
                "previous": m["previousValue"],
                "delta": m["delta"],
                "pctChange": m["pctChange"],
                "direction": m["direction"],
            }
        )

    series = [
        {"period": m.strftime("%Y-%m"), "value": monthly[m]}
        for m in months_sorted[-24:]
    ]
    return {
        "monthOverMonth": mom,
        "quarterOverQuarter": qoq,
        "yearOverYear": yoy,
        "yearToDate": ytd,
        "sameMonthPriorYear": smpy,
        "monthlySeries": series,
        "quarterlySeries": quarterly_series,
        "monthlyChanges": monthly_changes,
        "quarterlyChanges": quarterly_changes,
        "pointers": pointers,
        "measure": measure_name,
        "asOf": last_complete.strftime("%Y-%m"),
    }


def _top_dimension_drivers(
    rows: list[list[Any]],
    dim: dict[str, Any],
    measure: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        key = row[dim["index"]] if dim["index"] < len(row) else None
        n = _to_float(row[measure["index"]] if measure["index"] < len(row) else None)
        if key is None or str(key).strip() == "" or n is None:
            continue
        totals[str(key)] += n
    ranked = sorted(totals.items(), key=lambda x: (-abs(x[1]), x[0]))[:limit]
    grand = sum(abs(v) for v in totals.values()) or 1.0
    return [
        {
            "dimension": dim["name"],
            "value": k,
            "measure": measure["name"],
            "total": v,
            "share": abs(v) / grand,
        }
        for k, v in ranked
    ]


def _outlier_highlights(
    rows: list[list[Any]],
    measure: dict[str, Any],
    dim: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs: list[tuple[str, float]] = []
    for i, row in enumerate(rows):
        n = _to_float(row[measure["index"]] if measure["index"] < len(row) else None)
        if n is None:
            continue
        label = str(row[dim["index"]]) if dim and dim["index"] < len(row) else f"row {i + 1}"
        pairs.append((label, n))
    if len(pairs) < 5:
        return []
    vals = [v for _, v in pairs]
    mu = mean(vals)
    sigma = pstdev(vals)
    if sigma == 0:
        return []
    outliers = [(lab, v, (v - mu) / sigma) for lab, v in pairs if abs((v - mu) / sigma) >= 2.5]
    outliers.sort(key=lambda x: -abs(x[2]))
    highlights = []
    for lab, v, z in outliers[:3]:
        direction = "high" if z > 0 else "low"
        highlights.append(
            {
                "severity": "high" if abs(z) >= 3 else "medium",
                "category": "outlier",
                "text": (
                    f"{lab} is an unusually {direction} {measure['name']} "
                    f"({_fmt_num(v)}, z={z:.1f})."
                ),
            }
        )
    return highlights


def _build_template_summary(
    worksheet: str,
    row_count: int,
    measures: list[dict[str, Any]],
    comparisons: dict[str, Any],
    highlights: list[dict[str, Any]],
    date_range: Optional[tuple[str, str]],
) -> str:
    parts: list[str] = []
    parts.append(
        f"Analyzed {row_count:,} summary rows from “{worksheet}”"
        + (f" covering {date_range[0]} to {date_range[1]}" if date_range else "")
        + "."
    )
    if measures:
        top = measures[0]
        if "sum" in top:
            parts.append(f"Primary measure {top['name']} totals {_fmt_num(top['sum'])}.")
    mom = comparisons.get("monthOverMonth")
    if mom and mom.get("pctChange") is not None:
        sign = "up" if mom["delta"] >= 0 else "down"
        parts.append(
            f"Last month ({mom['currentPeriod']}) is {sign} "
            f"{abs(mom['pctChange']):.1f}% vs {mom['previousPeriod']}."
        )
    yoy = comparisons.get("yearOverYear")
    if yoy and yoy.get("pctChange") is not None:
        sign = "up" if yoy["delta"] >= 0 else "down"
        parts.append(
            f"Trailing 12 months are {sign} {abs(yoy['pctChange']):.1f}% versus the prior year."
        )
    if highlights:
        parts.append(highlights[0]["text"])
    return " ".join(parts)


def analyze_table(
    *,
    columns: list[dict[str, Any]],
    rows: list[list[Any]],
    worksheet_name: str = "Worksheet",
    dashboard_name: str = "",
    workbook_name: str = "",
) -> dict[str, Any]:
    """Run full narrative analysis on a compact tabular payload."""
    classified = _classify_columns(columns, rows)
    measures_meta = classified["measures"]
    dimensions_meta = classified["dimensions"]
    dates_meta = classified["dates"]

    profiles: list[dict[str, Any]] = []
    for m in measures_meta:
        vals = [r[m["index"]] if m["index"] < len(r) else None for r in rows]
        profiles.append(_profile_column(m["name"], vals, "measure"))
    for d in dimensions_meta:
        vals = [r[d["index"]] if d["index"] < len(r) else None for r in rows]
        profiles.append(_profile_column(d["name"], vals, "dimension"))
    for dt in dates_meta:
        vals = [r[dt["index"]] if dt["index"] < len(r) else None for r in rows]
        profiles.append(_profile_column(dt["name"], vals, "date"))

    measure_profiles = [p for p in profiles if p["kind"] == "measure"]
    primary_measure = measures_meta[0] if measures_meta else None
    # Prefer invoice/order/transaction dates over due dates for trend analysis
    primary_date = None
    if dates_meta:
        preferred = next(
            (
                d
                for d in dates_meta
                if any(k in d["name"].lower() for k in ("invoice", "order", "transact", "posting", "created"))
            ),
            None,
        )
        primary_date = preferred or dates_meta[0]
    primary_dim = dimensions_meta[0] if dimensions_meta else None

    comparisons: dict[str, Any] = {
        "monthOverMonth": None,
        "quarterOverQuarter": None,
        "yearOverYear": None,
        "yearToDate": None,
        "sameMonthPriorYear": None,
        "monthlySeries": [],
        "quarterlySeries": [],
        "monthlyChanges": [],
        "quarterlyChanges": [],
        "pointers": [],
    }
    if primary_measure and primary_date:
        monthly = _aggregate_by_month(rows, primary_date["index"], primary_measure["index"])
        comparisons = _period_comparisons(monthly, primary_measure["name"])
        comparisons["dateField"] = primary_date["name"]

    highlights: list[dict[str, Any]] = []

    mom = comparisons.get("monthOverMonth")
    if mom and mom.get("pctChange") is not None:
        direction = "increased" if mom["delta"] >= 0 else "decreased"
        highlights.append(
            {
                "severity": "high" if abs(mom["pctChange"]) >= 10 else "medium",
                "category": "month_over_month",
                "text": (
                    f"{mom['measure']} {direction} {_fmt_num(abs(mom['delta']))} "
                    f"({abs(mom['pctChange']):.1f}%) from {mom['previousPeriod']} "
                    f"to {mom['currentPeriod']}."
                ),
            }
        )

    qoq = comparisons.get("quarterOverQuarter")
    if qoq and qoq.get("pctChange") is not None:
        direction = "increased" if qoq["delta"] >= 0 else "decreased"
        highlights.append(
            {
                "severity": "high" if abs(qoq["pctChange"]) >= 10 else "medium",
                "category": "quarter_over_quarter",
                "text": (
                    f"{qoq['measure']} {direction} {_fmt_num(abs(qoq['delta']))} "
                    f"({abs(qoq['pctChange']):.1f}%) from {qoq['previousPeriod']} "
                    f"to {qoq['currentPeriod']}."
                ),
            }
        )

    yoy = comparisons.get("yearOverYear")
    if yoy and yoy.get("pctChange") is not None:
        direction = "grew" if yoy["delta"] >= 0 else "declined"
        highlights.append(
            {
                "severity": "high" if abs(yoy["pctChange"]) >= 10 else "medium",
                "category": "year_over_year",
                "text": (
                    f"{yoy['measure']} {direction} {abs(yoy['pctChange']):.1f}% "
                    f"over the last 12 months versus the prior 12 months."
                ),
            }
        )

    ytd = comparisons.get("yearToDate")
    if ytd and ytd.get("pctChange") is not None:
        direction = "ahead of" if ytd["delta"] >= 0 else "behind"
        highlights.append(
            {
                "severity": "medium",
                "category": "year_to_date",
                "text": (
                    f"YTD {ytd['measure']} is {direction} prior YTD by "
                    f"{abs(ytd['pctChange']):.1f}% ({_fmt_num(ytd['current'])} vs "
                    f"{_fmt_num(ytd['previous'])})."
                ),
            }
        )

    smpy = comparisons.get("sameMonthPriorYear")
    if smpy and smpy.get("pctChange") is not None:
        direction = "up" if smpy["delta"] >= 0 else "down"
        highlights.append(
            {
                "severity": "medium",
                "category": "same_month_prior_year",
                "text": (
                    f"{smpy['currentPeriod']} is {direction} {abs(smpy['pctChange']):.1f}% "
                    f"vs same month last year ({smpy['previousPeriod']})."
                ),
            }
        )

    if primary_measure and primary_dim:
        drivers = _top_dimension_drivers(rows, primary_dim, primary_measure)
        if drivers:
            top = drivers[0]
            highlights.append(
                {
                    "severity": "medium",
                    "category": "top_driver",
                    "text": (
                        f"Top contributor for {top['measure']} by {top['dimension']} "
                        f"is “{top['value']}” ({_fmt_num(top['total'])}, "
                        f"{top['share'] * 100:.1f}% share)."
                    ),
                    "details": drivers,
                }
            )

    if primary_measure:
        highlights.extend(_outlier_highlights(rows, primary_measure, primary_dim))

    for p in profiles:
        if p["nullRate"] >= 0.15:
            highlights.append(
                {
                    "severity": "medium" if p["nullRate"] < 0.4 else "high",
                    "category": "data_quality",
                    "text": (
                        f"“{p['name']}” has {p['nullRate'] * 100:.0f}% null/missing values "
                        f"({p['nullCount']:,} of {len(rows):,} rows)."
                    ),
                }
            )

    for p in profiles:
        if p["kind"] == "dimension" and p["distinctCount"] == 1 and len(rows) > 1:
            highlights.append(
                {
                    "severity": "low",
                    "category": "concentration",
                    "text": f"“{p['name']}” has only one distinct value in the current view.",
                }
            )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    highlights.sort(key=lambda h: (severity_rank.get(h.get("severity", "low"), 9), h["category"]))
    # Deduplicate by text
    seen: set[str] = set()
    unique_highlights: list[dict[str, Any]] = []
    for h in highlights:
        if h["text"] in seen:
            continue
        seen.add(h["text"])
        unique_highlights.append(h)
    highlights = unique_highlights[:12]

    date_range = None
    date_profiles = [p for p in profiles if p["kind"] == "date"]
    if date_profiles and date_profiles[0].get("minDate"):
        date_range = (date_profiles[0]["minDate"], date_profiles[0]["maxDate"])

    kpis: list[dict[str, Any]] = []
    for p in measure_profiles[:4]:
        kpi: dict[str, Any] = {
            "name": p["name"],
            "value": p.get("sum", p.get("mean")),
            "formatted": _fmt_num(p.get("sum", p.get("mean") or 0)),
            "mean": p.get("mean"),
            "min": p.get("min"),
            "max": p.get("max"),
        }
        if primary_measure and p["name"] == primary_measure["name"] and mom:
            kpi["momPct"] = mom.get("pctChange")
            kpi["momDelta"] = mom.get("delta")
        if primary_measure and p["name"] == primary_measure["name"] and qoq:
            kpi["qoqPct"] = qoq.get("pctChange")
            kpi["qoqDelta"] = qoq.get("delta")
        if primary_measure and p["name"] == primary_measure["name"] and yoy:
            kpi["yoyPct"] = yoy.get("pctChange")
            kpi["yoyDelta"] = yoy.get("delta")
        kpis.append(kpi)

    summary = _build_template_summary(
        worksheet_name,
        len(rows),
        measure_profiles,
        comparisons,
        highlights,
        date_range,
    )

    notes: list[str] = []
    if not primary_date:
        notes.append("No date field detected — month/year comparisons are unavailable.")
    else:
        notes.append(f"Period comparisons use date field “{primary_date['name']}”.")
    if not primary_measure:
        notes.append("No numeric measure detected — KPI totals are limited.")
    if len(rows) == 0:
        notes.append("Worksheet returned no summary rows (check filters).")
    notes.append("Insights use dashboard summary data and respect current filters.")

    quantitative = {
        "measure": comparisons.get("measure") or (primary_measure["name"] if primary_measure else None),
        "dateField": comparisons.get("dateField") or (primary_date["name"] if primary_date else None),
        "asOf": comparisons.get("asOf"),
        "headline": [
            block
            for block in [
                comparisons.get("monthOverMonth"),
                comparisons.get("quarterOverQuarter"),
                comparisons.get("yearOverYear"),
                comparisons.get("yearToDate"),
                comparisons.get("sameMonthPriorYear"),
            ]
            if block
        ],
        "pointers": comparisons.get("pointers") or [],
        "monthlyChanges": comparisons.get("monthlyChanges") or [],
        "quarterlyChanges": comparisons.get("quarterlyChanges") or [],
        "monthlySeries": comparisons.get("monthlySeries") or [],
        "quarterlySeries": comparisons.get("quarterlySeries") or [],
    }

    return {
        "summary": summary,
        "summarySource": "template",
        "kpis": kpis,
        "highlights": highlights,
        "comparisons": comparisons,
        "quantitative": quantitative,
        "profiles": profiles,
        "schema": {
            "measures": [m["name"] for m in measures_meta],
            "dimensions": [d["name"] for d in dimensions_meta],
            "dates": [d["name"] for d in dates_meta],
        },
        "context": {
            "worksheetName": worksheet_name,
            "dashboardName": dashboard_name,
            "workbookName": workbook_name,
            "rowCount": len(rows),
            "columnCount": len(columns),
            "dateRange": (
                {"min": date_range[0], "max": date_range[1]} if date_range else None
            ),
            "dateField": primary_date["name"] if primary_date else None,
        },
        "notes": notes,
        "topDrivers": (
            _top_dimension_drivers(rows, primary_dim, primary_measure)
            if primary_dim and primary_measure
            else []
        ),
    }
