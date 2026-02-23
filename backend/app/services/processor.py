"""
Processing service.
Phase 2A: progress + result are persisted to the DB.
Phase 2B: PDFs with a text layer are parsed by the real parser (parser.py).
          Images / scanned PDFs fall back to the mock payload or return an error_code.
"""

from __future__ import annotations

import asyncio
import logging
import random

from app.models.schemas import (
    Anomaly,
    AnomalySeverity,
    LineItem,
    LineItemCategory,
    ParsedSlipPayload,
    QuickAnswers,
    SectionBlock,
    SlipMeta,
    SummaryTotals,
    TaxCreditsDetected,
)
from app.db.crud import (
    get_quick_answers,
    get_upload,
    update_upload_status,
    upsert_result,
)
from app.db.database import AsyncSessionLocal
from app.services.parser import parse_pdf, parse_with_ocr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mocked payload builder
# ---------------------------------------------------------------------------

def _build_mock_payload(answers: QuickAnswers | None) -> ParsedSlipPayload:
    """
    Return a realistic-looking mocked payslip payload.
    Answers are used to lightly adjust severity/notes so the UI
    can show the 'adapted to your answers' banner meaningfully.
    """
    multiple_employers = answers and answers.multiple_employers == "yes"
    has_pension = answers is None or (answers.has_pension != "no")
    has_car = answers and answers.has_benefit_in_kind == "yes"
    big_change = answers and answers.big_change_this_month == "yes"

    # ---- line items ----
    line_items: list[LineItem] = [
        LineItem(
            id="li_base",
            category=LineItemCategory.EARNING,
            description_hebrew="שכר יסוד",
            explanation_hebrew="שכר הבסיס החודשי שלך לפני כל תוספות.",
            value=12000.0,
            raw_text="שכר יסוד 12,000",
            confidence=0.97,
        ),
        LineItem(
            id="li_overtime",
            category=LineItemCategory.EARNING,
            description_hebrew="שעות נוספות 125%",
            explanation_hebrew="תגמול על שעות עבודה מעבר לנורמה בתעריף של 125% — הגדלה של 25% על כל שעה.",
            value=850.0,
            raw_text="שע\"נ 125% 850",
            confidence=0.88,
        ),
        LineItem(
            id="li_travel",
            category=LineItemCategory.EARNING,
            description_hebrew="החזר נסיעות",
            explanation_hebrew="החזר הוצאות נסיעה לעבודה. סכום מרבי יומי נקבע לפי מס הכנסה.",
            value=550.0,
            raw_text="נסיעות 550",
            confidence=0.91,
        ),
        LineItem(
            id="li_meal",
            category=LineItemCategory.EARNING,
            description_hebrew="דמי הבראה",
            explanation_hebrew="תוספת שנתית לפי יום הבראה, משולמת לרוב בחודשי יולי/אוגוסט. אם מופיעה עכשיו – כנראה פיצול חודשי.",
            value=380.0,
            raw_text="הבראה 380",
            confidence=0.82,
        ),
        LineItem(
            id="li_car",
            category=LineItemCategory.BENEFIT_IN_KIND,
            description_hebrew="שווי רכב",
            explanation_hebrew=(
                "זקיפת שווי רכב — הסכום שמס הכנסה רואה כהכנסה עבור השימוש ברכב החברה. "
                "מגדיל את ברוטו לצורכי מס אך לא משפיע על תשלום בפועל."
            ) if has_car else "שווי רכב לא מזוהה בתלוש זה.",
            value=2470.0 if has_car else None,
            raw_text="שווי רכב 2,470" if has_car else None,
            confidence=0.79 if has_car else 0.3,
            is_unknown=not has_car,
            unknown_guesses=[] if has_car else ["שווי רכב", "הוצאות רכב", "שימוש ברכב"],
            unknown_question="האם יש לך רכב צמוד?" if not has_car else None,
        ),
        LineItem(
            id="li_income_tax",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס הכנסה",
            explanation_hebrew="ניכוי מס הכנסה מחושב לפי מדרגות המס ונקודות הזיכוי שלך.",
            value=-2340.0,
            raw_text="מס הכנסה (2,340)",
            confidence=0.95,
        ),
        LineItem(
            id="li_bituach",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ביטוח לאומי (עובד)",
            explanation_hebrew="ניכוי ביטוח לאומי מחלק העובד — מממן גמלאות נכות, אבטלה ועוד.",
            value=-620.0,
            raw_text="ב\"ל עובד (620)",
            confidence=0.96,
        ),
        LineItem(
            id="li_health",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ביטוח בריאות",
            explanation_hebrew="ניכוי מס בריאות — ממן את קופות החולים.",
            value=-190.0,
            raw_text="בריאות (190)",
            confidence=0.96,
        ),
        LineItem(
            id="li_pension_emp",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="פנסיה (עובד)",
            explanation_hebrew=(
                "ניכוי חלק העובד לפנסיה. לפי חוק, המינימום הוא 6% משכר הבסיס."
            ) if has_pension else "לא זוהתה פנסיה. בדוק אם יש ניכוי בשם אחר.",
            value=-720.0 if has_pension else None,
            raw_text="פנסיה עובד (720)" if has_pension else None,
            confidence=0.93 if has_pension else 0.2,
            is_unknown=not has_pension,
            unknown_guesses=[] if has_pension else ["פנסיה", "קרן פנסיה", "גמל"],
            unknown_question="האם מנוכה לך פנסיה מהתלוש?" if not has_pension else None,
        ),
        LineItem(
            id="li_pension_er",
            category=LineItemCategory.EMPLOYER_CONTRIBUTION,
            description_hebrew="פנסיה (מעסיק)",
            explanation_hebrew="הפרשת המעסיק לפנסיה — לפחות 6.5% משכר הבסיס. כסף שמגיע לך מעבר לשכר הנטו.",
            value=780.0 if has_pension else None,
            raw_text="פנסיה מעסיק 780" if has_pension else None,
            confidence=0.90 if has_pension else 0.2,
        ),
        LineItem(
            id="li_bituach_er",
            category=LineItemCategory.EMPLOYER_CONTRIBUTION,
            description_hebrew="ביטוח לאומי (מעסיק)",
            explanation_hebrew="חלק המעסיק בביטוח לאומי — עלות ישירה למעסיק שאינה מופחתת מהנטו שלך.",
            value=1150.0,
            raw_text="ב\"ל מעסיק 1,150",
            confidence=0.88,
        ),
        LineItem(
            id="li_unknown_1",
            category=LineItemCategory.EARNING,
            description_hebrew="לא מזוהה",
            explanation_hebrew="שורה שלא הצלחנו לזהות בוודאות. ראה הצעות אפשריות למטה.",
            value=300.0,
            raw_text="תמריץ ביצוע 300",
            confidence=0.41,
            is_unknown=True,
            unknown_guesses=["בונוס ביצוע", "תמריץ", "פרמיה"],
            unknown_question="מה לשאול את השכר?: האם קיבלת בונוס ביצוע החודש?",
        ),
    ]

    # ---- anomalies (severity adjusted by answers) ----
    pension_severity = AnomalySeverity.INFO if (big_change or not has_pension) else AnomalySeverity.WARNING

    anomalies: list[Anomaly] = [
        Anomaly(
            id="ano_net_mismatch",
            severity=AnomalySeverity.CRITICAL if not big_change else AnomalySeverity.WARNING,
            what_we_found="פער בין ברוטו פחות ניכויים לנטו: ₪87",
            why_suspicious="חישוב פשוט: ברוטו 16,250 פחות ניכויים 3,870 = 12,380. הנטו בתלוש הוא 12,293. הפרש של ₪87 לא מוסבר.",
            what_to_do="בדוק אם יש ניכוי נסתר (הלוואה, עיקול, ביטוח מנהלים) שלא מופיע בפירוט. השווה לחודש הקודם.",
            ask_payroll="האם יש ניכוי נוסף שאינו מפורט בתלוש החודש?",
            related_line_item_ids=["li_base", "li_income_tax"],
        ),
        Anomaly(
            id="ano_travel_high",
            severity=AnomalySeverity.WARNING,
            what_we_found="החזר נסיעות גבוה מהצפוי (₪550 על 22 ימי עבודה)",
            why_suspicious="התעריף היומי המרבי לפי מס הכנסה הוא ₪22.60 ליום (2024). עבור 22 ימים: ₪497.20. הסכום המשולם גבוה ב-₪53.",
            what_to_do="בדוק אם יש הסכם קיבוצי או חוזה אישי המתיר סכום גבוה יותר.",
            ask_payroll="על פי מה מחושב החזר הנסיעות שלי?",
            related_line_item_ids=["li_travel"],
        ),
        Anomaly(
            id="ano_pension_low",
            severity=pension_severity,
            what_we_found="ניכוי פנסיה (6%) נמוך ממינימום חוקי אפשרי בהתחשב בשכר הבסיס",
            why_suspicious="שכר הבסיס הוא ₪12,000. 6% = ₪720. הניכוי בתלוש: ₪720 — תואם בדיוק. אולם המעסיק מפריש 6.5% ולא 7.5% כנדרש בהסכם קיבוצי בענף ההיי-טק.",
            what_to_do="בדוק אם ההסכם הקיבוצי בענף שלך דורש הפרשה גבוהה יותר.",
            ask_payroll="מה שיעור הפרשת המעסיק לפנסיה ומהו הבסיס לחישוב?",
            related_line_item_ids=["li_pension_emp", "li_pension_er"],
        ),
        Anomaly(
            id="ano_multiple_employers" if multiple_employers else "ano_tax_credits",
            severity=AnomalySeverity.INFO,
            what_we_found=(
                "זיהינו מספר מעסיקים — יתכן שנקודות הזיכוי מנוצלות אצל מעסיק אחר"
                if multiple_employers else
                "נקודות זיכוי: רק 2.75 זוהו. הצפוי לעובד/ת רגיל/ה: ≥ 2.75"
            ),
            why_suspicious=(
                "כשיש מספר מעסיקים, כל אחד מחשב מס בנפרד. בסוף השנה ייתכן חיוב/זכות מס."
                if multiple_employers else
                "בדוק אם מגיעות לך נקודות נוספות: ילדים, תואר אקדמי, תושב אזור עדיפות."
            ),
            what_to_do=(
                "שקול להגיש טופס 116ג לתיאום מס בין המעסיקים."
                if multiple_employers else
                "עבור לטאב 'נקודות זיכוי' למילוי שאלון מהיר."
            ),
            ask_payroll=(
                "האם מנוכה לי מס מלא גם אצלך? האם הגשתי טופס תיאום מס?"
                if multiple_employers else
                "איזה טופס עלי להגיש כדי לקבל נקודות זיכוי נוספות?"
            ),
        ),
        Anomaly(
            id="ano_unknown_item",
            severity=AnomalySeverity.INFO,
            what_we_found="שורה אחת בתלוש לא זוהתה אוטומטית (₪300)",
            why_suspicious="הטקסט 'תמריץ ביצוע' אינו שם תקני — יתכן שזה בונוס, תמריץ מכירות, או פרמיה.",
            what_to_do="ראה את הכרטיס 'לא מזוהה' בפירוט המלא ובחר את הקטגוריה הנכונה.",
            ask_payroll="מהו 'תמריץ ביצוע' בתלוש שלי ואיך הוא ממוסה?",
            related_line_item_ids=["li_unknown_1"],
        ),
    ]

    # ---- section blocks ----
    blocks: list[SectionBlock] = [
        SectionBlock(
            section_name="כותרת התלוש",
            bbox_json={"x": 0, "y": 0, "w": 800, "h": 120},
            page_index=0,
            raw_text_preview="שם עובד: [מושחת]  ת.ז: [מושחת]  חודש: 01/2025",
        ),
        SectionBlock(
            section_name="רכיבי שכר",
            bbox_json={"x": 0, "y": 130, "w": 800, "h": 280},
            page_index=0,
            raw_text_preview="שכר יסוד 12,000 | שע\"נ 850 | נסיעות 550 ...",
        ),
        SectionBlock(
            section_name="ניכויים",
            bbox_json={"x": 0, "y": 420, "w": 800, "h": 200},
            page_index=0,
            raw_text_preview="מס הכנסה (2,340) | ב\"ל (620) | בריאות (190) ...",
        ),
    ]

    # ---- summary ----
    gross = sum(
        li.value for li in line_items
        if li.category in (LineItemCategory.EARNING, LineItemCategory.BENEFIT_IN_KIND)
        and li.value is not None
    )
    total_deductions = sum(
        abs(li.value) for li in line_items
        if li.category == LineItemCategory.DEDUCTION and li.value is not None
    )
    net = gross - total_deductions

    integrity_notes = []
    if abs((net) - 12293) > 100:
        integrity_notes.append(f"נטו מחושב ({net:.0f}) שונה מנטו בתלוש (12,293) — בדוק ניכויים נסתרים")

    summary = SummaryTotals(
        gross=round(gross, 2),
        gross_confidence=0.93,
        net=12293.0,   # as printed on slip
        net_confidence=0.97,
        total_deductions=round(total_deductions, 2),
        total_employer_contributions=1930.0,
        income_tax=2340.0,
        national_insurance=620.0,
        health_insurance=190.0,
        pension_employee=720.0 if has_pension else None,
        integrity_ok=len(integrity_notes) == 0,
        integrity_notes=integrity_notes,
    )

    tax_credits = TaxCreditsDetected(
        credit_points_detected=2.75,
        estimated_monthly_value=223.0,
        confidence=0.72,
        notes=["זוהו 2.75 נקודות זיכוי בסיסיות", "ייתכנו נקודות נוספות לפי פרטיים"],
    )

    return ParsedSlipPayload(
        slip_meta=SlipMeta(
            pay_month="2025-01",
            provider_guess="חילן",
            confidence=0.68,
            employer_name="חברה לדוגמה בע\"מ",
            employee_name_redacted=True,
        ),
        summary=summary,
        line_items=line_items,
        anomalies=anomalies,
        blocks=blocks,
        tax_credits_detected=tax_credits,
        answers_applied=answers is not None,
        parse_source="mock",
    )


# ---------------------------------------------------------------------------
# Internal DB helper
# ---------------------------------------------------------------------------

async def _update(upload_id: str, *, status: str, stage: str, pct: int, error: str | None = None) -> None:
    """Persist status + progress to DB in its own short-lived session."""
    async with AsyncSessionLocal() as db:
        await update_upload_status(
            db, upload_id, status=status, progress_stage=stage, progress_pct=pct, error_message=error
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Background job simulation
# ---------------------------------------------------------------------------

async def run_processing_job(upload_id: str) -> None:
    """
    Real processing pipeline (Phase 2B).
      1-3. Update progress stages with short UX delays.
      4.   Fetch file path + MIME type + quick answers from DB.
      5.   Route: PDF with text layer → real parser; otherwise → mock.
      6.   Persist result_json to DB, mark done.
    Errors are caught and persisted to DB as 'failed'.
    """
    logger.info("Processing job started for upload_id=%s", upload_id)

    try:
        # -- Stage 1: initial progress beat --
        await _update(upload_id, status="processing", stage="מעבד את התלוש…", pct=10)
        await asyncio.sleep(random.uniform(0.3, 0.5))

        # -- Stage 2: announce text extraction --
        await _update(upload_id, status="processing", stage="מחלץ טקסט…", pct=35)
        await asyncio.sleep(random.uniform(0.3, 0.5))

        # -- Stage 3: announce field analysis --
        await _update(upload_id, status="processing", stage="מנתח שורות ורכיבי שכר…", pct=65)
        await asyncio.sleep(random.uniform(0.2, 0.4))

        # -- Stage 4: real work starts here --
        await _update(upload_id, status="processing", stage="בודק תקינות וחריגות…", pct=88)

        # Fetch upload row (eager-loads upload_file) and quick answers in one session
        file_path: str | None = None
        mime_type: str | None = None
        transient_flag: bool = False
        answers_dict: dict | None = None
        async with AsyncSessionLocal() as db:
            upload_row = await get_upload(db, upload_id)
            if upload_row:
                mime_type = upload_row.mime_type
                transient_flag = bool(upload_row.transient)
                if upload_row.upload_file:
                    file_path = upload_row.upload_file.path
            answers_dict = await get_quick_answers(db, upload_id)

        answers_obj: QuickAnswers | None = None
        if answers_dict:
            try:
                answers_obj = QuickAnswers(**answers_dict)
            except Exception:
                answers_obj = None

        # Route: PDF → real parser; scanned PDF → OCR upgrade; image → direct OCR
        is_pdf = (mime_type == "application/pdf")
        if is_pdf and file_path:
            try:
                # asyncio.to_thread runs the sync pdfplumber call in a thread pool
                payload = await asyncio.to_thread(parse_pdf, file_path, answers_obj)
            except Exception as exc:
                logger.exception(
                    "parse_pdf failed for upload_id=%s, falling back to mock: %s",
                    upload_id, exc,
                )
                payload = _build_mock_payload(answers_obj)
            # Upgrade OCR_REQUIRED → real Tesseract OCR attempt
            if payload.error_code == "OCR_REQUIRED":
                await _update(
                    upload_id, status="processing",
                    stage="מנסה לקרוא בעזרת OCR…", pct=75
                )
                try:
                    payload = await asyncio.to_thread(
                        parse_with_ocr, file_path, mime_type, answers_obj, transient_flag
                    )
                except Exception as exc:
                    logger.exception(
                        "parse_with_ocr (PDF) failed for upload_id=%s: %s",
                        upload_id, exc,
                    )
                    # Keep existing payload (OCR_REQUIRED) as final result
        elif file_path:
            # Non-PDF image (JPG, PNG, HEIC) → direct OCR
            await _update(
                upload_id, status="processing",
                stage="קורא תמונה עם OCR…", pct=50
            )
            try:
                payload = await asyncio.to_thread(
                    parse_with_ocr, file_path, mime_type or "image/jpeg", answers_obj, transient_flag
                )
            except Exception as exc:
                logger.exception(
                    "parse_with_ocr (image) failed for upload_id=%s: %s",
                    upload_id, exc,
                )
                payload = _build_mock_payload(answers_obj)
        else:
            payload = _build_mock_payload(answers_obj)

        payload_dict = payload.model_dump(mode="json")

        # -- Persist result + mark done --
        async with AsyncSessionLocal() as db:
            await upsert_result(db, upload_id=upload_id, result_dict=payload_dict)
            await update_upload_status(
                db, upload_id, status="done", progress_stage="הניתוח הושלם", progress_pct=100
            )
            await db.commit()

        logger.info(
            "Processing done for upload_id=%s parse_source=%s error_code=%s",
            upload_id,
            payload.parse_source,
            payload.error_code,
        )

    except Exception as exc:
        logger.exception("Processing failed for upload_id=%s: %s", upload_id, exc)
        try:
            await _update(
                upload_id,
                status="failed",
                stage="נכשל",
                pct=0,
                error="שגיאה פנימית בעיבוד התלוש. אנא נסה שוב.",
            )
        except Exception:
            pass
