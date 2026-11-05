"""html (bouwontwerp fase 6, Keuze 17): de tags weg met html.parser, de titel als meta.

Script, style en template tellen niet mee. Na een blok (p, div, li, een kop, een rij …) komt een
regeleinde, na een cel een spatie. De codering: een BOM, anders de charset uit een meta-tag in
het begin, anders UTF-8 als dat klopt, en anders cp1252.
"""

from __future__ import annotations

import codecs
import re
from html.parser import HTMLParser
from typing import BinaryIO

from app.extract import plain
from app.extract.collect import Collector
from app.extract.normalize import fold_meta

HTML_BYTES = 32 * 1024 * 1024
HEAD_BYTES = 4096
SKIP = {"script", "style", "template", "noscript", "head"}
BLOCKS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre", "hr", "dd", "dt", "dl",
    "address", "figcaption", "main", "nav", "aside", "form", "fieldset", "legend", "caption",
}  # fmt: skip
CELLS = {"td", "th"}
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]{1,40})""", re.I)
TITLE_CHARS = 500


def codec_of(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return plain.codec_of(data)
    found = _CHARSET.search(data[:HEAD_BYTES])
    if found:
        try:
            name = codecs.lookup(found.group(1).decode("ascii")).name
        except (LookupError, UnicodeDecodeError):
            name = ""
        # Alleen een codec voor tekst die op ASCII lijkt (geen UTF-16 zonder BOM, geen rot13)
        if name and name not in ("utf-16", "utf-32", "rot-13") and not name.startswith("utf_16"):
            return "cp1252" if name in ("latin-1", "iso8859-1") else name
    return plain.codec_of(data)


class _Text(HTMLParser):
    def __init__(self, out: Collector):
        super().__init__(convert_charrefs=True)
        self.out = out
        self.skip = 0
        self.in_title = False
        self.title: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self.in_title = True
        if tag in SKIP:
            self.skip += 1
        elif tag in BLOCKS:
            self.out.add("\n")
        elif tag in CELLS:
            self.out.add(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in BLOCKS:
            self.out.add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCKS:
            self.out.add("\n")
        elif tag in CELLS:
            self.out.add(" ")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            if sum(map(len, self.title)) < TITLE_CHARS:
                self.title.append(data)
            return
        if not self.skip:
            self.out.add(data)


def read(f: BinaryIO, out: Collector) -> None:
    data = f.read(HTML_BYTES)
    if f.read(1):
        out.truncate()
    text = plain.decode(data, codec_of(data))
    del data
    parser = _Text(out)
    try:
        for start in range(0, len(text), plain.CHUNK_CHARS):
            parser.feed(text[start : start + plain.CHUNK_CHARS])
            out.check()
        parser.close()
    finally:
        # Ook als de lezer stopt (vol, of de klok): de titel tot dan
        out.meta = fold_meta("".join(parser.title))
