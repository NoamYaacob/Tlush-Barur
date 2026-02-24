"""
Corrections service — Phase 6.

Pure computation module (no I/O, no DB). Provides two public functions:

  apply_corrections_to_payload(payload, raw_corrections) -> ParsedSlipPayload
      Validates field paths, reads original values, writes corrected values,
      appends CorrectionEntry records to the audit trail.
      Raises ValueError on invalid field paths (converted to HTTP 422 by the router).

  recompute_anomalies(payload) -> ParsedSlipPayload
      Reruns _run_integrity_checks() and _build_anomalies_from_real_data()
      using the current (potentially corrected) values in payload.summary.
      Returns a new payload with updated anomalies and integrity fields.

Allowed field paths:
  "summary.<field>"          where <field> ∈ CORRECTABLE_SUMMARY_FIELDS
  "line_items[<id>].value"   where <id> is an existing LineItem id
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORRECTABLE_SUMMARY_FIELDS: frozenset[str] = frozenset({
    "gross",
    "net",
    "income_tax",
    "national_insurance",
    "health_insurance",
    "credit_points",
})

_LINE_ITEM_RE = re.compile(r"^line_items\[([^\]]+)\]\.value$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_corrections_to_payload(
    payload: object,
    raw_corrections: list[dict[str, Any]],
) -> object:
    """
    Apply a list of raw correction dicts to the payload.

    Each dict must contain:
        field_path (str): dot-notation path to the field.
        corrected_value (float | None): new value (None = clear the field).

    Returns a new ParsedSlipPayload with corrections appended to the audit trail.
    Raises ValueError with a descriptive message on any invalid field_path.
    """
    from app.models.schemas import (
        ParsedSlipPayload,
        CorrectionEntry,
        SummaryTotals,
        LineItem,
    )

    assert isinstance(payload, ParsedSlipPayload)

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    # Work on mutable dict copies
    summary_dict: dict[str, Any] = payload.summary.model_dump()
    line_items_by_id: dict[str, LineItem] = {li.id: li for li in payload.line_items}
    new_entries: list[CorrectionEntry] = []

    for raw in raw_corrections:
        field_path: str = str(raw["field_path"])
        corrected_value: float | None = raw.get("corrected_value")

        original_value, summary_dict, line_items_by_id = _apply_single(
            field_path=field_path,
            new_value=corrected_value,
            summary_dict=summary_dict,
            line_items_by_id=line_items_by_id,
        )

        new_entries.append(CorrectionEntry(
            field_path=field_path,
            original_value=original_value,
            corrected_value=corrected_value,
            corrected_at=now_iso,
        ))

    updated_summary = SummaryTotals.model_validate(summary_dict)
    updated_line_items = list(line_items_by_id.values())
    all_corrections = list(payload.corrections) + new_entries

    return payload.model_copy(update={
        "summary": updated_summary,
        "line_items": updated_line_items,
        "corrections": all_corrections,
    })


def recompute_anomalies(payload: object) -> object:
    """
    Rerun integrity checks and anomaly detection using the current
    (potentially corrected) values from payload.summary and payload.line_items.

    Returns a new ParsedSlipPayload with updated anomalies and integrity fields.
    The corrections audit trail is preserved unchanged.
    """
    from app.models.schemas import ParsedSlipPayload
    from app.services.parser import _run_integrity_checks, _build_anomalies_from_real_data

    assert isinstance(payload, ParsedSlipPayload)
    s = payload.summary

    integrity_ok, integrity_notes = _run_integrity_checks(
        gross=s.gross,
        net=s.net,
        income_tax=s.income_tax,
        national_ins=s.national_insurance,
        health=s.health_insurance,
    )

    updated_summary = s.model_copy(update={
        "integrity_ok": integrity_ok,
        "integrity_notes": integrity_notes,
    })

    new_anomalies = _build_anomalies_from_real_data(
        gross=s.gross,
        net=s.net,
        integrity_ok=integrity_ok,
        integrity_notes=integrity_notes,
        income_tax=s.income_tax,
        national_ins=s.national_insurance,
        health=s.health_insurance,
        credit_points=s.credit_points,
        net_salary=s.net_salary,
        net_to_pay=s.net_to_pay,
        line_items=payload.line_items,
        answers=None,
    )

    return payload.model_copy(update={
        "summary": updated_summary,
        "anomalies": new_anomalies,
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_single(
    field_path: str,
    new_value: float | None,
    summary_dict: dict[str, Any],
    line_items_by_id: dict,
) -> tuple[float | None, dict[str, Any], dict]:
    """
    Apply one correction. Returns (original_value, updated_summary_dict, updated_line_items).
    Raises ValueError for invalid field paths.
    """
    # Case 1: summary field
    if field_path.startswith("summary."):
        field_name = field_path[len("summary."):]
        if field_name not in CORRECTABLE_SUMMARY_FIELDS:
            raise ValueError(
                f"Field '{field_name}' is not correctable. "
                f"Correctable summary fields: {sorted(CORRECTABLE_SUMMARY_FIELDS)}"
            )
        original_value: float | None = summary_dict.get(field_name)
        updated_summary = {**summary_dict, field_name: new_value}
        return original_value, updated_summary, line_items_by_id

    # Case 2: line item value
    m = _LINE_ITEM_RE.match(field_path)
    if m:
        item_id = m.group(1)
        if item_id not in line_items_by_id:
            raise ValueError(
                f"Line item with id '{item_id}' not found in payload. "
                f"Known ids: {list(line_items_by_id.keys())[:10]}"
            )
        li = line_items_by_id[item_id]
        original_value = li.value
        updated_li = li.model_copy(update={"value": new_value})
        updated_items = {**line_items_by_id, item_id: updated_li}
        return original_value, summary_dict, updated_items

    raise ValueError(
        f"Unrecognized field_path format: '{field_path}'. "
        "Use 'summary.<field>' or 'line_items[<id>].value'"
    )
