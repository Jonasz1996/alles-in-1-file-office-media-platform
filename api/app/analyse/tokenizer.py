"""De tokenizer van SigLIP 2 (fase 6, Keuze 74): eigen code, alleen de standaardbibliotheek.

SentencePiece-BPE met byte fallback, zoals de Rust-bibliotheek `tokenizers` het tokenizer.json van
SigLIP 2 (de woordenlijst van Gemma, 256.000 stukken) uitvoert, met de standaard van SigLIP 2
erbij: kleine letters. Een vraag wordt zo 64 ids:

1. **Toegevoegde tokens** (`added_tokens`, zoals `<eos>`) eerst: links het langste, in de ruwe
   tekst voor een token met `normalized` onwaar en in de genormaliseerde tekst voor de rest.
2. **Normaliseren** per stuk tekst daartussen: kleine letters **per teken** (niet `str.lower()`
   op de hele tekst: dat maakt van een slot-sigma een ς, de bibliotheek een σ; B5), dan elke
   spatie een `▁` (en `▁` ervoor als het bestand dat vraagt).
3. **BPE** per stuk: elk teken is een stuk uit de woordenlijst, of anders `<0xXX>` per byte van
   zijn UTF-8 (byte fallback), of `<unk>`. Dan samenvoegen op rang, bij gelijke rang de meest
   linkse: een heap over een gelinkte lijst.
4. **Afkappen** op 63, dan `<eos>`, dan `<pad>` tot 64 (rechts).

"hond op het strand" wordt `h | ond | ▁op | ▁het | ▁strand | <eos>` (B5).

**tokenizer.bin.** Het JSON-bestand laden kost 1,35 s en 245 MB (B5). De omzetter
(app/analyse/worker/omzetten/siglip.py) schrijft daarom `tokenizer.bin`, dat de worker laadt:
de stukken als één blok UTF-8 met offsets, de merges als paren van ids met hun rang (gesorteerd op
het paar, om te zoeken met bisect), de tekens die zelf een stuk zijn, de ids van de 256 bytes en de
toegevoegde tokens. Laden is een paar keer `array.frombytes`; er komt geen dict van 256.000
stukken. `Tokenizer.from_json` geeft precies dezelfde tokens (test_tokenizer.py).

**Nooit een fout op een vraag.** encode() neemt elke str: losse surrogaten worden U+FFFD, en
alleen de eerste MAX_CHARS tekens tellen (de container neemt er hooguit 200). Een bestand dat niet
klopt, geeft TokenizerFout bij het laden, nooit later.

De worker laadt hem in het kind `zoeken`; de app nooit. Een vraag is zoektekst: deze module logt
niets en zet de tekst nooit in een fout.
"""

from __future__ import annotations

import bisect
import hashlib
import heapq
import json
import operator
import re
import struct
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

LENGTE = 64  # tokens per vraag (text_config.max_position_embeddings van SigLIP 2)
MAX_CHARS = 2000  # meer tekens van een vraag tellen niet (Keuze 75: de container neemt er ≤ 200)
SPATIE = "▁"  # U+2581, de spatie van SentencePiece

MAGIC = b"PLTOKBIN"
VERSIE = 1
GEEN = 0xFFFFFFFF  # geen id (bijvoorbeeld geen <unk>)
_LAAG = 0xFFFFFFFF  # de onderste 32 bits van een sleutel of waarde
MAX_STUKKEN = 1 << 24  # hooguit zoveel stukken: ids passen in 32 bits, een paar in 64
MAX_BIN = 512 * 1024 * 1024  # een tokenizer.bin is ±12 MB; dit is de harde grens bij het laden
MAX_JSON = 256 * 1024 * 1024  # tokenizer.json van SigLIP 2 is 34,4 MB
MAX_NESTING = 4  # een Sequence van normalizers in een Sequence; SigLIP 2 heeft er één

# Vlaggen in de kop van tokenizer.bin
KLEINE_LETTERS = 1
SPATIE_WORDT_STREEP = 2  # normalizer Replace(" ", "▁")
STREEP_ERVOOR = 4  # normalizer Prepend("▁")
BYTE_FALLBACK = 8
UNK_SAMEN = 16  # fuse_unk: <unk> na <unk> wordt één <unk>
_VLAGGEN = KLEINE_LETTERS | SPATIE_WORDT_STREEP | STREEP_ERVOOR | BYTE_FALLBACK | UNK_SAMEN

# magic, versie, vlaggen, stukken, merges, tekens, toegevoegd, pad, eos, unk, lengte, blok
_KOP = struct.Struct("<8s11I")


# Een regex voor de toegevoegde tokens, en van de gevonden tekst naar de id
_Patroon = tuple[re.Pattern[str], dict[str, int]]


class TokenizerFout(ValueError):
    """Een tokenizer.json of tokenizer.bin die niet klopt (nooit bij het coderen van een vraag)."""


@dataclass(frozen=True)
class Toegevoegd:
    """Een toegevoegd token: de tekst, de id, en of hij op de genormaliseerde tekst past."""

    inhoud: str
    id: int
    genormaliseerd: bool


def _byte_stuk(b: int) -> str:
    return f"<0x{b:02X}>"


def _schoon(tekst: str) -> str:
    """Losse surrogaten (kan in een str van Python, niet in UTF-8) worden U+FFFD."""
    try:
        tekst.encode("utf-8")
    except UnicodeEncodeError:
        tekst = tekst.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return tekst


class Tokenizer:
    """Een geladen tokenizer. Maak hem met from_json (de omzetter, tests) of load/from_bin (de
    worker); to_bin geeft de bytes van tokenizer.bin."""

    def __init__(
        self,
        *,
        blok: bytes,
        offsets: array,
        sleutels: array,
        waarden: array,
        punten: array,
        tekens: array,
        bytes_ids: array,
        toegevoegd: list[tuple[int, bool]],
        vlaggen: int,
        pad: int,
        eos: int,
        unk: int,
        lengte: int = LENGTE,
    ) -> None:
        """De vorm van tokenizer.bin, in arrays: de stukken als één blok UTF-8 met offsets (id k
        is blok[offsets[k]:offsets[k + 1]]); de merges als sleutel links << 32 | rechts
        (oplopend, voor bisect) met waarde rang << 32 | uitkomst; de tekens die een stuk zijn als
        codepunt (oplopend) met hun id; de id van elke byte (GEEN als die er niet is). Alles wordt
        hier nagekeken, met lussen zonder Python-objecten per stuk: laden blijft snel en klein."""
        n = len(offsets) - 1
        if not 0 < n <= MAX_STUKKEN:
            raise TokenizerFout(f"{n} stukken; verwacht 1 tot {MAX_STUKKEN}")
        if vlaggen & ~_VLAGGEN:
            raise TokenizerFout(f"onbekende vlaggen {vlaggen:#x}")
        if not 2 <= lengte <= 4096:
            raise TokenizerFout(f"lengte {lengte}; verwacht 2 tot 4096")
        for naam, i in (("<pad>", pad), ("<eos>", eos), ("<unk>", unk)):
            if not (0 <= i < n or (naam == "<unk>" and i == GEEN)):
                raise TokenizerFout(f"{naam}: id {i} buiten de woordenlijst ({n})")
        if offsets[0] != 0 or offsets[-1] != len(blok):
            raise TokenizerFout("de offsets van de stukken passen niet bij het blok")
        if not all(map(operator.le, offsets, islice(offsets, 1, None))):
            raise TokenizerFout("de offsets van de stukken lopen terug")
        try:
            blok.decode("utf-8")
        except UnicodeDecodeError:
            raise TokenizerFout("de stukken zijn geen UTF-8") from None
        if any(blok[o] & 0xC0 == 0x80 for o in islice(offsets, 0, n) if o < len(blok)):
            raise TokenizerFout("een stuk begint midden in een teken")
        if len(sleutels) != len(waarden) or len(punten) != len(tekens) or len(bytes_ids) != 256:
            raise TokenizerFout("de lengtes van de tabellen kloppen niet")
        vorige = -1
        for k, w in zip(sleutels, waarden, strict=True):
            if k <= vorige or k >> 32 >= n or k & _LAAG >= n or w & _LAAG >= n:
                raise TokenizerFout("de merges zijn niet oplopend of hebben een id buiten de lijst")
            vorige = k
        vorige = -1
        for p, i in zip(punten, tekens, strict=True):
            if p <= vorige or p > 0x10FFFF or i >= n:
                raise TokenizerFout("de tekens zijn niet oplopend of hebben een id buiten de lijst")
            vorige = p
        if any(i != GEEN and i >= n for i in bytes_ids):
            raise TokenizerFout("een byte heeft een id buiten de lijst")
        self.pad, self.eos, self.unk = pad, eos, unk
        self.vlaggen = vlaggen
        self.lengte = lengte
        self._blok, self._offsets = blok, offsets
        self._m_sleutels, self._m_waarden = sleutels, waarden
        self._t_punten, self._t_ids = punten, tekens
        self._bytes = bytes_ids
        gezien: set[int] = set()
        self.toegevoegd: list[Toegevoegd] = []
        for i, norm in toegevoegd:
            if not 0 <= i < n or norm not in (False, True):
                raise TokenizerFout(f"toegevoegd token {i} klopt niet")
            if i in gezien:
                raise TokenizerFout(f"toegevoegd token {i} staat er twee keer in")
            gezien.add(i)
            self.toegevoegd.append(Toegevoegd(self.stuk(i), i, bool(norm)))
        self._ruw = self._patroon(t for t in self.toegevoegd if not t.genormaliseerd)
        self._norm = self._patroon(t for t in self.toegevoegd if t.genormaliseerd)

    def _patroon(self, tokens: Iterator[Toegevoegd]) -> _Patroon | None:
        """Links het langste: re probeert de alternatieven op volgorde, dus langste eerst. Een token
        dat op de genormaliseerde tekst past, past met zijn eigen genormaliseerde vorm (zoals
        `tokenizers` dat doet)."""
        naar: dict[str, int] = {}
        for t in tokens:
            sleutel = self._normaliseer(t.inhoud) if t.genormaliseerd else t.inhoud
            if sleutel:
                naar.setdefault(sleutel, t.id)
        if not naar:
            return None
        alles = sorted(naar, key=lambda s: (-len(s), s))
        return re.compile("|".join(map(re.escape, alles))), naar

    # --- de woordenlijst ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._offsets) - 1

    def stuk(self, i: int) -> str:
        """Het stuk met deze id (voor de toegevoegde tokens, tests en het verslag)."""
        if not 0 <= i < len(self):
            raise IndexError(i)
        return self._blok[self._offsets[i] : self._offsets[i + 1]].decode("utf-8")

    def _teken(self, c: str) -> int | None:
        punt = ord(c)
        j = bisect.bisect_left(self._t_punten, punt)
        if j < len(self._t_punten) and self._t_punten[j] == punt:
            return self._t_ids[j]
        return None

    def _merge(self, links: int, rechts: int) -> tuple[int, int] | None:
        sleutel = (links << 32) | rechts
        j = bisect.bisect_left(self._m_sleutels, sleutel)
        if j < len(self._m_sleutels) and self._m_sleutels[j] == sleutel:
            w = self._m_waarden[j]
            return w >> 32, w & _LAAG
        return None

    # --- coderen ---------------------------------------------------------------------------

    def _normaliseer(self, tekst: str) -> str:
        if self.vlaggen & KLEINE_LETTERS:
            tekst = "".join(map(str.lower, tekst))  # per teken: Σ wordt altijd σ
        if self.vlaggen & SPATIE_WORDT_STREEP:
            tekst = tekst.replace(" ", SPATIE)
        if self.vlaggen & STREEP_ERVOOR and tekst:
            tekst = SPATIE + tekst
        return tekst

    @staticmethod
    def _splits(tekst: str, patroon: _Patroon | None) -> Iterator[tuple[str, int | None]]:
        if patroon is None:
            if tekst:
                yield tekst, None
            return
        regex, naar = patroon
        begin = 0
        for m in regex.finditer(tekst):
            if m.start() > begin:
                yield tekst[begin : m.start()], None
            yield m.group(), naar[m.group()]
            begin = m.end()
        if begin < len(tekst):
            yield tekst[begin:], None

    def _bpe(self, woord: str) -> list[int]:
        """BPE van één stuk tekst, zoals Word::merge_all van `tokenizers`."""
        ids: list[int] = []
        vorige_unk = False
        for c in woord:
            i = self._teken(c)
            if i is not None:
                ids.append(i)
                vorige_unk = False
                continue
            if self.vlaggen & BYTE_FALLBACK:
                bs = [self._bytes[b] for b in c.encode("utf-8")]
                if GEEN not in bs:
                    ids.extend(bs)
                    vorige_unk = False
                    continue
            if self.unk == GEEN:
                continue  # zonder <unk> valt het teken weg, zoals in tokenizers zonder unk_token
            if not (vorige_unk and self.vlaggen & UNK_SAMEN):
                ids.append(self.unk)
            vorige_unk = True
        n = len(ids)
        if n < 2:
            return ids
        voor = list(range(-1, n - 1))
        na = list(range(1, n + 1))
        na[-1] = -1
        weg = [False] * n
        rij: list[tuple[int, int, int]] = []
        for p in range(n - 1):
            m = self._merge(ids[p], ids[p + 1])
            if m is not None:
                rij.append((m[0], p, m[1]))
        heapq.heapify(rij)
        while rij:
            rang, p, nieuw = heapq.heappop(rij)
            if weg[p] or na[p] == -1:
                continue
            q = na[p]
            m = self._merge(ids[p], ids[q])
            if m is None or m[1] != nieuw:
                continue  # een verlopen plek: het paar is intussen anders
            ids[p] = nieuw
            weg[q] = True
            na[p] = na[q]
            if na[q] != -1:
                voor[na[q]] = p
            if voor[p] != -1:
                m = self._merge(ids[voor[p]], nieuw)
                if m is not None:
                    heapq.heappush(rij, (m[0], voor[p], m[1]))
            if na[p] != -1:
                m = self._merge(nieuw, ids[na[p]])
                if m is not None:
                    heapq.heappush(rij, (m[0], p, m[1]))
        return [ids[p] for p in range(n) if not weg[p]]

    def stukken(self, tekst: str, genoeg: int | None = None) -> list[int]:
        """De ids van de tekst, zonder <eos>, afkappen of opvullen. Met genoeg stopt hij zodra
        er zoveel zijn (een volgend stuk tekst verandert de eerdere ids niet)."""
        tekst = _schoon(str(tekst))[:MAX_CHARS]
        uit: list[int] = []
        for ruw, token in self._splits(tekst, self._ruw):
            if token is not None:
                uit.append(token)
            else:
                for deel, token2 in self._splits(self._normaliseer(ruw), self._norm):
                    uit.extend([token2] if token2 is not None else self._bpe(deel))
                    if genoeg is not None and len(uit) >= genoeg:
                        return uit
            if genoeg is not None and len(uit) >= genoeg:
                return uit
        return uit

    def encode(self, tekst: str) -> list[int]:
        """Precies `lengte` ids: hooguit lengte - 1 stukken, <eos>, dan <pad>."""
        ids = self.stukken(tekst, genoeg=self.lengte - 1)[: self.lengte - 1]
        ids.append(self.eos)
        ids.extend([self.pad] * (self.lengte - len(ids)))
        return ids

    # --- tokenizer.bin ---------------------------------------------------------------------

    def to_bin(self) -> bytes:
        """De bytes van tokenizer.bin: de kop, de tabellen in de volgorde van __init__, en de
        sha256 van alles ervoor. Dezelfde tokenizer geeft altijd dezelfde bytes."""
        toeg = array("I")
        for t in self.toegevoegd:
            toeg.extend((t.id, 1 if t.genormaliseerd else 0))
        uit = bytearray(
            _KOP.pack(
                MAGIC,
                VERSIE,
                self.vlaggen,
                len(self),
                len(self._m_sleutels),
                len(self._t_punten),
                len(self.toegevoegd),
                self.pad,
                self.eos,
                self.unk,
                self.lengte,
                len(self._blok),
            )
        )
        uit += _le(self._offsets)
        uit += self._blok
        for a in (self._m_sleutels, self._m_waarden, self._t_punten, self._t_ids, self._bytes):
            uit += _le(a)
        uit += _le(toeg)
        uit += hashlib.sha256(uit).digest()
        return bytes(uit)

    @classmethod
    def from_bin(cls, data: bytes | bytearray | memoryview) -> Tokenizer:
        """Lees tokenizer.bin. Elke lengte, offset en id wordt nagekeken; de laatste 32 bytes
        zijn de sha256 van de rest."""
        data = memoryview(data).cast("B")
        if len(data) > MAX_BIN:
            raise TokenizerFout(f"tokenizer.bin is {len(data)} bytes; hooguit {MAX_BIN}")
        if len(data) < _KOP.size + 32:
            raise TokenizerFout("tokenizer.bin is te kort")
        inhoud, som = data[:-32], bytes(data[-32:])
        if hashlib.sha256(inhoud).digest() != som:
            raise TokenizerFout("tokenizer.bin: de sha256 aan het eind klopt niet")
        kop = _KOP.unpack_from(inhoud)
        magic, versie, vlaggen, n, n_m, n_t, n_a, pad, eos, unk, lengte, n_blok = kop
        if magic != MAGIC or versie != VERSIE:
            raise TokenizerFout("tokenizer.bin: geen tokenizer.bin van versie 1")
        if not 0 < n <= MAX_STUKKEN or n_t > n or n_a > n:
            raise TokenizerFout("tokenizer.bin: aantallen buiten de grenzen")
        maten = (("I", n + 1), ("B", n_blok), ("Q", n_m), ("Q", n_m), ("I", n_t), ("I", n_t))
        maten += (("I", 256), ("I", 2 * n_a))
        nodig = _KOP.size + sum(c * array(t).itemsize for t, c in maten)
        if nodig != len(inhoud):
            raise TokenizerFout(f"tokenizer.bin: {len(inhoud)} bytes, de kop zegt {nodig}")
        pos = _KOP.size
        delen: list[array] = []
        for t, c in maten:
            a = array(t)
            a.frombytes(inhoud[pos : pos + c * a.itemsize])
            if not _LE_MACHINE:
                a.byteswap()
            pos += c * a.itemsize
            delen.append(a)
        offsets, blok, sleutels, waarden, punten, t_ids, bytes_ids, toeg = delen
        blok_bytes = blok.tobytes()
        if len(offsets) < 2 or offsets[-1] != len(blok_bytes):
            raise TokenizerFout("tokenizer.bin: de offsets passen niet bij het blok")
        toegevoegd = [(toeg[2 * k], toeg[2 * k + 1]) for k in range(n_a)]
        return cls(
            blok=blok_bytes,
            offsets=offsets,
            sleutels=sleutels,
            waarden=waarden,
            punten=punten,
            tekens=t_ids,
            bytes_ids=bytes_ids,
            toegevoegd=toegevoegd,
            vlaggen=vlaggen,
            pad=pad,
            eos=eos,
            unk=unk,
            lengte=lengte,
        )

    @classmethod
    def load(cls, path: str | Path) -> Tokenizer:
        with Path(path).open("rb") as f:
            data = f.read(MAX_BIN + 1)
        return cls.from_bin(data)

    # --- tokenizer.json --------------------------------------------------------------------

    @classmethod
    def from_json(
        cls, bron: str | Path | bytes, *, kleine_letters: bool = True, lengte: int = LENGTE
    ) -> Tokenizer:
        """Lees tokenizer.json (BPE, zoals `tokenizers` het schrijft). Alleen wat SigLIP 2
        gebruikt, mag; iets anders geeft TokenizerFout met wat er niet klopt. kleine_letters:
        de standaard van SigLIP 2, ook als het bestand geen Lowercase heeft (Keuze 74)."""
        if isinstance(bron, (bytes, bytearray)):
            data = bytes(bron)
        else:
            with Path(bron).open("rb") as f:
                data = f.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise TokenizerFout(f"tokenizer.json is groter dan {MAX_JSON} bytes")
        try:
            doc = json.loads(data.decode("utf-8"), object_pairs_hook=_geen_dubbele)
        except (UnicodeDecodeError, ValueError, RecursionError) as e:
            soort = type(e).__name__
            raise TokenizerFout(f"tokenizer.json is geen geldige JSON ({soort})") from None
        try:
            return cls._uit_doc(doc, kleine_letters=kleine_letters, lengte=lengte)
        except RecursionError:  # een vangnet: elke plek die nest, heeft al een grens
            raise TokenizerFout("tokenizer.json: te diep genest") from None

    @classmethod
    def _uit_doc(cls, doc: object, *, kleine_letters: bool, lengte: int) -> Tokenizer:
        if not isinstance(doc, dict):
            raise TokenizerFout("tokenizer.json: geen object")
        model = doc.get("model")
        if not isinstance(model, dict) or model.get("type") != "BPE":
            raise TokenizerFout("tokenizer.json: model.type is niet BPE")
        for veld, mag in (
            ("dropout", (None, 0, 0.0)),
            ("continuing_subword_prefix", (None, "")),
            ("end_of_word_suffix", (None, "")),
            ("ignore_merges", (None, False)),
        ):
            if model.get(veld) not in mag:
                raise TokenizerFout(f"tokenizer.json: model.{veld} = {model.get(veld)!r} kan niet")
        vocab = model.get("vocab")
        if not isinstance(vocab, dict) or not vocab:
            raise TokenizerFout("tokenizer.json: model.vocab ontbreekt")
        n = len(vocab)
        if n > MAX_STUKKEN:
            raise TokenizerFout(f"tokenizer.json: {n} stukken; hooguit {MAX_STUKKEN}")
        lijst: list[str | None] = [None] * n
        for stuk, i in vocab.items():
            if not isinstance(stuk, str) or not stuk or type(i) is not int or not 0 <= i < n:
                raise TokenizerFout(f"tokenizer.json: vocab heeft een id buiten 0..{n - 1}")
            if lijst[i] is not None:
                raise TokenizerFout(f"tokenizer.json: id {i} staat er twee keer in")
            lijst[i] = stuk

        vlaggen = KLEINE_LETTERS if kleine_letters else 0
        if model.get("byte_fallback"):
            vlaggen |= BYTE_FALLBACK
        if model.get("fuse_unk"):
            vlaggen |= UNK_SAMEN
        vlaggen |= _normalizer(doc.get("normalizer"))
        _pre_tokenizer(doc.get("pre_tokenizer"), vlaggen)
        unk_naam = model.get("unk_token")
        if unk_naam is None:
            unk = GEEN
        elif isinstance(unk_naam, str) and unk_naam in vocab:
            unk = vocab[unk_naam]
        else:
            raise TokenizerFout("tokenizer.json: model.unk_token staat niet in vocab")
        for naam in ("<pad>", "<eos>"):
            if naam not in vocab:
                raise TokenizerFout(f"tokenizer.json: {naam} staat niet in vocab")
        _post_processor(doc.get("post_processor"))
        _richting(doc.get("truncation"), "truncation")
        _richting(doc.get("padding"), "padding")

        merges_json = model.get("merges")
        if not isinstance(merges_json, list):
            raise TokenizerFout("tokenizer.json: model.merges ontbreekt")
        per_paar: dict[int, int] = {}
        for rang, m in enumerate(merges_json):
            if isinstance(m, str):
                delen = m.split(" ")
            elif isinstance(m, list):
                delen = m
            else:
                delen = []
            if len(delen) != 2 or not all(isinstance(d, str) for d in delen):
                raise TokenizerFout(f"tokenizer.json: merge {rang} is geen paar")
            a, b = delen
            if a not in vocab or b not in vocab or a + b not in vocab:
                raise TokenizerFout(f"tokenizer.json: merge {rang} gebruikt een stuk buiten vocab")
            # Een paar dat twee keer staat: de laatste telt (een HashMap in tokenizers)
            per_paar[(vocab[a] << 32) | vocab[b]] = (rang << 32) | vocab[a + b]
        sleutels = array("Q", sorted(per_paar))
        waarden = array("Q", (per_paar[k] for k in sleutels))

        blok = bytearray()
        offsets = array("I", [0])
        for stuk in lijst:
            blok += stuk.encode("utf-8")  # type: ignore[union-attr] - elke plek is gevuld
            offsets.append(len(blok))
        tekens = sorted((ord(s), i) for i, s in enumerate(lijst) if len(s) == 1)  # type: ignore[arg-type]
        bytes_ids = array("I", (vocab.get(_byte_stuk(b), GEEN) for b in range(256)))

        toegevoegd = []
        alle = doc.get("added_tokens")
        if alle is not None and not isinstance(alle, list):
            raise TokenizerFout("tokenizer.json: added_tokens is geen lijst")
        for t in alle or []:
            if not isinstance(t, dict):
                raise TokenizerFout("tokenizer.json: een added_token is geen object")
            i, inhoud = t.get("id"), t.get("content")
            if type(i) is not int or not 0 <= i < n or inhoud != lijst[i]:
                raise TokenizerFout(f"tokenizer.json: added_token {i!r} past niet bij vocab")
            for veld in ("lstrip", "rstrip", "single_word"):
                if t.get(veld):
                    raise TokenizerFout(f"tokenizer.json: added_token {i} met {veld} kan niet")
            toegevoegd.append((i, bool(t.get("normalized"))))
        return cls(
            blok=bytes(blok),
            offsets=offsets,
            sleutels=sleutels,
            waarden=waarden,
            punten=array("I", (p for p, _ in tekens)),
            tekens=array("I", (i for _, i in tekens)),
            bytes_ids=bytes_ids,
            toegevoegd=toegevoegd,
            vlaggen=vlaggen,
            pad=vocab["<pad>"],
            eos=vocab["<eos>"],
            unk=unk,
            lengte=lengte,
        )


_LE_MACHINE = struct.pack("=I", 1) == struct.pack("<I", 1)


def _le(a: array) -> bytes:
    """De bytes van een array in little-endian, ook op een machine die dat niet is."""
    if _LE_MACHINE:
        return a.tobytes()
    b = array(a.typecode, a)
    b.byteswap()
    return b.tobytes()


def _geen_dubbele(paren: list[tuple[str, object]]) -> dict[str, object]:
    uit: dict[str, object] = {}
    for k, v in paren:
        if k in uit:
            raise ValueError("dubbele sleutel")
        uit[k] = v
    return uit


def _normalizer(n: object, diepte: int = 0) -> int:
    """De normalizers die SigLIP 2 (Gemma) gebruikt; de rest kan niet. Een Sequence in een
    Sequence mag, tot MAX_NESTING diep (SigLIP 2 heeft er één): dieper is TokenizerFout, geen
    RecursionError (de review van golf 5, veilig F4)."""
    if n is None:
        return 0
    if not isinstance(n, dict):
        raise TokenizerFout("tokenizer.json: normalizer is geen object")
    soort = n.get("type")
    if soort == "Sequence":
        if diepte >= MAX_NESTING:
            raise TokenizerFout(f"tokenizer.json: normalizer meer dan {MAX_NESTING} diep genest")
        lijst = n.get("normalizers")
        if not isinstance(lijst, list):
            raise TokenizerFout("tokenizer.json: normalizer Sequence zonder lijst")
        vlaggen = 0
        for deel in lijst:
            vlaggen |= _normalizer(deel, diepte + 1)
        return vlaggen
    if soort == "Lowercase":
        return KLEINE_LETTERS
    if soort == "Replace" and n.get("pattern") == {"String": " "} and n.get("content") == SPATIE:
        return SPATIE_WORDT_STREEP
    if soort == "Prepend" and n.get("prepend") == SPATIE:
        return STREEP_ERVOOR
    raise TokenizerFout(f"tokenizer.json: normalizer {soort!r} kan niet")


def _pre_tokenizer(p: object, vlaggen: int) -> None:
    """Geen pre_tokenizer (Gemma), of een Split op " " die na Replace(" ", "▁") niets meer doet."""
    if p is None:
        return
    if (
        isinstance(p, dict)
        and p.get("type") == "Split"
        and p.get("pattern") == {"String": " "}
        and p.get("invert") in (None, False)
        and vlaggen & SPATIE_WORDT_STREEP
    ):
        return
    soort = p.get("type") if isinstance(p, dict) else p
    raise TokenizerFout(f"tokenizer.json: pre_tokenizer {soort!r} kan niet")


def _post_processor(p: object) -> None:
    """Niets, of een TemplateProcessing die alleen <eos> achter de tekst zet. <bos> ervoor kan
    niet: SigLIP 2 heeft er geen (Keuze 74)."""
    if p is None:
        return
    if not isinstance(p, dict) or p.get("type") != "TemplateProcessing":
        soort = p.get("type") if isinstance(p, dict) else p
        raise TokenizerFout(f"tokenizer.json: post_processor {soort!r} kan niet")
    stappen = p.get("single")
    if not isinstance(stappen, list):
        raise TokenizerFout("tokenizer.json: post_processor zonder single")
    vorm = []
    for s in stappen:
        if isinstance(s, dict) and "Sequence" in s:
            vorm.append("A")
        elif isinstance(s, dict) and isinstance(s.get("SpecialToken"), dict):
            vorm.append(s["SpecialToken"].get("id"))
        else:
            vorm.append("?")
    if vorm not in (["A"], ["A", "<eos>"]):
        raise TokenizerFout(f"tokenizer.json: post_processor {vorm} kan niet; verwacht A <eos>")


def _richting(v: object, naam: str) -> None:
    """truncation en padding: alleen rechts, zoals SigLIP 2 getraind is."""
    if v is None:
        return
    if not isinstance(v, dict):
        raise TokenizerFout(f"tokenizer.json: {naam} is geen object")
    if v.get("direction", "Right") != "Right":
        raise TokenizerFout(f"tokenizer.json: {naam} links kan niet")
