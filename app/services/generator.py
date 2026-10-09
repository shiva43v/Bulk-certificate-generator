"""Certificate PDF rendering using ReportLab.

One predefined template (landscape A4) drawn in code: border, title, recipient
name, event, date, issuer and a certificate ID for verification.
"""
import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# DejaVu supports many scripts (accents, Cyrillic, Greek...). The Docker image installs it.
# If it's missing we fall back to Helvetica, which only supports Latin-1 text.
_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
try:
    pdfmetrics.registerFont(TTFont("DejaVu", str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
    FONT, FONT_BOLD = "DejaVu", "DejaVu-Bold"
except Exception:  # pragma: no cover - depends on host fonts
    pass

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8860b")
GREY = colors.HexColor("#555555")


def _draw_fitted(c: canvas.Canvas, text: str, y: float, font: str, size: float,
                 color, max_width: float = PAGE_W - 160, min_size: float = 9) -> None:
    """Draw centred text, shrinking the font so long values stay inside the border."""
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 1
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(PAGE_W / 2, y, text)


def generate_certificate_pdf(
    output_path: Path,
    *,
    recipient_name: str,
    event_name: str,
    issued_by: str,
    issue_date: date,
    certificate_id: str,
) -> None:
    """Render a certificate to `output_path`.

    Written to a temp file first and renamed, so a failure never leaves a
    half-written PDF behind.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    try:
        c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
        c.setTitle(f"Certificate - {recipient_name}")
        c.setAuthor(issued_by)

        # Double border
        c.setStrokeColor(NAVY)
        c.setLineWidth(4)
        c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        c.rect(36, 36, PAGE_W - 72, PAGE_H - 72)

        _draw_fitted(c, "CERTIFICATE", PAGE_H - 120, FONT_BOLD, 44, NAVY)
        _draw_fitted(c, "OF COMPLETION", PAGE_H - 150, FONT, 18, GOLD)

        _draw_fitted(c, "This is to certify that", PAGE_H - 215, FONT, 16, GREY)
        _draw_fitted(c, recipient_name, PAGE_H - 270, FONT_BOLD, 38, NAVY, min_size=10)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.line(PAGE_W / 2 - 200, PAGE_H - 282, PAGE_W / 2 + 200, PAGE_H - 282)

        _draw_fitted(c, "has successfully completed", PAGE_H - 320, FONT, 16, GREY)
        _draw_fitted(c, event_name, PAGE_H - 360, FONT_BOLD, 24, NAVY, min_size=10)

        # Footer: date (left), issuer (right)
        c.setFillColor(GREY)
        c.setFont(FONT, 12)
        c.drawString(90, 110, f"Date: {issue_date.strftime('%d %B %Y')}")
        c.drawRightString(PAGE_W - 90, 110, f"Issued by: {issued_by[:60]}")
        c.setFont(FONT, 8)
        c.drawCentredString(PAGE_W / 2, 55, f"Certificate ID: {certificate_id}")

        c.showPage()
        c.save()
        os.replace(tmp_path, output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
