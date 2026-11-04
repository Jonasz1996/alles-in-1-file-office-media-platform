"""html (Keuze 17): de tags weg met html.parser, de titel als meta."""

from __future__ import annotations

from tests.extract.lees import lees

PAGINA = """<!doctype html><html><head><meta charset="windows-1252"><title>De titel</title>
<style>p { color: red }</style><script>var geheim = "scriptwoord";</script></head>
<body><h1>Kop</h1><p>Een alinea met caf\xe9 &amp; &euro; en <b>vet</b>.</p>
<table><tr><td>cel1</td><td>cel2</td></tr></table><template>sjabloonwoord</template></body></html>
"""


def test_tags_weg_titel_als_meta_en_de_charset_van_de_pagina():
    found, text = lees(PAGINA.encode("cp1252"), "html")
    assert found.state == "done" and found.meta == "De titel"
    assert text == "Kop\nEen alinea met café & € en vet.\ncel1 cel2"
    assert "scriptwoord" not in text and "color" not in text and "sjabloonwoord" not in text


def test_utf8_zonder_charset():
    found, text = lees("<p>Straße</p><p>GROẞE</p>".encode(), "html")
    assert text == "Straße\nGROẞE"


def test_kapotte_html_geeft_wat_er_is():
    found, text = lees(b"<p>begin <b <<< <!-- nooit dicht", "html")
    assert found.state == "done" and text.startswith("begin")
