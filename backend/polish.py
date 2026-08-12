"""Optional LLM polish and structured AI insight generation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any, Optional

from backend.config import env


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _fmt(n: float) -> str:
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{n:,.2f}"


def build_datasource_evidence(
    columns: list[dict[str, Any]], rows: list[list[Any]]
) -> dict[str, Any]:
    """Build compact, factual aggregations safe to send to the LLM."""
    names = [str(c.get("fieldName") or c.get("name") or f"Column {i}") for i, c in enumerate(columns)]
    lower = [n.lower() for n in names]

    amount_idx = next((i for i, n in enumerate(lower) if "amount" in n or "value" in n), None)
    cleared_idx = next((i for i, n in enumerate(lower) if "cleared" in n), None)
    due_idx = next((i for i, n in enumerate(lower) if "due" in n and "date" in n), None)
    invoice_idx = next((i for i, n in enumerate(lower) if "invoice" in n and "date" in n), None)
    creditor_idx = next(
        (i for i, n in enumerate(lower) if any(k in n for k in ("creditor", "vendor", "supplier"))),
        None,
    )
    aging_idxs = [i for i, n in enumerate(lower) if "aging" in n]

    amount_total = 0.0
    unpaid_total = 0.0
    paid_total = 0.0
    unpaid_count = 0
    paid_count = 0
    payment_lags: list[float] = []

    unpaid_by_creditor: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "amount": 0.0})
    unpaid_by_aging: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "amount": 0.0})
    by_cleared: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "amount": 0.0})
    due_month_totals: dict[str, float] = defaultdict(float)
    invoice_month_totals: dict[str, float] = defaultdict(float)

    for row in rows:
        amount = _to_number(row[amount_idx]) if amount_idx is not None and amount_idx < len(row) else None
        if amount is None:
            continue
        amount_total += amount

        cleared = str(row[cleared_idx]).strip().upper() if cleared_idx is not None and cleared_idx < len(row) else ""
        by_cleared[cleared or "UNKNOWN"]["count"] += 1
        by_cleared[cleared or "UNKNOWN"]["amount"] += amount

        if cleared == "N":
            unpaid_total += amount
            unpaid_count += 1
            if creditor_idx is not None and creditor_idx < len(row) and row[creditor_idx] not in (None, ""):
                label = str(row[creditor_idx])
                unpaid_by_creditor[label]["count"] += 1
                unpaid_by_creditor[label]["amount"] += amount
            for aidx in aging_idxs:
                if aidx < len(row) and row[aidx] not in (None, ""):
                    label = f"{names[aidx]}::{row[aidx]}"
                    unpaid_by_aging[label]["count"] += 1
                    unpaid_by_aging[label]["amount"] += amount
                    break
        elif cleared == "Y":
            paid_total += amount
            paid_count += 1

        due = _parse_date(row[due_idx]) if due_idx is not None and due_idx < len(row) else None
        invoice = _parse_date(row[invoice_idx]) if invoice_idx is not None and invoice_idx < len(row) else None
        if due is not None:
            due_month_totals[due.strftime("%Y-%m")] += amount
        if invoice is not None:
            invoice_month_totals[invoice.strftime("%Y-%m")] += amount
        if due is not None and invoice is not None:
            payment_lags.append((due - invoice).days)

    breakdowns: list[dict[str, Any]] = []
    for idx, name in enumerate(names):
        if idx == amount_idx or "date" in name.lower():
            continue
        groups: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "amount": 0.0})
        for row in rows:
            if idx >= len(row) or row[idx] in (None, ""):
                continue
            label = str(row[idx])
            groups[label]["count"] += 1
            if amount_idx is not None and amount_idx < len(row):
                groups[label]["amount"] += _to_number(row[amount_idx]) or 0.0
        if not groups or len(groups) > 200:
            continue
        ranked = sorted(groups.items(), key=lambda item: (-item[1]["amount"], -item[1]["count"], item[0]))[:15]
        breakdowns.append(
            {
                "field": name,
                "distinctCount": len(groups),
                "values": [
                    {
                        "label": label,
                        "count": int(stats["count"]),
                        "amount": round(stats["amount"], 2),
                        "amountSharePct": round(stats["amount"] / amount_total * 100, 1) if amount_total else None,
                    }
                    for label, stats in ranked
                ],
            }
        )

    top_unpaid_creditors = sorted(
        unpaid_by_creditor.items(), key=lambda item: (-item[1]["amount"], item[0])
    )[:15]
    top_creditor_share = (
        sum(stats["amount"] for _, stats in top_unpaid_creditors[:10]) / unpaid_total * 100
        if unpaid_total
        else None
    )

    return {
        "rowCount": len(rows),
        "amountField": names[amount_idx] if amount_idx is not None else None,
        "amountTotal": round(amount_total, 2),
        "paidVsUnpaid": {
            "paidCount": paid_count,
            "unpaidCount": unpaid_count,
            "paidAmount": round(paid_total, 2),
            "unpaidAmount": round(unpaid_total, 2),
            "paidSharePct": round(paid_total / amount_total * 100, 1) if amount_total else None,
            "unpaidSharePct": round(unpaid_total / amount_total * 100, 1) if amount_total else None,
            "byClearedFlag": [
                {
                    "label": label,
                    "count": int(stats["count"]),
                    "amount": round(stats["amount"], 2),
                    "amountSharePct": round(stats["amount"] / amount_total * 100, 1) if amount_total else None,
                }
                for label, stats in sorted(by_cleared.items(), key=lambda item: -item[1]["amount"])
            ],
        },
        "topUnpaidCreditors": [
            {
                "creditor": label,
                "unpaidCount": int(stats["count"]),
                "unpaidAmount": round(stats["amount"], 2),
                "shareOfUnpaidPct": round(stats["amount"] / unpaid_total * 100, 1) if unpaid_total else None,
            }
            for label, stats in top_unpaid_creditors
        ],
        "creditorConcentration": {
            "top10ShareOfUnpaidPct": round(top_creditor_share, 1) if top_creditor_share is not None else None,
            "distinctUnpaidCreditors": len(unpaid_by_creditor),
        },
        "unpaidAging": [
            {
                "bucket": label,
                "count": int(stats["count"]),
                "amount": round(stats["amount"], 2),
                "shareOfUnpaidPct": round(stats["amount"] / unpaid_total * 100, 1) if unpaid_total else None,
            }
            for label, stats in sorted(unpaid_by_aging.items(), key=lambda item: -item[1]["amount"])[:12]
        ],
        "paymentTiming": {
            "avgInvoiceToDueDays": round(mean(payment_lags), 1) if payment_lags else None,
            "minInvoiceToDueDays": min(payment_lags) if payment_lags else None,
            "maxInvoiceToDueDays": max(payment_lags) if payment_lags else None,
            "sampleSize": len(payment_lags),
        },
        "dueMonthTotals": [
            {"period": period, "amount": round(amount, 2)}
            for period, amount in sorted(due_month_totals.items())[-18:]
        ],
        "invoiceMonthTotals": [
            {"period": period, "amount": round(amount, 2)}
            for period, amount in sorted(invoice_month_totals.items())[-18:]
        ],
        "breakdowns": breakdowns,
        "formattedTotals": {
            "amountTotal": _fmt(amount_total),
            "paidAmount": _fmt(paid_total),
            "unpaidAmount": _fmt(unpaid_total),
        },
    }


def generate_insight_sections(
    analysis: dict[str, Any],
    columns: list[dict[str, Any]],
    rows: list[list[Any]],
) -> Optional[list[dict[str, Any]]]:
    """Generate titled executive insight groups as strict JSON."""
    api_key = env("OPENAI_API_KEY")
    if not api_key:
        return None
    evidence = build_datasource_evidence(columns, rows)
    date_fields = [
        str(c.get("fieldName") or c.get("name") or "")
        for c in columns
        if "date" in str(c.get("fieldName") or c.get("name") or "").lower()
    ]
    comparisons = analysis.get("comparisons", {}) or {}
    quantitative = analysis.get("quantitative") or {}
    date_field = str(
        quantitative.get("dateField")
        or analysis.get("context", {}).get("dateField")
        or ""
    ).lower()
    # Due Date totals are payment schedule timing — not cash-flow / spend growth.
    # Invoice Date trends are volume/timing signals and may be cited with that caveat.
    if "due" in date_field and "invoice" not in date_field:
        comparisons = {
            "note": (
                "Primary date field is Due Date. "
                "Do not treat monthly totals as MoM/YoY business performance or cash-flow improvement."
            ),
            "monthOverMonth": None,
            "quarterOverQuarter": None,
            "yearOverYear": None,
            "yearToDate": None,
        }
        quantitative = {
            "note": "Due-date schedule totals omitted from narrative performance claims.",
            "pointers": [],
            "headline": [],
        }
    elif any("due" in name.lower() or "invoice" in name.lower() for name in date_fields):
        comparisons = {
            **{
                k: comparisons.get(k)
                for k in (
                    "monthOverMonth",
                    "quarterOverQuarter",
                    "yearOverYear",
                    "yearToDate",
                    "sameMonthPriorYear",
                )
            },
            "dateField": quantitative.get("dateField") or date_field,
            "note": (
                "Period comparisons use Invoice Date (preferred) when available. "
                "Cite them as invoice-volume / timing changes, not cash collections or spend reduction."
            ),
        }
    context = analysis.get("context", {}) or {}
    schema = analysis.get("schema", {}) or {}
    field_blob = " ".join(
        [
            str(context.get("datasourceName") or ""),
            str(context.get("dashboardName") or ""),
            str(context.get("workbookName") or ""),
            " ".join(str(x) for x in schema.get("measures", [])),
            " ".join(str(x) for x in schema.get("dimensions", [])),
            " ".join(str(x) for x in schema.get("dates", [])),
            " ".join(date_fields),
        ]
    ).lower()
    is_ap = any(
        k in field_blob
        for k in (
            "payable",
            "creditor",
            "cleared flag",
            "outstanding amount",
            "aging category",
            "invoice date",
            "due date",
        )
    )
    if is_ap:
        system_prompt = (
            "You are a senior accounts-payable and procurement analyst. "
            "Create a rich executive narrative using ONLY supplied evidence. "
            "Return JSON exactly as {\"sections\":[{\"title\":\"...\","
            "\"insights\":[{\"text\":\"...\",\"severity\":\"critical|warning|opportunity|info\"}]}]}. "
            "Create 4-6 meaningful sections and 3-5 insights per section. "
            "Prefer section themes like: Overall Payables Position, Unpaid Exposure & Aging Risk, "
            "Creditor Concentration, Payment Timing & Process Gaps, Priority Actions. "
            "Each insight must cite concrete numbers, explain why it matters, and recommend a practical next step. "
            "Do not invent causes; label plausible causes as investigation items. "
            "Accounts-payable semantics: Cleared Flag Y means paid/settled and N means still unpaid. "
            "Do not describe cleared/paid amounts as receivable. "
            "Due Date totals show payment schedule timing, not cash-flow improvement. "
            "Invoice Date MoM/QoQ/YoY figures are invoice-volume / timing signals — cite numbers exactly when provided, "
            "but do not call them collections, cash saved, or spend reduction. "
            "When quantitative.headline or quantitative.pointers are present, weave 2-4 of those numbered deltas into insights. "
            "When outlierAnalysis / quantitative.outliers are present, include a short outliers callout with exact values. "
            "Prioritize unpaid (Cleared=N) risk, overdue aging, and top unpaid creditors. "
            "Do not use markdown."
        )
    else:
        system_prompt = (
            "You are a senior business analyst for the dashboard/datasource provided. "
            "Infer the domain from field names (e.g. Sales, Orders, Marketing) and write matching section titles. "
            "Create a rich executive narrative using ONLY supplied evidence. "
            "Return JSON exactly as {\"sections\":[{\"title\":\"...\","
            "\"insights\":[{\"text\":\"...\",\"severity\":\"critical|warning|opportunity|info\"}]}]}. "
            "Create 4-6 meaningful sections and 3-5 insights per section. "
            "For sales/performance data prefer themes like: Overall Performance, Period Comparisons (MoM/QoQ/YoY), "
            "Category or Region Drivers, Risks & Opportunities, Priority Actions. "
            "Never use accounts-payable themes (creditors, cleared flags, aging payables) unless those fields exist. "
            "Each insight must cite concrete numbers, explain why it matters, and recommend a practical next step. "
            "Do not invent causes; label plausible causes as investigation items. "
            "When quantitative.headline or quantitative.pointers are present, weave 2-4 of those numbered deltas into insights. "
            "When outlierAnalysis / quantitative.outliers are present, include a short outliers callout with exact values. "
            "Do not use markdown."
        )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=env("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            max_tokens=1800,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "workbook": context.get("workbookName"),
                            "dashboard": context.get("dashboardName"),
                            "datasource": context.get("datasourceName"),
                            "evidence": evidence,
                            "computedAnalysis": {
                                "kpis": analysis.get("kpis", []),
                                "highlights": analysis.get("highlights", []),
                                "comparisons": comparisons,
                                "quantitative": {
                                    "measure": quantitative.get("measure"),
                                    "dateField": quantitative.get("dateField"),
                                    "asOf": quantitative.get("asOf"),
                                    "headline": quantitative.get("headline") or [],
                                    "pointers": (quantitative.get("pointers") or [])[:8],
                                    "monthlyChanges": (quantitative.get("monthlyChanges") or [])[-6:],
                                    "quarterlyChanges": quantitative.get("quarterlyChanges") or [],
                                    "outliers": {
                                        "stats": (quantitative.get("outliers") or {}).get("stats"),
                                        "pointers": ((quantitative.get("outliers") or {}).get("pointers") or [])[:6],
                                        "flagged": ((quantitative.get("outliers") or {}).get("outliers") or [])[:5],
                                    },
                                    "note": quantitative.get("note"),
                                },
                                "outlierAnalysis": {
                                    "stats": (analysis.get("outlierAnalysis") or {}).get("stats"),
                                    "pointers": ((analysis.get("outlierAnalysis") or {}).get("pointers") or [])[:6],
                                },
                                "topDrivers": analysis.get("topDrivers", []),
                            },
                        },
                        default=str,
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content or "{}"
        payload = json.loads(raw)
        sections = payload.get("sections")
        if not isinstance(sections, list):
            return None
        return [
            {
                "title": str(section.get("title") or "Key Insights"),
                "insights": [
                    {
                        "text": str(insight.get("text") or ""),
                        "severity": str(insight.get("severity") or "info"),
                    }
                    for insight in section.get("insights", [])
                    if isinstance(insight, dict) and insight.get("text")
                ],
            }
            for section in sections
            if isinstance(section, dict) and section.get("insights")
        ]
    except Exception:
        return None


def polish_summary(analysis: dict[str, Any]) -> Optional[str]:
    """Return a polished summary string, or None if OpenAI is unavailable/fails."""
    api_key = env("OPENAI_API_KEY")
    if not api_key:
        return None

    model = env("OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "summary": analysis.get("summary"),
        "kpis": analysis.get("kpis", [])[:4],
        "highlights": [h.get("text") for h in analysis.get("highlights", [])[:8]],
        "comparisons": {
            "monthOverMonth": analysis.get("comparisons", {}).get("monthOverMonth"),
            "quarterOverQuarter": analysis.get("comparisons", {}).get("quarterOverQuarter"),
            "yearOverYear": analysis.get("comparisons", {}).get("yearOverYear"),
            "yearToDate": analysis.get("comparisons", {}).get("yearToDate"),
            "sameMonthPriorYear": analysis.get("comparisons", {}).get("sameMonthPriorYear"),
        },
        "quantitativePointers": [
            p.get("text") for p in (analysis.get("quantitative") or {}).get("pointers", [])[:6]
        ],
        "context": analysis.get("context"),
        "notes": analysis.get("notes"),
    }

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.3,
            max_tokens=350,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write concise executive data narratives for Tableau dashboards. "
                        "Use only the facts provided. 2–4 short sentences. "
                        "Mention MoM/YoY when present. No markdown, no bullet lists, no inventing numbers."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Rewrite this analysis into a clear executive summary:\n"
                        + json.dumps(payload, default=str)
                    ),
                },
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        return None
