"""
export_service.py — Phase 17: Professional PDF report generation.

Generates a Hebrew/RTL payslip analysis report from ParsedSlipPayload.
Uses fpdf2 for PDF creation and Rubik font for Hebrew text.

Design decisions:
  - generate_pdf() is a pure function: takes payload, returns bytes.
    No I/O side effects — caller streams or writes to disk.
  - Privacy by construction: employer_name and employee_name are always null
    in the payload schema; the PDF prints "עובד/ת" and "מעסיק" generics.
  - Hebrew RTL: all text cells use align="R"; bidi is handled automatically
    by fpdf2's bidirectional text algorithm.
  - Rubik font (~61 KB) is bundled in app/static/fonts/ and embedded in each PDF.
  - _kv_row() renders label (right, Hebrew) and value (left, numeric) as two
    side-by-side cells to simulate a RTL key-value table.
  - multi_cell() is used for long free-text (insight body, anomaly descriptions)
    to handle automatic word-wrapping.
  - Footer is rendered on every page via the FPDF footer() hook with a legal
    disclaimer in Hebrew.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from fpdf import FPDF, XPos, YPos

if TYPE_CHECKING:
    from app.models.schemas import ParsedSlipPayload

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bundled Rubik-Regular font — supports Hebrew Unicode glyphs
_FONT_PATH = Path(__file__).parent.parent / "static" / "fonts" / "Rubik-Regular.ttf"

# Category labels in Hebrew
_CATEGORY_LABEL: dict[str, str] = {
    "earning": "הכנסות",
    "deduction": "ניכויים",
    "employer_contribution": "הפרשות מעסיק",
    "benefit_in_kind": "שווי / זקיפות",
    "balance": "יתרות",
}

_INSIGHT_KIND_LABEL: dict[str, str] = {
    "success": "+",   # positive
    "info":    "i",   # informational
    "warning": "!",   # attention
}

_INSIGHT_KIND_COLOR: dict[str, tuple[int, int, int]] = {
    "success": (22, 163, 74),    # green-600
    "info":    (37, 99, 235),    # blue-600
    "warning": (217, 119, 6),    # amber-600
}

_LEGAL_DISCLAIMER = (
    "דוח זה נוצר למטרות חינוכיות בלבד על-ידי מערכת תלוש ברור. "
    "אין לראות בו ייעוץ מקצועי, משפטי, או פיננסי. "
    "לבירורים יש לפנות למחלקת השכר."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(n: Optional[float]) -> str:
    """Format a float as ILS currency string, e.g. '₪25,089.32'."""
    if n is None:
        return "—"
    return f"\u20aa{abs(n):,.2f}"   # ₪ + absolute value with 2 decimals


# ---------------------------------------------------------------------------
# Custom FPDF class
# ---------------------------------------------------------------------------

class _HebrewPDF(FPDF):
    """
    FPDF subclass with Rubik Hebrew font pre-loaded and legal footer on every page.
    """

    def __init__(self, font_path: Path = _FONT_PATH) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=22)
        self.set_margins(left=15, top=15, right=15)
        self.add_font("Rubik", "", str(font_path))

    def header(self) -> None:
        # No automatic page header — title is rendered once in generate_pdf()
        pass

    def footer(self) -> None:
        """Legal disclaimer in small gray text at the bottom of every page."""
        self.set_y(-15)
        self.set_font("Rubik", size=6)
        self.set_text_color(160, 160, 160)
        self.cell(0, 5, _LEGAL_DISCLAIMER, align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # Reset
        self.set_text_color(0, 0, 0)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _section_header(pdf: _HebrewPDF, title: str) -> None:
    """Render a shaded section header row (gray background, bold-sized text)."""
    pdf.set_font("Rubik", size=12)
    pdf.set_fill_color(235, 235, 235)
    pdf.cell(0, 9, title, fill=True, align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    pdf.set_font("Rubik", size=10)


def _kv_row(
    pdf: _HebrewPDF,
    label: str,
    value: str,
    row_h: float = 7.0,
) -> None:
    """
    Render a key-value pair row suited for Hebrew RTL layout.

    The value (typically a number) is in the left column and the label
    (Hebrew) is in the right column, so when read right-to-left the label
    comes first and the value follows — matching natural Hebrew reading order.

    label_w: 60% of effective page width (right side)
    value_w: 40% of effective page width (left side)
    """
    eff_w = pdf.epw
    label_w = eff_w * 0.65
    value_w = eff_w * 0.35
    y = pdf.get_y()

    # Label — right column
    pdf.set_xy(pdf.l_margin + value_w, y)
    pdf.cell(label_w, row_h, label, align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Value — left column (numeric, LTR)
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font("Rubik", size=10)
    pdf.cell(value_w, row_h, value, align="L",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(y + row_h)


def _separator(pdf: _HebrewPDF) -> None:
    """Draw a thin horizontal line separator."""
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + pdf.epw, pdf.get_y())
    pdf.ln(3)
    pdf.set_draw_color(0, 0, 0)


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def generate_pdf(payload: "ParsedSlipPayload") -> bytes:
    """
    Generate a professional Hebrew payslip analysis PDF report.

    Args:
        payload: Validated ParsedSlipPayload from the parsing pipeline.

    Returns:
        Raw PDF bytes. The caller is responsible for streaming or saving them.

    Privacy guarantee:
        - employer_name and employee_name are always null in the payload schema.
        - The PDF explicitly prints "עובד/ת" and "מעסיק" as generic placeholders.
        - No PII is written to the PDF.
    """
    pdf = _HebrewPDF()
    pdf.add_page()

    s = payload.summary
    meta = payload.slip_meta

    # ── Title ────────────────────────────────────────────────────────────
    pdf.set_font("Rubik", size=20)
    pdf.set_text_color(30, 64, 175)       # blue-800
    pdf.cell(0, 16, "תלוש ברור — דוח ניתוח שכר", align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)

    pdf.set_font("Rubik", size=9)
    pdf.set_text_color(100, 100, 100)
    month_display = meta.pay_month or "לא זוהה"
    subtitle = f"חודש: {month_display}   |   עובד/ת   |   מעסיק"
    pdf.cell(0, 6, subtitle, align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    _separator(pdf)

    # ── Financial Summary ────────────────────────────────────────────────
    _section_header(pdf, "סיכום פיננסי")
    pdf.set_font("Rubik", size=10)
    _kv_row(pdf, "ברוטו", _fmt(s.gross))
    _kv_row(pdf, "נטו לתשלום", _fmt(s.net))
    if s.total_deductions is not None:
        _kv_row(pdf, 'סה"כ ניכויים', _fmt(s.total_deductions))
    if s.total_employer_contributions is not None:
        _kv_row(pdf, "הפרשות מעסיק", _fmt(s.total_employer_contributions))
    pdf.ln(2)

    # ── Main Deductions ──────────────────────────────────────────────────
    deductions = [
        ("מס הכנסה", s.income_tax),
        ("ביטוח לאומי", s.national_insurance),
        ("מס בריאות", s.health_insurance),
        ("פנסיה (עובד)", s.pension_employee),
    ]
    visible_deductions = [(lbl, val) for lbl, val in deductions if val is not None]
    if visible_deductions:
        _section_header(pdf, "פירוט ניכויים עיקריים")
        pdf.set_font("Rubik", size=10)
        for lbl, val in visible_deductions:
            _kv_row(pdf, lbl, _fmt(val))
        pdf.ln(2)

    # ── Line Items (grouped by category) ────────────────────────────────
    if payload.line_items:
        _section_header(pdf, "פירוט שורות שכר")
        pdf.set_font("Rubik", size=10)
        current_cat: Optional[str] = None
        for item in payload.line_items:
            # Get category string value
            cat_val = (
                item.category.value
                if hasattr(item.category, "value")
                else str(item.category)
            )
            if cat_val != current_cat:
                current_cat = cat_val
                pdf.ln(1)
                pdf.set_font("Rubik", size=8)
                pdf.set_text_color(100, 100, 100)
                cat_label = _CATEGORY_LABEL.get(cat_val, cat_val)
                pdf.cell(0, 5, f"[ {cat_label} ]", align="R",
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Rubik", size=10)

            desc = (item.description_hebrew or "")[:60]
            val_str = _fmt(item.value)
            # Show quantity×rate if available
            if item.quantity is not None and item.rate is not None:
                val_str = f"{_fmt(item.value)}  ({item.quantity:.0f} × {_fmt(item.rate)})"
            _kv_row(pdf, desc, val_str)
        pdf.ln(2)

    # ── Smart Insights ───────────────────────────────────────────────────
    if payload.insights:
        _section_header(pdf, "תובנות חכמות")
        for ins in payload.insights:
            color = _INSIGHT_KIND_COLOR.get(ins.kind, (80, 80, 80))
            pdf.set_text_color(*color)
            pdf.set_font("Rubik", size=10)
            badge = _INSIGHT_KIND_LABEL.get(ins.kind, "•")
            pdf.cell(0, 7, f"[{badge}]  {ins.title}", align="R",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(70, 70, 70)
            pdf.set_font("Rubik", size=8)
            pdf.multi_cell(0, 5, ins.body, align="R")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)
        pdf.ln(1)

    # ── Anomalies ────────────────────────────────────────────────────────
    if payload.anomalies:
        _section_header(pdf, "חריגות ובדיקות")
        severity_color: dict[str, tuple[int, int, int]] = {
            "Critical": (185, 28, 28),   # red-700
            "Warning":  (180, 83, 9),    # amber-700
            "Info":     (37, 99, 235),   # blue-600
        }
        severity_prefix = {
            "Critical": "(!)",
            "Warning":  "(!)",
            "Info":     "(i)",
        }
        for a in payload.anomalies:
            sev = (
                a.severity.value
                if hasattr(a.severity, "value")
                else str(a.severity)
            )
            color = severity_color.get(sev, (80, 80, 80))
            prefix = severity_prefix.get(sev, "•")
            pdf.set_text_color(*color)
            pdf.set_font("Rubik", size=10)
            pdf.cell(0, 7, f"{prefix}  {a.what_we_found}", align="R",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(70, 70, 70)
            pdf.set_font("Rubik", size=8)
            if a.what_to_do:
                pdf.multi_cell(0, 5, a.what_to_do, align="R")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

    return bytes(pdf.output())
