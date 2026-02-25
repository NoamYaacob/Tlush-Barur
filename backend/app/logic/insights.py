"""
insights.py — Phase 15: The Payroll Expert — Educational Insights Engine.

Generates plain-Hebrew educational insights from validated payslip data.
Insights are separate from anomalies: anomalies flag potential problems,
insights explain what is happening and why — in a supportive, helpful tone.

Design principles:
  - Privacy: insights use generic terms only ("המעסיק", "התלוש"). Never name
    the employee, employer, or provider.
  - Tone: supportive second-person ("את/ה"), non-judgmental, educational.
  - Color coding: INFO (blue), SUCCESS (green), WARNING (yellow/red).
  - Independent: pure function — takes scalar values, returns list[Insight].
    No I/O, no DB access, no external calls. Fully testable.

Insight types generated:
  1. Income Tax Zero  — credit points fully offset tax liability
  2. Pension Boost    — employee pension % above mandatory 6% minimum
  3. Minimum Wage     — hourly/base rate above legal minimum
  4. Havra'a Pay      — convalescence pay present, explained
  5. Missing Health   — health insurance not found (WARNING)
  6. Zero Net Check   — net appears suspiciously low vs. gross
"""

from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants — 2025 Israeli labour law values
# ---------------------------------------------------------------------------

# Legal minimum wage per hour (2025): ₪34.32
_MIN_WAGE_HOURLY = 34.32

# Mandatory minimum employee pension contribution: 6% of pensionable salary
_PENSION_MANDATORY_MIN_PCT = 6.0

# Maximum credit-point value to still consider tax as "zero due to credits"
# (If gross is very high, credits alone can't explain zero tax)
_CREDIT_POINT_MONTHLY_VALUE = 258.0   # ₪ per point per month (2025)

# Gross below which zero income tax is plausible without a credit-point explanation
# (≈ bracket 1 ceiling at 1 credit point worth of monthly tax reduction)
_GROSS_ZERO_TAX_PLAUSIBLE_THRESHOLD = 7_500.0


# ---------------------------------------------------------------------------
# Insight data class (plain dataclass — not a Pydantic model to avoid
# circular import; Pydantic schema is in schemas.py)
# ---------------------------------------------------------------------------

class Insight:
    """
    A single educational insight card.

    Attributes:
        id       — stable machine identifier, e.g. "ins_tax_zero_credits"
        kind     — "info" | "success" | "warning"
        title    — short Hebrew title (≤60 chars), shown on the card header
        body     — full Hebrew explanation (1-3 sentences), shown on the card body
    """
    __slots__ = ("id", "kind", "title", "body")

    def __init__(self, id: str, kind: str, title: str, body: str) -> None:
        self.id = id
        self.kind = kind
        self.title = title
        self.body = body

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "body": self.body}


# ---------------------------------------------------------------------------
# Main generator function
# ---------------------------------------------------------------------------

def generate_insights(
    gross: "float | None",
    net: "float | None",
    income_tax: "float | None",
    national_insurance: "float | None",
    health_insurance: "float | None",
    pension_employee: "float | None",
    credit_points: "float | None",
    line_items: "list | None" = None,
) -> list[Insight]:
    """
    Generate educational insight cards from validated payslip data.

    Args:
        gross              — resolved gross pay (סה"כ תשלומים or ברוטו)
        net                — resolved net pay (נטו לתשלום)
        income_tax         — income tax deduction (מס הכנסה), None if not found
        national_insurance — NI deduction (ביטוח לאומי), None if not found
        health_insurance   — health tax (מס בריאות), None if not found
        pension_employee   — employee pension deduction, None if not found
        credit_points      — tax credit points detected, None if not found
        line_items         — list of LineItem objects (for description scanning)

    Returns:
        list[Insight] in display order (success first, then info, then warning).
    """
    insights: list[Insight] = []
    items = line_items or []

    # -----------------------------------------------------------------------
    # 1. Income Tax Zero — credit points fully offset liability
    # -----------------------------------------------------------------------
    if income_tax is not None and income_tax == 0 and credit_points is not None:
        # Verify that credits plausibly explain zero tax
        monthly_credit_value = credit_points * _CREDIT_POINT_MONTHLY_VALUE
        if gross is None or monthly_credit_value >= (gross * 0.10 * 0.5):
            # Credits cover at least half the estimated bracket-1 tax → plausible
            points_str = f"{credit_points:.2f}".rstrip("0").rstrip(".")
            insights.append(Insight(
                id="ins_tax_zero_credits",
                kind="info",
                title="לא שולם מס הכנסה החודש",
                body=(
                    f"לא שילמת/שילמת מס הכנסה החודש כיוון שנקודות הזיכוי שלך "
                    f"({points_str} נקודות, שווי ₪{monthly_credit_value:,.0f}/חודש) "
                    f"קיזזו את חבות המס באופן מלא. "
                    f"נקודות זיכוי הן הטבת מס קבועה שמקטינה את המס שאת/ה חייב/ת לשלם."
                ),
            ))

    # -----------------------------------------------------------------------
    # 2. Pension Boost — contribution above mandatory minimum
    # -----------------------------------------------------------------------
    if pension_employee is not None and gross is not None and gross > 0:
        pension_pct = (pension_employee / gross) * 100.0
        if pension_pct > _PENSION_MANDATORY_MIN_PCT:
            pct_str = f"{pension_pct:.1f}".rstrip("0").rstrip(".")
            insights.append(Insight(
                id="ins_pension_above_minimum",
                kind="success",
                title="הפרשת פנסיה גבוהה ממינימום החובה",
                body=(
                    f"הפרשת הפנסיה שלך ({pct_str}%) גבוהה ממינימום החובה "
                    f"({_PENSION_MANDATORY_MIN_PCT:.0f}%), "
                    f"מה שמגדיל את החיסכון הפנסיוני שלך לטווח הארוך. "
                    f"ייתכן שהמעסיק מפריש גם הוא סכום מוגדל בהתאם."
                ),
            ))

    # -----------------------------------------------------------------------
    # 3. Minimum Wage Compliance — hourly rate above legal minimum
    # -----------------------------------------------------------------------
    # Look for a base-salary line item with a rate (תעריף) field
    _MIN_WAGE_KEYWORDS = re.compile(
        r'שכר\s+בסיס|משכורת\s+בסיס|שכר\s+יסוד|בסיס',
        re.UNICODE | re.IGNORECASE,
    )
    base_item_rate: Optional[float] = None
    for item in items:
        desc = getattr(item, "description_hebrew", "") or ""
        rate = getattr(item, "rate", None)
        if _MIN_WAGE_KEYWORDS.search(desc) and rate is not None and rate > 0:
            base_item_rate = rate
            break

    if base_item_rate is not None and base_item_rate >= _MIN_WAGE_HOURLY:
        insights.append(Insight(
            id="ins_min_wage_ok",
            kind="success",
            title="שכר הבסיס תקין",
            body=(
                f"שכר הבסיס שלך (₪{base_item_rate:,.2f} לשעה) תקין וגבוה "
                f"משכר המינימום החוקי (₪{_MIN_WAGE_HOURLY:,.2f} לשעה לשנת 2025). "
                f"המעסיק עומד בדרישות חוק שכר המינימום."
            ),
        ))
    elif base_item_rate is not None and 0 < base_item_rate < _MIN_WAGE_HOURLY:
        # Rate found but below minimum — flag as warning
        insights.append(Insight(
            id="ins_min_wage_low",
            kind="warning",
            title="שכר הבסיס נמוך משכר המינימום",
            body=(
                f"שכר הבסיס שזוהה (₪{base_item_rate:,.2f} לשעה) נמוך משכר המינימום "
                f"החוקי לשנת 2025 (₪{_MIN_WAGE_HOURLY:,.2f} לשעה). "
                f"מומלץ לבדוק זאת מול מחלקת השכר — ייתכן שמדובר בשגיאת קריאה."
            ),
        ))

    # -----------------------------------------------------------------------
    # 4. Havra'a (Recovery/Convalescence Pay) — detected in line items
    # -----------------------------------------------------------------------
    _HAVRAA_KEYWORDS = re.compile(r'הבראה', re.UNICODE | re.IGNORECASE)
    havraa_item = next(
        (item for item in items if _HAVRAA_KEYWORDS.search(
            getattr(item, "description_hebrew", "") or ""
        )),
        None,
    )
    if havraa_item is not None:
        val = getattr(havraa_item, "value", None)
        val_str = f" (₪{val:,.2f})" if val is not None else ""
        insights.append(Insight(
            id="ins_havraa_explained",
            kind="info",
            title="דמי הבראה — מה זה?",
            body=(
                f"שולמו לך דמי הבראה{val_str}. "
                f"זהו תשלום שנתי (או יחסי, לפי ותק) המגיע לכל עובד/ת לאחר שנת ותק ראשונה — "
                f"מטרתו לסייע בהוצאות נופש והחלמה. "
                f"גובה דמי ההבראה נקבע לפי ימי הבראה × תעריף (₪378 ליום בסקטור הפרטי לשנת 2025)."
            ),
        ))

    # -----------------------------------------------------------------------
    # 5. Missing Health Insurance — mandatory deduction not found
    # -----------------------------------------------------------------------
    if health_insurance is None and national_insurance is not None:
        # NI found but health missing → likely a parsing gap, not legal issue
        insights.append(Insight(
            id="ins_missing_health",
            kind="warning",
            title="מס בריאות לא זוהה בתלוש",
            body=(
                "לא זיהינו תשלום מס בריאות בתלוש. "
                "מס בריאות הוא ניכוי חובה הנלקח יחד עם ביטוח לאומי ומועבר למוסד לביטוח לאומי. "
                "ייתכן שהוא כלול בסכום ביטוח הלאומי שהוצג, או שלא זוהה בשל פורמט התלוש — "
                "מומלץ לבדוק את שורת 'מס בריאות' בתלוש הפיזי."
            ),
        ))

    # -----------------------------------------------------------------------
    # Sort: success first → info → warning (most positive impression first)
    # -----------------------------------------------------------------------
    _ORDER = {"success": 0, "info": 1, "warning": 2}
    insights.sort(key=lambda ins: _ORDER.get(ins.kind, 3))

    return insights
