"""app/analyse/tokenizer.py (fase 6, X3a, Keuze 74): de tokenizer van SigLIP 2 met alleen de
standaardbibliotheek.

Een klein tokenizer.json in de vorm van Gemma (BPE, byte fallback, Lowercase en Replace(" ", "▁"),
<eos> erachter) wordt hier gemaakt. De verwachte stukken in TABEL zijn met de hand afgeleid uit de
regels van `tokenizers` (zie de toelichting per regel), niet met deze module. tokenizer.bin moet
precies dezelfde tokens geven als het JSON-bestand, ook op een fuzz met vaste seed, en een bestand
dat niet klopt geeft TokenizerFout bij het laden. De echte tokenizer.json (256.000 stukken) komt in
de job beeld-model (BZ3, tests_analyse/test_beeld_tokenizer_echt.py).
"""

from __future__ import annotations

import hashlib
import json
import random
import struct
import subprocess
import sys
import time
from array import array
from pathlib import Path

import pytest

from app.analyse import tokenizer as tok
from app.analyse.tokenizer import GEEN, LENGTE, MAX_CHARS, Tokenizer, TokenizerFout

# Merges op rang (0 eerst). (y, z) staat vóór (x, y): "xyz" wordt x | yz.
MERGES = [
    ("o", "n"),
    ("on", "d"),
    ("▁", "o"),
    ("▁o", "p"),
    ("▁", "h"),
    ("e", "t"),
    ("▁h", "et"),
    ("▁", "s"),
    ("t", "r"),
    ("▁s", "tr"),
    ("a", "n"),
    ("an", "d"),
    ("▁str", "and"),
    ("a", "a"),
    ("y", "z"),
    ("x", "y"),
    ("k", "a"),
]
GRIEKS = "σοφιαδ"  # wel σ, geen ς: een slot-sigma die ς werd, viel terug op bytes


def _vocab(*, bytes_ook: bool = True) -> dict[str, int]:
    vocab = {"<pad>": 0, "<eos>": 1, "<bos>": 2, "<unk>": 3}
    if bytes_ook:
        for b in range(256):
            vocab[f"<0x{b:02X}>"] = len(vocab)
    for c in "abcdefghijklmnopqrstuvwxyz▁" + GRIEKS:
        vocab.setdefault(c, len(vocab))
    for a, b in MERGES:
        vocab.setdefault(a + b, len(vocab))
    vocab.setdefault("kat", len(vocab))  # een toegevoegd token op de genormaliseerde tekst
    return vocab


def _toegevoegd(i: int, inhoud: str, *, normalized: bool = False, special: bool = True) -> dict:
    return {
        "id": i,
        "content": inhoud,
        "single_word": False,
        "lstrip": False,
        "rstrip": False,
        "normalized": normalized,
        "special": special,
    }


def _doc(**anders) -> dict:
    """Een tokenizer.json in de vorm die `tokenizers` schrijft voor Gemma met de standaard van
    SigLIP 2 (Lowercase vóór Replace, Split op " " die daarna niets meer doet, A <eos>)."""
    vocab = anders.pop("vocab", None) or _vocab()
    model = {
        "type": "BPE",
        "dropout": None,
        "unk_token": "<unk>",
        "continuing_subword_prefix": None,
        "end_of_word_suffix": None,
        "fuse_unk": True,
        "byte_fallback": True,
        "ignore_merges": False,
        "vocab": vocab,
        "merges": [[a, b] for a, b in MERGES],
    }
    model.update(anders.pop("model", {}))
    doc = {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [
            _toegevoegd(0, "<pad>"),
            _toegevoegd(1, "<eos>"),
            _toegevoegd(2, "<bos>"),
            _toegevoegd(3, "<unk>"),
            _toegevoegd(vocab["kat"], "kat", normalized=True, special=False),
        ],
        "normalizer": {
            "type": "Sequence",
            "normalizers": [
                {"type": "Lowercase"},
                {"type": "Replace", "pattern": {"String": " "}, "content": "▁"},
            ],
        },
        "pre_tokenizer": {
            "type": "Split",
            "pattern": {"String": " "},
            "behavior": "MergedWithPrevious",
            "invert": False,
        },
        "post_processor": {
            "type": "TemplateProcessing",
            "single": [
                {"Sequence": {"id": "A", "type_id": 0}},
                {"SpecialToken": {"id": "<eos>", "type_id": 0}},
            ],
            "pair": [],
            "special_tokens": {},
        },
        "decoder": None,
        "model": model,
    }
    doc.update(anders)
    return doc


def _json(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode()


@pytest.fixture(scope="module")
def t_json() -> Tokenizer:
    return Tokenizer.from_json(_json(_doc()))


@pytest.fixture(scope="module")
def t_bin(t_json: Tokenizer) -> Tokenizer:
    return Tokenizer.from_bin(t_json.to_bin())


def _namen(t: Tokenizer, ids: list[int]) -> list[str]:
    return [t.stuk(i) for i in ids]


# (tekst, verwachte stukken zonder <eos>/<pad>, waarom)
TABEL = [
    ("hond op het strand", ["h", "ond", "▁op", "▁het", "▁strand"], "B5: het voorbeeld"),
    ("Hond Op Het Strand", ["h", "ond", "▁op", "▁het", "▁strand"], "kleine letters"),
    ("HOND", ["h", "ond"], "kleine letters"),
    # per teken: Σ is altijd σ; "ΟΔΟΣ".lower() zou op het eind ς geven (niet in vocab: bytes)
    (
        "ΣΟΦΙΑ ΟΔΟΣ",
        ["σ", "ο", "φ", "ι", "α", "▁", "ο", "δ", "ο", "σ"],
        "kleine letters per teken (geen slot-sigma)",
    ),
    ("€", ["<0xE2>", "<0x82>", "<0xAC>"], "byte fallback: UTF-8 E2 82 AC"),
    ("a€b", ["a", "<0xE2>", "<0x82>", "<0xAC>", "b"], "byte fallback midden in een woord"),
    ("İ", ["i", "<0xCC>", "<0x87>"], "İ wordt per teken i + U+0307 (CC 87)"),
    ("aaa", ["aa", "a"], "gelijke rang: links eerst"),
    ("aaaa", ["aa", "aa"], "twee keer dezelfde merge"),
    ("xyz", ["x", "yz"], "lagere rang eerst: (y, z) vóór (x, y)"),
    ("xy", ["xy"], "(x, y) alleen"),
    ("hond<eos>strand", ["h", "ond", "<eos>", "s", "tr", "and"], "toegevoegd token, ruw"),
    ("<EOS>", ["<0x3C>", "e", "o", "s", "<0x3E>"], "<eos> ruw: geen hoofdletters"),
    ("<eos><eos>", ["<eos>", "<eos>"], "twee keer"),
    ("Kat", ["kat"], "kat: een toegevoegd token op de genormaliseerde tekst"),
    ("KAT op", ["kat", "▁op"], "kat, dan de rest"),
    ("ka", ["ka"], "geen kat: gewone merge"),
    (" ", ["▁"], "een spatie wordt ▁"),
    ("  hond", ["▁", "▁h", "ond"], "geen merge (▁, ▁); (▁, h) wel"),
    ("", [], "lege tekst: alleen <eos>"),
    ("a\ud800b", ["a", "<0xEF>", "<0xBF>", "<0xBD>", "b"], "losse surrogaat wordt U+FFFD"),
    ("\t", ["<0x09>"], "tab: byte fallback"),
    ("😀", ["<0xF0>", "<0x9F>", "<0x98>", "<0x80>"], "4 bytes"),
]


@pytest.mark.parametrize("tekst,verwacht,waarom", TABEL, ids=[w for _, _, w in TABEL])
def test_de_tabel(t_json, t_bin, tekst, verwacht, waarom):
    for t in (t_json, t_bin):
        ids = t.encode(tekst)
        assert len(ids) == LENGTE
        k = len(verwacht)
        assert _namen(t, ids[:k]) == verwacht, waarom
        assert ids[k] == t.eos == 1
        assert ids[k + 1 :] == [t.pad] * (LENGTE - k - 1)
        assert t.pad == 0


def test_hond_op_het_strand_als_ids(t_json):
    v = _vocab()
    ids = t_json.encode("hond op het strand")
    assert ids[:6] == [v["h"], v["ond"], v["▁op"], v["▁het"], v["▁strand"], 1]
    assert ids[6:] == [0] * 58


def test_per_teken_en_niet_str_lower():
    """De reden van de regel: str.lower() op de hele tekst maakt van de laatste Σ een ς."""
    assert "ΟΔΟΣ".lower().endswith("ς")
    assert "".join(map(str.lower, "ΟΔΟΣ")) == "οδοσ"


@pytest.mark.parametrize(
    "n_a,stukken",
    [
        (124, 62),  # 62 x aa, <eos>, één <pad>
        (126, 63),  # precies 63 stukken: <eos> op plek 63, geen <pad>
        (127, 63),  # 63 x aa en een a: afgekapt op 63
        (128, 63),
        (200, 63),
        (5000, 63),
    ],
)
def test_afkappen_op_63_en_eos(t_json, t_bin, n_a, stukken):
    for t in (t_json, t_bin):
        ids = t.encode("a" * n_a)
        v = _vocab()
        assert ids[:stukken] == [v["aa"]] * stukken
        assert ids[stukken] == 1
        assert ids[stukken + 1 :] == [0] * (LENGTE - stukken - 1)


def test_eos_als_64e_token_bij_veel_toegevoegde_tokens(t_json):
    ids = t_json.encode("<eos>" * 100)
    assert ids == [1] * 64


def test_max_chars(t_json):
    assert len(t_json.stukken("a" * (MAX_CHARS + 3000))) == MAX_CHARS // 2
    assert len(t_json.stukken("b" * 5000)) == MAX_CHARS


def test_stukken_met_genoeg_verandert_het_begin_niet(t_json):
    tekst = "hond op het strand " * 30
    alles = t_json.stukken(tekst)
    assert len(alles) > 63
    assert t_json.stukken(tekst, genoeg=63)[:63] == alles[:63]
    gedeeld = "hond<eos>" * 40
    assert t_json.stukken(gedeeld, genoeg=63)[:63] == t_json.stukken(gedeeld)[:63]


def test_unk_samen_zonder_byte_fallback():
    vocab = _vocab(bytes_ook=False)
    for fuse, verwacht in ((True, ["a", "<unk>", "b"]), (False, ["a", "<unk>", "<unk>", "b"])):
        doc = _doc(vocab=vocab, model={"byte_fallback": False, "fuse_unk": fuse})
        t = Tokenizer.from_json(_json(doc))
        assert _namen(t, t.stukken("a€€b")) == verwacht
        assert Tokenizer.from_bin(t.to_bin()).encode("a€€b") == t.encode("a€€b")


def test_byte_fallback_met_ontbrekende_byte_wordt_unk():
    vocab = _vocab()
    del vocab["<0xAC>"]
    # de ids moeten 0..n-1 blijven: schuif de laatste op de vrije plek
    weg = len(vocab)
    vrij = sorted(set(range(weg + 1)) - set(vocab.values()))[0]
    laatste = next(k for k, i in vocab.items() if i == weg)
    vocab[laatste] = vrij
    doc = _doc(vocab=vocab)
    doc["added_tokens"][-1]["id"] = vocab["kat"]
    t = Tokenizer.from_json(_json(doc))
    assert _namen(t, t.stukken("€a")) == ["<unk>", "a"]


def test_zonder_unk_valt_het_teken_weg():
    doc = _doc(vocab=_vocab(bytes_ook=False), model={"byte_fallback": False, "unk_token": None})
    t = Tokenizer.from_json(_json(doc))
    assert t.unk == GEEN
    assert _namen(t, t.stukken("a€b")) == ["a", "b"]


def test_zonder_lowercase_in_het_bestand_toch_kleine_letters():
    doc = _doc(normalizer={"type": "Replace", "pattern": {"String": " "}, "content": "▁"})
    t = Tokenizer.from_json(_json(doc))
    assert _namen(t, t.stukken("Hond Op")) == ["h", "ond", "▁op"]
    t2 = Tokenizer.from_json(_json(doc), kleine_letters=False)
    assert _namen(t2, t2.stukken("Hond"))[0] == "<0x48>"


def test_prepend_en_geen_pre_tokenizer():
    doc = _doc(
        normalizer={
            "type": "Sequence",
            "normalizers": [
                {"type": "Prepend", "prepend": "▁"},
                {"type": "Replace", "pattern": {"String": " "}, "content": "▁"},
            ],
        },
        pre_tokenizer=None,
    )
    t = Tokenizer.from_json(_json(doc))
    assert _namen(t, t.stukken("op het")) == ["▁op", "▁het"]
    assert t.stukken("") == []


def test_merges_als_tekst_en_dubbel_paar():
    """Oudere bestanden hebben merges als "a b"; een paar dat twee keer staat: de laatste telt."""
    doc = _doc(model={"merges": [f"{a} {b}" for a, b in MERGES]})
    t = Tokenizer.from_json(_json(doc))
    assert _namen(t, t.stukken("hond op")) == ["h", "ond", "▁op"]
    # (y, z) nog een keer, nu na (x, y): dan wint (x, y) en wordt "xyz" xy | z
    doc = _doc(model={"merges": [[a, b] for a, b in MERGES] + [["y", "z"]]})
    t = Tokenizer.from_json(_json(doc))
    assert _namen(t, t.stukken("xyz")) == ["xy", "z"]


# --- tokenizer.bin -----------------------------------------------------------------------------


def test_bin_is_bepaald_en_herhaalbaar(t_json):
    b = t_json.to_bin()
    assert Tokenizer.from_json(_json(_doc())).to_bin() == b
    assert Tokenizer.from_bin(b).to_bin() == b
    assert b[:8] == tok.MAGIC
    assert hashlib.sha256(b[:-32]).digest() == b[-32:]


def test_load_van_een_bestand(tmp_path, t_json):
    pad = tmp_path / "tokenizer.bin"
    pad.write_bytes(t_json.to_bin())
    t = Tokenizer.load(pad)
    assert t.encode("hond op het strand") == t_json.encode("hond op het strand")
    assert len(t) == len(t_json)
    assert [x.inhoud for x in t.toegevoegd] == ["<pad>", "<eos>", "<bos>", "<unk>", "kat"]


ALFABET = (
    list("abcdehknoprstxyzABCHKNOSXYZ ")
    + list("ΣΟΔΦσςİß€😀\t\n▁<>")
    + ["<eos>", "<EOS>", "kat", "KAT", "\ud800", "\udfff", "\x00", "̇", "\U0010fffd"]
)


def test_fuzz_bin_gelijk_aan_json_en_nooit_een_fout(t_json, t_bin):
    rnd = random.Random(20261112)
    for _ in range(3000):
        n = rnd.choice((0, 1, 2, 5, 20, 80, 300))
        tekst = "".join(rnd.choice(ALFABET) for _ in range(n))
        ids = t_json.encode(tekst)
        assert t_bin.encode(tekst) == ids
        assert len(ids) == LENGTE
        assert all(0 <= i < len(t_json) for i in ids)
        k = ids.index(t_json.eos) if t_json.eos in ids else -1
        # na de laatste echte <eos> alleen <pad>; ervoor nooit <pad>
        laatste = max(i for i, x in enumerate(ids) if x == t_json.eos)
        assert ids[laatste + 1 :] == [t_json.pad] * (LENGTE - laatste - 1)
        assert t_json.pad not in ids[:laatste]
        assert k >= 0


def test_fuzz_willekeurige_codepunten(t_json, t_bin):
    rnd = random.Random(7)
    for _ in range(500):
        tekst = "".join(chr(rnd.randrange(0x110000)) for _ in range(rnd.randrange(0, 40)))
        assert t_bin.encode(tekst) == t_json.encode(tekst)


def test_geen_kwadratische_tijd(t_json):
    """De zwaarste vragen: één lang stuk zonder spatie, alles één merge, alles bytes."""
    for tekst in ("a" * 10_000, "xy" * 5000, "€" * 10_000, "<eos>" * 3000, "kat" * 4000):
        t0 = time.perf_counter()
        t_json.encode(tekst)
        assert time.perf_counter() - t0 < 1.0, tekst[:10]


def test_geen_tekst_in_een_fout_en_niets_op_stderr(t_json, capsys):
    t_json.encode("geheimwoord7")
    uit = capsys.readouterr()
    assert uit.out == uit.err == ""


# --- weigeren ------------------------------------------------------------------------------------


def _genest(diep: int, binnen: dict | None = None) -> dict:
    """binnen (standaard Lowercase) in diep Sequences (geldige JSON; json.loads neemt 1200 nog
    aan)."""
    n: dict = binnen or {"type": "Lowercase"}
    for _ in range(diep):
        n = {"type": "Sequence", "normalizers": [n]}
    return n


def test_een_sequence_in_een_sequence_tot_de_grens_kan(t_json):
    """De normalizer van SigLIP 2 (zelf een Sequence) in nog MAX_NESTING - 1 Sequences."""
    diep = _genest(tok.MAX_NESTING - 1, _doc()["normalizer"])
    t = Tokenizer.from_json(_met(normalizer=diep))
    assert t.encode("Hond op het strand") == t_json.encode("Hond op het strand")


def _met(**veld):
    return _json(_doc(**veld))


def _model(**veld):
    return _json(_doc(model=veld))


@pytest.mark.parametrize(
    "bron,fout",
    [
        (b"{", "geen geldige JSON"),
        (b"\xff", "geen geldige JSON"),
        (b"[]", "geen object"),
        (b'{"model": {"type": "BPE"}, "model": {}}', "geen geldige JSON"),
        (_model(type="Unigram"), "niet BPE"),
        (_model(dropout=0.1), "dropout"),
        (_model(continuing_subword_prefix="##"), "continuing_subword_prefix"),
        (_model(end_of_word_suffix="</w>"), "end_of_word_suffix"),
        (_model(ignore_merges=True), "ignore_merges"),
        (_model(vocab={}), "vocab ontbreekt"),
        (_model(vocab={"<pad>": 0, "<eos>": 5}), "buiten"),
        (_model(vocab={"<pad>": 0, "<eos>": True}), "buiten"),
        (_model(vocab={"<pad>": 0, "<eos>": 1, "a": 2}, merges=[], unk_token="<unk>"), "unk_token"),
        (_model(vocab={"<pad>": 0, "x": 1}, merges=[], unk_token=None), "<eos>"),
        (_model(merges=[["a"]]), "geen paar"),
        (_model(merges=["a b c"]), "geen paar"),
        (_model(merges=[["a", "q"]]), "buiten vocab"),  # "aq" staat niet in vocab
        (_model(merges=[["a", "♥"]]), "buiten vocab"),
        (_model(merges=None), "merges ontbreekt"),
        (_met(normalizer={"type": "NFKC"}), "normalizer 'NFKC'"),
        (_met(normalizer={"type": "Sequence", "normalizers": [{"type": "Strip"}]}), "Strip"),
        (_met(normalizer={"type": "Replace", "pattern": {"Regex": " "}, "content": "▁"}), "Repl"),
        (_met(normalizer={"type": "Prepend", "prepend": "_"}), "Prepend"),
        (_met(pre_tokenizer={"type": "Metaspace"}), "pre_tokenizer 'Metaspace'"),
        (
            _met(
                normalizer={"type": "Lowercase"},
                pre_tokenizer={"type": "Split", "pattern": {"String": " "}, "invert": False},
            ),
            "pre_tokenizer 'Split'",
        ),
        (
            _met(pre_tokenizer={"type": "Split", "pattern": {"String": " "}, "invert": True}),
            "pre_tokenizer 'Split'",
        ),
        (_met(post_processor={"type": "ByteLevel"}), "post_processor 'ByteLevel'"),
        (
            _met(
                post_processor={
                    "type": "TemplateProcessing",
                    "single": [
                        {"SpecialToken": {"id": "<bos>", "type_id": 0}},
                        {"Sequence": {"id": "A", "type_id": 0}},
                    ],
                }
            ),
            "verwacht A <eos>",
        ),
        (_met(truncation={"direction": "Left", "max_length": 64}), "truncation links"),
        (_met(padding={"direction": "Left"}), "padding links"),
        (_met(padding=[]), "padding is geen object"),
        (_met(added_tokens=[{"id": 0, "content": "<eos>"}]), "past niet bij vocab"),
        (_met(added_tokens=[{"id": 999, "content": "<eos>"}]), "past niet bij vocab"),
        (_met(added_tokens=[_toegevoegd(1, "<eos>") | {"lstrip": True}]), "lstrip"),
        (_met(added_tokens=[_toegevoegd(1, "<eos>") | {"single_word": True}]), "single_word"),
        (_met(added_tokens=[_toegevoegd(1, "<eos>"), _toegevoegd(1, "<eos>")]), "twee keer"),
        (_met(added_tokens=["<eos>"]), "geen object"),
        # De review van golf 5 (veilig F4): diep genest gaf RecursionError, een getal als
        # added_tokens TypeError; beide horen TokenizerFout
        (_met(normalizer=_genest(tok.MAX_NESTING + 1)), "diep genest"),
        (_met(normalizer=_genest(1200)), "diep genest"),
        (_met(added_tokens=5), "added_tokens is geen lijst"),
        (_met(added_tokens=True), "added_tokens is geen lijst"),
    ],
    ids=lambda x: x[:40].decode(errors="replace") if isinstance(x, bytes) else None,
)
def test_json_die_niet_kan(bron, fout):
    with pytest.raises(TokenizerFout, match=fout):
        Tokenizer.from_json(bron)


def test_json_met_een_dubbele_id():
    vocab = _vocab()
    vocab["extra"] = vocab["a"]
    with pytest.raises(TokenizerFout, match="buiten|twee keer"):
        Tokenizer.from_json(_json(_doc(vocab=vocab)))


def test_json_te_groot(tmp_path, monkeypatch):
    monkeypatch.setattr(tok, "MAX_JSON", 100)
    pad = tmp_path / "tokenizer.json"
    pad.write_bytes(_json(_doc()))
    with pytest.raises(TokenizerFout, match="groter"):
        Tokenizer.from_json(pad)


def _teken(inhoud: bytes) -> bytes:
    """Een tokenizer.bin met een geldige sha256: de fout zit in de inhoud, niet in de som."""
    return inhoud + hashlib.sha256(inhoud).digest()


def _kop(b: bytes) -> list:
    return list(tok._KOP.unpack_from(b))


def _met_kop(b: bytes, **veld) -> bytes:
    namen = ["magic", "versie", "vlaggen", "n", "n_m", "n_t", "n_a", "pad", "eos", "unk", "lengte"]
    namen.append("blok")
    kop = _kop(b)
    for k, v in veld.items():
        kop[namen.index(k)] = v
    return _teken(tok._KOP.pack(*kop) + b[tok._KOP.size : -32])


def _plek(t: Tokenizer, deel: str) -> int:
    """De offset in tokenizer.bin van een tabel."""
    n, n_m, n_t = len(t), len(t._m_sleutels), len(t._t_punten)
    pos = tok._KOP.size
    for naam, maat in (
        ("offsets", 4 * (n + 1)),
        ("blok", len(t._blok)),
        ("sleutels", 8 * n_m),
        ("waarden", 8 * n_m),
        ("punten", 4 * n_t),
        ("tekens", 4 * n_t),
        ("bytes", 4 * 256),
        ("toegevoegd", 0),
    ):
        if naam == deel:
            return pos
        pos += maat
    raise KeyError(deel)


def _zet(b: bytes, plek: int, fmt: str, waarde: int) -> bytes:
    inhoud = bytearray(b[:-32])
    struct.pack_into(fmt, inhoud, plek, waarde)
    return _teken(bytes(inhoud))


def test_bin_die_niet_kan(t_json):
    b = t_json.to_bin()
    t = t_json
    gevallen = [
        (b"", "te kort"),
        (b[:-1], "sha256"),
        (b[:-33] + b[-32:], "sha256"),
        (b[:100] + bytes([b[100] ^ 1]) + b[101:], "sha256"),
        (_teken(b[:-32] + b"\x00"), "bytes, de kop zegt"),
        (_teken(b[: -32 - 4]), "bytes, de kop zegt"),
        (_met_kop(b, magic=b"PLTOKBIX"), "versie 1"),
        (_met_kop(b, versie=2), "versie 1"),
        (_met_kop(b, vlaggen=64), "onbekende vlaggen"),
        (_met_kop(b, n=0), "aantallen"),
        (_met_kop(b, n_t=len(t) + 1), "aantallen"),
        (_met_kop(b, pad=len(t)), "<pad>"),
        (_met_kop(b, eos=len(t) + 7), "<eos>"),
        (_met_kop(b, unk=len(t)), "<unk>"),
        (_met_kop(b, lengte=1), "lengte"),
        (_zet(b, _plek(t, "offsets"), "<I", 1), "offsets"),
        (_zet(b, _plek(t, "offsets") + 8, "<I", 0), "lopen terug"),
        (_zet(b, _plek(t, "offsets") + 4 * len(t), "<I", 5), "offsets"),
        (_zet(b, _plek(t, "blok") + 4, "<B", 0xFF), "geen UTF-8"),
        (_zet(b, _plek(t, "sleutels"), "<Q", (1 << 63)), "merges"),
        (_zet(b, _plek(t, "sleutels") + 8, "<Q", 0), "merges"),
        (_zet(b, _plek(t, "waarden"), "<Q", len(t)), "merges"),
        (_zet(b, _plek(t, "punten"), "<I", 0x110000), "tekens"),
        (_zet(b, _plek(t, "tekens"), "<I", len(t)), "tekens"),
        (_zet(b, _plek(t, "bytes"), "<I", len(t)), "byte"),
        (_zet(b, len(b) - 32 - 8, "<I", len(t)), "toegevoegd token"),
        (_zet(b, len(b) - 32 - 4, "<I", 2), "toegevoegd token"),
        (_zet(b, len(b) - 32 - 8, "<I", 1), "twee keer"),  # "kat" wordt nog een <eos>
    ]
    for data, fout in gevallen:
        with pytest.raises(TokenizerFout, match=fout):
            Tokenizer.from_bin(data)


def test_bin_een_stuk_midden_in_een_teken(t_json):
    """Een offset die midden in een teken van twee bytes valt (▁ is E2 96 81)."""
    b = t_json.to_bin()
    i = _vocab()["▁"]
    plek = tok._KOP.size + 4 * (i + 1)
    begin = struct.unpack_from("<I", b, tok._KOP.size + 4 * i)[0]
    with pytest.raises(TokenizerFout, match="midden in een teken|geen UTF-8"):
        Tokenizer.from_bin(_zet(b, plek, "<I", begin + 1))


def test_bin_te_groot(monkeypatch, t_json):
    monkeypatch.setattr(tok, "MAX_BIN", 1000)
    with pytest.raises(TokenizerFout, match="hooguit"):
        Tokenizer.from_bin(t_json.to_bin())


# --- BZ7: laden van een tokenizer.bin op ware grootte --------------------------------------------

N_STUKKEN = 256_000  # de woordenlijst van Gemma
N_MERGES = 600_000  # meer dan Gemma; ruim genoeg voor de grens


@pytest.fixture(scope="module")
def groot_bin() -> bytes:
    """Een tokenizer.bin met 256.000 stukken en 600.000 merges, direct in arrays gemaakt (het
    JSON-pad zou hier zelf de tijd en het geheugen zijn)."""
    rnd = random.Random(74)
    stukken = ["<pad>", "<eos>", "<bos>", "<unk>"] + [f"<0x{b:02X}>" for b in range(256)]
    stukken += [chr(c) for c in range(0x61, 0x7B)] + ["▁"]
    while len(stukken) < N_STUKKEN:
        stukken.append(f"▁w{len(stukken)}é")
    blok = bytearray()
    offsets = array("I", [0])
    for s in stukken:
        blok += s.encode()
        offsets.append(len(blok))
    sleutels = sorted(
        {(rnd.randrange(N_STUKKEN) << 32) | rnd.randrange(N_STUKKEN) for _ in range(N_MERGES)}
    )
    waarden = array("Q", ((r << 32) | rnd.randrange(N_STUKKEN) for r in range(len(sleutels))))
    tekens = sorted((ord(s), i) for i, s in enumerate(stukken) if len(s) == 1)
    t = Tokenizer(
        blok=bytes(blok),
        offsets=offsets,
        sleutels=array("Q", sleutels),
        waarden=waarden,
        punten=array("I", (p for p, _ in tekens)),
        tekens=array("I", (i for _, i in tekens)),
        bytes_ids=array("I", range(4, 260)),
        toegevoegd=[(0, False), (1, False)],
        vlaggen=tok.KLEINE_LETTERS | tok.SPATIE_WORDT_STREEP | tok.BYTE_FALLBACK,
        pad=0,
        eos=1,
        unk=3,
    )
    return t.to_bin()


_MEET_RSS = """
import resource, sys
sys.path.insert(0, sys.argv[1])
from app.analyse.tokenizer import Tokenizer
voor = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t = Tokenizer.load(sys.argv[2])
na = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
assert len(t.encode("hond op het strand")) == 64
print(na - voor)
"""


def test_bz7_laden_onder_een_halve_seconde_en_100_mb(groot_bin, tmp_path):
    """BZ7 (Keuze 74): tokenizer.bin laden ≤ 0,5 s en ≤ 100 MB (de JSON kost 1,35 s en 245 MB).
    De tijd is de snelste van drie keer; het geheugen de groei van maxrss in een vers proces,
    van vóór het lezen van het bestand tot na het laden."""
    assert len(groot_bin) > 10_000_000
    tijden = []
    for _ in range(3):
        t0 = time.perf_counter()
        t = Tokenizer.from_bin(groot_bin)
        tijden.append(time.perf_counter() - t0)
    assert min(tijden) <= 0.5, tijden
    assert len(t) == N_STUKKEN
    ids = t.encode("hond op het strand")
    assert len(ids) == LENGTE and 1 in ids

    pad = tmp_path / "tokenizer.bin"
    pad.write_bytes(groot_bin)
    api = str(Path(__file__).resolve().parents[2])
    uit = subprocess.run(
        [sys.executable, "-I", "-c", _MEET_RSS, api, str(pad)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    groei_kib = int(uit.stdout.strip())
    assert groei_kib <= 100 * 1024, groei_kib
