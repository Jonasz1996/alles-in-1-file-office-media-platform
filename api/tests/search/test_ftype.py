"""node.ftype (een virtuele kolom met de CASE uit app/search/schema.py) zegt hetzelfde als
mime.ftype(), voor elke mime die mime.py kent en voor elke soort node; en de groepen kloppen
met mime.category() (een archief is "other", rekenbladen en presentaties een eigen soort)."""

import sqlite3

import pytest

from app import mime
from app.search import schema


def known_mimes() -> set[str | None]:
    found: set[str | None] = {None, "", "application/octet-stream", "application/pdf"}
    for table in (mime.ZIP_BY_EXT, mime.OLE_BY_EXT, mime.TEXT_BY_EXT, mime.MEDIA_BY_EXT):
        found |= set(table.values())
    found |= set(mime.COMPRESSED.values()) | mime.ARCHIVE_TYPES | mime.DOCUMENT_TYPES
    found |= set(mime.FTYP_IMAGE.values()) | set(mime.FTYP_OTHER.values())
    found |= {"image/jpeg", "image/png", "video/mp4", "audio/mpeg", "text/plain", "font/woff"}
    return found


def sql_ftype(rows: list[tuple[str, str | None]]) -> list[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"CREATE TABLE node (kind TEXT, mime TEXT, ftype TEXT GENERATED ALWAYS AS "
        f"({schema.FTYPE_SQL}) VIRTUAL)"
    )
    conn.executemany("INSERT INTO node (kind, mime) VALUES (?, ?)", rows)
    return [r[0] for r in conn.execute("SELECT ftype FROM node ORDER BY rowid")]


@pytest.mark.parametrize("kind", ["file", "dir", "album"])
def test_sql_gelijk_aan_python(kind):
    rows = [(kind, m) for m in sorted(known_mimes(), key=str)]
    assert sql_ftype(rows) == [mime.ftype(m, k) for k, m in rows]


def test_de_soorten():
    assert set(mime.FTYPES) == {
        "folder", "image", "video", "audio", "pdf", "document", "spreadsheet", "presentation",
        "other",
    }  # fmt: skip
    seen = {mime.ftype(m) for m in known_mimes()} | {mime.ftype(None, "dir")}
    assert seen == set(mime.FTYPES)
    assert mime.ftype(mime.XLSX) == "spreadsheet"
    assert mime.ftype("text/csv") == "spreadsheet"
    assert mime.ftype(mime.ODP) == "presentation"
    assert mime.ftype("application/zip") == "other"
    assert mime.ftype("image/jpeg", "album") == "other"


def test_gelijk_aan_category_buiten_de_nieuwe_soorten():
    """Wat geen rekenblad, presentatie of archief is, krijgt dezelfde soort als
    mime.category() (die de filters van 0.7 en de pictogrammen gebruiken)."""
    split = mime.SPREADSHEET_TYPES | mime.PRESENTATION_TYPES
    for m in known_mimes():
        cat = mime.category(m)
        if m in split:
            assert cat == "document", m
        elif cat == "archive":
            assert mime.ftype(m) == "other", m
        else:
            assert mime.ftype(m) == cat, m
