"""Tekst uit documenten halen voor zoeken (bouwontwerp fase 6, Z2; Keuze 17 en 21).

Z0 legde de vorm van de uitkomst vast (result.py). Z2 bouwde de lezers:

- pdf.py (pypdfium2), office.py (de stand "alle tekst" van app/office/reader.py), plain.py
  (txt, md, csv), html.py, rtf.py, media.py (titel, artiest en album);
- normalize.py: de tekst opschonen (Keuze 21); collect.py: de grenzen per blob (tekens en de
  klok van 30 s CPU); read.py: één blob lezen, met de uitkomst als Extracted;
- frame.py: de uitkomsten op de pipe, in lengte-gemarkeerde stukken (JSON, dan de tekst).

De lezers draaien alleen in het kind van de worker (`app.render text`, Op.TEXT), of met media
uit in het renderproces van de app. Het app-proces zelf importeert pdf.py, office.py en de
kantoorlezer op het pad van de tekst nooit (test_extract_no_pdfium_in_app.py); hier staat
daarom alleen wat de app nodig heeft om te kiezen: welke soort lezer bij een inhoud hoort.
"""

from __future__ import annotations

from typing import Literal

Kind = Literal["pdf", "office", "plain", "html", "rtf", "media"]
KINDS: tuple[str, ...] = ("pdf", "office", "plain", "html", "rtf", "media")
# Oude kantoorformaten: geen eigen lezer; de tekst komt uit de PDF die OnlyOffice ervan maakte
# (fase 4). De app geeft dan die PDF als fd, met de soort pdf.
LEGACY = "legacy"

OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}
LEGACY_MIMES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}
PLAIN_MIMES = {"text/plain", "text/markdown", "text/x-markdown", "text/csv"}
HTML_MIMES = {"text/html", "application/xhtml+xml"}
RTF_MIMES = {"application/rtf", "text/rtf"}
PLAIN_EXTENSIONS = (".txt", ".md", ".markdown", ".csv")
# Groter dan dit: alleen de naam (skip)
MAX_BYTES = 512 * 1024 * 1024


def kind_for(mime: str | None, name: str | None = None) -> str | None:
    """De lezer voor een inhoud met deze soort (Blob.mime) en naam, LEGACY voor doc, xls en
    ppt, of None: dan heeft de stap text niets te doen (skip)."""
    mime = (mime or "").split(";")[0].strip().lower()
    lower = (name or "").lower()
    if mime == "application/pdf":
        return "pdf"
    if mime in OFFICE_MIMES:
        return "office"
    if mime in LEGACY_MIMES:
        return LEGACY
    if mime in RTF_MIMES or (mime in ("", "application/octet-stream") and lower.endswith(".rtf")):
        return "rtf"
    if mime in HTML_MIMES:
        return "html"
    if mime in PLAIN_MIMES:
        return "plain"
    if mime.startswith("text/") and lower.endswith(PLAIN_EXTENSIONS):
        return "plain"
    if mime.startswith(("audio/", "video/")):
        return "media"
    return None
