"""
Tax Credits Wizard — Phase 5.

Pure computation module (no I/O, no DB, no imports from parser.py).
Implements 2025 Israeli tax authority credit point rules for the six core categories
covered by the wizard.

Usage:
    from app.services.credits_wizard import compute_credits
    result = compute_credits(request, detected_points=2.25)

Israeli credit point rules (2025):
    Base (Israeli resident)              2.25  pts  (everyone)
    Married / registered partnership    +0.50  pts  (total 2.75 for married)
    First child under 18               +1.00  pts
    Each additional child (2nd+)        +0.50  pts  each
    Academic degree (BA/BSc or higher) +1.00  pts  (pct. 34(c) of Income Tax Ordinance)
    Completed IDF / national service   +0.50  pts
    New immigrant (< 3.5 yrs in IL)    +0.50  pts  (conservative; actual may vary 0.5–3.0)

Mismatch threshold: |gap| ≥ 0.50 points is considered a meaningful discrepancy.
"""

from __future__ import annotations

MISMATCH_THRESHOLD = 0.50   # minimum gap to report as "under" or "over"


def compute_credits(
    request: object,
    detected_points: float | None,
) -> object:
    """
    Compute expected credit points from wizard answers and compare to detected.

    Args:
        request: CreditWizardRequest instance (fields: marital_status, num_children,
                 has_degree, has_army_service, is_new_immigrant, is_disabled).
        detected_points: credit points read from the payslip (None if not found).

    Returns:
        CreditWizardResult with expected_points, gap, gap_direction, components,
        mismatch_reasons, what_to_do, confidence, disclaimer.
    """
    from app.models.schemas import (
        CreditWizardResult,
        CreditPointComponent,
    )

    # ------------------------------------------------------------------
    # Extract request fields with safe getattr fallback
    # ------------------------------------------------------------------
    marital_status = getattr(request, "marital_status", "unknown")
    num_children   = int(getattr(request, "num_children", 0) or 0)
    has_degree     = getattr(request, "has_degree", "unknown")
    has_army       = getattr(request, "has_army_service", "unknown")
    is_immigrant   = getattr(request, "is_new_immigrant", "unknown")
    is_disabled    = getattr(request, "is_disabled", "unknown")

    components: list[CreditPointComponent] = []
    unknown_count = 0

    # ------------------------------------------------------------------
    # Rule 1: Base Israeli resident (everyone)
    # ------------------------------------------------------------------
    components.append(CreditPointComponent(
        label_hebrew="תושב/ת ישראל (בסיס)",
        points=2.25,
        applied=True,
    ))

    # ------------------------------------------------------------------
    # Rule 2: Marital status
    # ------------------------------------------------------------------
    is_married = marital_status == "married"
    if marital_status == "unknown":
        unknown_count += 1
    components.append(CreditPointComponent(
        label_hebrew="נשוי/אה או ידוע/ה בציבור",
        points=0.50,
        applied=is_married,
    ))

    # ------------------------------------------------------------------
    # Rule 3: Children under 18
    # ------------------------------------------------------------------
    child_pts = 0.0
    if num_children >= 1:
        child_pts += 1.00            # first child
    if num_children >= 2:
        child_pts += 0.50 * (num_children - 1)   # 2nd+ children
    components.append(CreditPointComponent(
        label_hebrew=f"ילדים מתחת לגיל 18 ({num_children})",
        points=child_pts,
        applied=num_children > 0,
    ))

    # ------------------------------------------------------------------
    # Rule 4: Academic degree
    # ------------------------------------------------------------------
    degree_applied = has_degree == "yes"
    if has_degree == "unknown":
        unknown_count += 1
    components.append(CreditPointComponent(
        label_hebrew="תואר אקדמי (BA/BSc ומעלה)",
        points=1.00,
        applied=degree_applied,
    ))

    # ------------------------------------------------------------------
    # Rule 5: Completed IDF / national service
    # ------------------------------------------------------------------
    army_applied = has_army == "yes"
    if has_army == "unknown":
        unknown_count += 1
    components.append(CreditPointComponent(
        label_hebrew="שירות צבאי / לאומי מלא",
        points=0.50,
        applied=army_applied,
    ))

    # ------------------------------------------------------------------
    # Rule 6: New immigrant (< 3.5 years in Israel)
    # Conservative: +0.50 pts (actual entitlement may be higher)
    # ------------------------------------------------------------------
    immigrant_applied = is_immigrant == "yes"
    if is_immigrant == "unknown":
        unknown_count += 1
    components.append(CreditPointComponent(
        label_hebrew="עולה חדש/ה (פחות מ-3.5 שנים בישראל)",
        points=0.50,
        applied=immigrant_applied,
    ))

    # ------------------------------------------------------------------
    # Sum expected points from applied components
    # ------------------------------------------------------------------
    expected_points = sum(c.points for c in components if c.applied)

    # ------------------------------------------------------------------
    # Confidence: base 0.90, minus 0.10 per unknown field (floor 0.10)
    # unknown fields counted: marital_status, has_degree, has_army, is_immigrant
    # (is_disabled is informational only, doesn't affect confidence)
    # ------------------------------------------------------------------
    confidence = max(0.10, round(0.90 - 0.10 * unknown_count, 2))

    # ------------------------------------------------------------------
    # Compare to detected
    # ------------------------------------------------------------------
    gap: float | None = None
    gap_direction = "unknown"
    mismatch_reasons: list[str] = []
    what_to_do = ""

    if detected_points is not None:
        gap = round(expected_points - detected_points, 2)
        if abs(gap) < MISMATCH_THRESHOLD:
            gap_direction = "ok"
            what_to_do = "נקודות הזיכוי שזוהו בתלוש תואמות את הצפוי לפי פרטיך. ✅"
        elif gap > 0:
            # detected < expected → employee may be under-claiming
            gap_direction = "under"
            what_to_do = (
                "ייתכן שמגיעות לך נקודות זיכוי נוספות שלא מדווחות. "
                "מלא/י טופס 101 מעודכן אצל המעסיק ופנה/י לרואה חשבון לבדיקה."
            )
            # Build specific reasons
            mismatch_reasons.append("ייתכן שטופס 101 לא עודכן לפי מצבך האישי הנוכחי")
            if is_married and marital_status != "unknown":
                mismatch_reasons.append("מצב משפחתי (נישואין) לא מדווח בטופס 101")
            if num_children > 0:
                mismatch_reasons.append("ילדים לא דווחו למעסיק דרך טופס 101")
            if degree_applied:
                mismatch_reasons.append("תואר אקדמי לא הוצהר בטופס 101")
            if army_applied:
                mismatch_reasons.append("שירות צבאי / לאומי לא הוצהר בטופס 101")
        else:
            # detected > expected → employee may be over-claiming
            gap_direction = "over"
            what_to_do = (
                "נקודות הזיכוי שזוהו בתלוש גבוהות מהצפוי לפי פרטיך. "
                "זה עלול לגרום לחוב מס בסוף השנה. בדוק אם יש תיאום מס עם מעסיק נוסף."
            )
            mismatch_reasons.append(
                "ייתכן שנקודות זיכוי מדווחות אצל יותר ממעסיק אחד — בדוק תיאום מס"
            )
            mismatch_reasons.append(
                "ייתכן שפרטי הזיכאות בטופס 101 שונים מהמצב שדיווחת בשאלון זה"
            )
    else:
        # No detected points on slip — can't compare
        gap_direction = "unknown"
        what_to_do = (
            "לא זוהו נקודות זיכוי בתלוש. "
            "בדוק שטופס 101 הוגש למעסיק ושנקודות הזיכוי מופיעות בתלוש."
        )
        mismatch_reasons.append("לא ניתן להשוות — נקודות זיכוי לא זוהו בתלוש")

    return CreditWizardResult(
        expected_points=round(expected_points, 2),
        detected_points=detected_points,
        gap=gap,
        gap_direction=gap_direction,
        components=components,
        mismatch_reasons=mismatch_reasons,
        what_to_do=what_to_do,
        confidence=confidence,
    )
