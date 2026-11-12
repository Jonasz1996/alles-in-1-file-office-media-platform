"""De boxen van fMP4 lezen: de init (`ftyp` + `moov`) en elk fragment (`moof` + `mdat`).

Voor S2 en S5 van de spike (bouwontwerp fase 5, §3.7). Een eigen lezer, met alleen de
standaardbibliotheek: uit `moov` per spoor de tijdschaal, de sample entry (`avc1`, `hvc1`,
`vp09`, `mp4a`, `Opus`), de lengte van een NAL-lengteveld, `trex` en `elst`; uit `moof` per
`traf` de `tfdt`, de versie van `trun` en per sample de duur, grootte, vlaggen en (getekende)
compositie-offset. Van het eerste sample leest hij de NAL-types (H.264, HEVC) of de kop van
het VP9-frame.

Een kapotte stroom geeft nooit een uitzondering: de lezer stopt en zegt waarom (`kapot`).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "mvex", "udta", "moof"}
VIDEO_ENTRIES = {"avc1", "avc3", "hvc1", "hev1", "vp09", "av01"}
AUDIO_ENTRIES = {"mp4a", "Opus", "fLaC", "ac-3", "ec-3"}
NIET_SYNC = 0x00010000  # sample_is_non_sync_sample in de samplevlaggen


def boxen(buf, start: int = 0, end: int | None = None):
    """(type, begin, begin van de inhoud, eind) per box in buf[start:end]."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        grootte, soort = struct.unpack_from(">I4s", buf, pos)
        kop = 8
        if grootte == 1:
            if pos + 16 > end:
                return
            grootte = struct.unpack_from(">Q", buf, pos + 8)[0]
            kop = 16
        elif grootte == 0:
            grootte = end - pos
        if grootte < kop or pos + grootte > end:
            return
        yield soort.decode("latin-1"), pos, pos + kop, pos + grootte
        pos += grootte


def zoek(buf, start: int, end: int, *pad: str):
    """De eerste box langs het pad: (begin, begin van de inhoud, eind), of None."""
    gevonden = None
    for naam in pad:
        for soort, b, inhoud, e in boxen(buf, start, end):
            if soort == naam:
                gevonden = (b, inhoud, e)
                start, end = inhoud, e
                break
        else:
            return None
    return gevonden


class Splitser:
    """Knipt een stroom bytes (de pipe van ffmpeg) in top-level boxen."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.pos = 0  # de plek van buf[0] in de stroom
        self.kapot: str | None = None

    def voeg(self, data: bytes) -> list[tuple[str, int, bytes]]:
        if self.kapot:
            return []
        self.buf += data
        uit = []
        while len(self.buf) >= 8:
            grootte, soort = struct.unpack_from(">I4s", self.buf, 0)
            kop = 8
            if grootte == 1:
                if len(self.buf) < 16:
                    break
                grootte = struct.unpack_from(">Q", self.buf, 8)[0]
                kop = 16
            if grootte == 0:
                break  # loopt tot het eind van de stroom; komt niet voor in fMP4 naar een pipe
            if grootte < kop:
                self.kapot = f"box {soort!r} met grootte {grootte} op {self.pos}"
                break
            if len(self.buf) < grootte:
                break
            uit.append((soort.decode("latin-1"), self.pos, bytes(self.buf[:grootte])))
            del self.buf[:grootte]
            self.pos += grootte
        return uit


@dataclass
class Spoor:
    id: int
    soort: str | None = None  # hdlr: vide of soun
    tijdschaal: int = 0
    entry: str | None = None
    nal_lengte: int | None = None
    trex: tuple[int, int, int] = (0, 0, 0)  # standaard duur, grootte, vlaggen
    elst: list[tuple[int, int]] | None = None  # (duur, media_time)
    stsd: bytes = b""

    def info(self) -> dict:
        return {
            "id": self.id,
            "soort": self.soort,
            "tijdschaal": self.tijdschaal,
            "entry": self.entry,
            "elst": self.elst,
        }


def _u32(buf, pos: int) -> int:
    return struct.unpack_from(">I", buf, pos)[0]


def lees_init(moov: bytes) -> dict[int, Spoor]:
    """De sporen uit een `moov`-box (met kop)."""
    sporen: dict[int, Spoor] = {}
    top = next(boxen(moov), None)
    if top is None or top[0] != "moov":
        return sporen
    _, _, s, e = top
    for soort, _b, ts, te in boxen(moov, s, e):
        if soort != "trak":
            continue
        tkhd = zoek(moov, ts, te, "tkhd")
        if tkhd is None:
            continue
        v = moov[tkhd[1]]
        spoor = Spoor(id=_u32(moov, tkhd[1] + 4 + (16 if v == 1 else 8)))
        mdhd = zoek(moov, ts, te, "mdia", "mdhd")
        if mdhd:
            v = moov[mdhd[1]]
            spoor.tijdschaal = _u32(moov, mdhd[1] + 4 + (16 if v == 1 else 8))
        hdlr = zoek(moov, ts, te, "mdia", "hdlr")
        if hdlr:
            spoor.soort = moov[hdlr[1] + 8 : hdlr[1] + 12].decode("latin-1")
        stsd = zoek(moov, ts, te, "mdia", "minf", "stbl", "stsd")
        if stsd:
            spoor.stsd = moov[stsd[0] : stsd[2]]
            entry = next(boxen(moov, stsd[1] + 8, stsd[2]), None)
            if entry:
                spoor.entry = entry[0]
                vast = 78 if entry[0] in VIDEO_ENTRIES else 28
                for kind, _kb, ks, _ke in boxen(moov, entry[2] + vast, entry[3]):
                    if kind == "avcC":
                        spoor.nal_lengte = (moov[ks + 4] & 3) + 1
                    elif kind == "hvcC":
                        spoor.nal_lengte = (moov[ks + 21] & 3) + 1
        elst = zoek(moov, ts, te, "edts", "elst")
        if elst:
            v = moov[elst[1]]
            n = _u32(moov, elst[1] + 4)
            pos = elst[1] + 8
            items = []
            for _ in range(min(n, 16)):
                if v == 1:
                    duur, tijd = struct.unpack_from(">Qq", moov, pos)
                    pos += 16
                else:
                    duur, tijd = struct.unpack_from(">Ii", moov, pos)
                    pos += 8
                pos += 4  # media_rate
                items.append((duur, tijd))
            spoor.elst = items
        sporen[spoor.id] = spoor
    for soort, _b, ms, me in boxen(moov, s, e):
        if soort == "mvex":
            for kind, _kb, ks, _ke in boxen(moov, ms, me):
                if kind == "trex":
                    tid = _u32(moov, ks + 4)
                    if tid in sporen:
                        sporen[tid].trex = struct.unpack_from(">III", moov, ks + 12)
    return sporen


@dataclass
class Traf:
    spoor: int
    tfdt: int | None = None
    tfdt_versie: int | None = None
    trun_versies: list[int] = field(default_factory=list)
    # per sample: (duur, grootte, vlaggen, cto, plek van de data in de stroom)
    samples: list[tuple[int, int, int, int, int]] = field(default_factory=list)


def lees_moof(moof: bytes, moof_pos: int, sporen: dict[int, Spoor]) -> list[Traf]:
    """De `traf`'s uit een `moof`-box (met kop) die op `moof_pos` in de stroom stond."""
    uit = []
    top = next(boxen(moof), None)
    if top is None or top[0] != "moof":
        return uit
    _, _, s, e = top
    for soort, _b, ts, te in boxen(moof, s, e):
        if soort != "traf":
            continue
        tfhd = zoek(moof, ts, te, "tfhd")
        if tfhd is None:
            continue
        p = tfhd[1]
        vlaggen = int.from_bytes(moof[p + 1 : p + 4], "big")
        traf = Traf(spoor=_u32(moof, p + 4))
        trex = sporen.get(traf.spoor, Spoor(id=traf.spoor)).trex
        dd, ds, df = trex
        basis = moof_pos
        p += 8
        if vlaggen & 0x1:
            basis = struct.unpack_from(">Q", moof, p)[0]
            p += 8
        if vlaggen & 0x2:
            p += 4
        if vlaggen & 0x8:
            dd = _u32(moof, p)
            p += 4
        if vlaggen & 0x10:
            ds = _u32(moof, p)
            p += 4
        if vlaggen & 0x20:
            df = _u32(moof, p)
            p += 4
        for kind, _kb, ks, _ke in boxen(moof, ts, te):
            if kind == "tfdt":
                v = moof[ks]
                traf.tfdt_versie = v
                traf.tfdt = (
                    struct.unpack_from(">Q", moof, ks + 4)[0] if v == 1 else _u32(moof, ks + 4)
                )
            elif kind == "trun":
                v = moof[ks]
                tv = int.from_bytes(moof[ks + 1 : ks + 4], "big")
                traf.trun_versies.append(v)
                n = _u32(moof, ks + 4)
                q = ks + 8
                plek = basis
                if tv & 0x1:
                    plek = basis + struct.unpack_from(">i", moof, q)[0]
                    q += 4
                eerste = None
                if tv & 0x4:
                    eerste = _u32(moof, q)
                    q += 4
                for i in range(n):
                    duur, grootte, vl, cto = dd, ds, df, 0
                    if tv & 0x100:
                        duur = _u32(moof, q)
                        q += 4
                    if tv & 0x200:
                        grootte = _u32(moof, q)
                        q += 4
                    if tv & 0x400:
                        vl = _u32(moof, q)
                        q += 4
                    elif i == 0 and eerste is not None:
                        vl = eerste
                    if tv & 0x800:
                        cto = struct.unpack_from(">i" if v == 1 else ">I", moof, q)[0]
                        q += 4
                    traf.samples.append((duur, grootte, vl, cto, plek))
                    plek += grootte
        uit.append(traf)
    return uit


def nal_typen(data: bytes, lengte: int, hevc: bool) -> list[int]:
    """De NAL-types in één sample (lengte-voorvoegsels, zoals in mp4)."""
    typen = []
    pos = 0
    while pos + lengte <= len(data) and len(typen) < 64:
        n = int.from_bytes(data[pos : pos + lengte], "big")
        pos += lengte
        if n == 0 or pos + n > len(data):
            break
        b = data[pos]
        typen.append((b >> 1) & 0x3F if hevc else b & 0x1F)
        pos += n
    return typen


def vp9_sleutel(data: bytes) -> bool | None:
    """Of een VP9-frame een keyframe is (de eerste bits van de framekop)."""
    if not data:
        return None
    b = data[0]
    if b >> 6 != 2:
        return None
    profiel = ((b >> 5) & 1) | (((b >> 4) & 1) << 1)
    bit = 3 if profiel < 3 else 2
    if (b >> bit) & 1:  # show_existing_frame
        return False
    return ((b >> (bit - 1)) & 1) == 0


H264_VCL = {1, 2, 3, 4, 5}
H264_IDR = 5
HEVC_IRAP = set(range(16, 24))
HEVC_IDR = {19, 20}


def ontleed(trafs: list[Traf], mdat: bytes, mdat_pos: int, sporen: dict[int, Spoor]) -> dict:
    """Per spoor van één fragment: tijden, aantallen, en wat het eerste sample is."""
    uit = {}
    for traf in trafs:
        spoor = sporen.get(traf.spoor)
        if spoor is None or not spoor.tijdschaal:
            continue
        ts = spoor.tijdschaal
        rij = {
            "tfdt": traf.tfdt,
            "tfdt_s": None if traf.tfdt is None else traf.tfdt / ts,
            "trun": traf.trun_versies,
            "n": len(traf.samples),
            "duur_s": sum(s[0] for s in traf.samples) / ts,
            "duren": sorted({s[0] for s in traf.samples}),
            "cto": [s[3] for s in traf.samples[:6]],
        }
        if traf.samples and traf.tfdt is not None:
            rij["pts0_s"] = (traf.tfdt + traf.samples[0][3]) / ts
            rij["sync0"] = not (traf.samples[0][2] & NIET_SYNC)
            rij["sync_n"] = sum(1 for s in traf.samples if not (s[2] & NIET_SYNC))
        if spoor.soort == "vide" and traf.samples:
            rij.update(_video(traf, spoor, mdat, mdat_pos))
        uit[spoor.soort or str(traf.spoor)] = rij
    return uit


def _sample(mdat: bytes, mdat_pos: int, plek: int, grootte: int) -> bytes | None:
    begin = plek - mdat_pos
    if begin < 8 or begin + grootte > len(mdat):
        return None
    return mdat[begin : begin + grootte]


def _video(traf: Traf, spoor: Spoor, mdat: bytes, mdat_pos: int) -> dict:
    hevc = spoor.entry in ("hvc1", "hev1")
    if spoor.entry in ("avc1", "avc3", "hvc1", "hev1") and spoor.nal_lengte:
        idr = HEVC_IDR if hevc else {H264_IDR}
        eerste = None
        aantal_idr = 0
        for i, (_d, grootte, _v, _c, plek) in enumerate(traf.samples):
            data = _sample(mdat, mdat_pos, plek, grootte)
            if data is None:
                return {"nal0": None, "fout": "sample buiten de mdat"}
            typen = nal_typen(data, spoor.nal_lengte, hevc)
            if i == 0:
                eerste = typen
            if any(t in idr for t in typen):
                aantal_idr += 1
        vcl = [t for t in (eerste or []) if (t < 32 if hevc else t in H264_VCL)]
        return {"nal0": eerste, "vcl0": vcl[0] if vcl else None, "idr_samples": aantal_idr}
    if spoor.entry == "vp09":
        sleutels = []
        for _d, grootte, _v, _c, plek in traf.samples:
            data = _sample(mdat, mdat_pos, plek, grootte)
            sleutels.append(None if data is None else vp9_sleutel(data[:4]))
        return {"vp9_sleutel0": sleutels[0], "vp9_sleutels": sum(1 for k in sleutels if k)}
    return {}


def boom(buf: bytes, start: int = 0, end: int | None = None, pad: str = "") -> dict[str, bytes]:
    """Elke blad-box als pad → bytes, voor de vergelijking van twee inits (S5).

    Containers worden doorlopen. `stsd` staat er zelf in (de vergelijking van Keuze 22) en ook
    per kind van zijn eerste entry (`avcC`, `btrt`, `esds`, …), zodat te zien is waar hij
    verschilt. Een `trak` heet naar zijn spoor-id."""
    uit: dict[str, bytes] = {}
    tellers: dict[str, int] = {}
    for soort, b, s, e in boxen(buf, start, end):
        naam = soort
        if soort == "trak":
            tkhd = zoek(buf, s, e, "tkhd")
            if tkhd:
                v = buf[tkhd[1]]
                naam = f"trak#{_u32(buf, tkhd[1] + 4 + (16 if v == 1 else 8))}"
        tellers[naam] = tellers.get(naam, 0) + 1
        if tellers[naam] > 1:
            naam = f"{naam}[{tellers[naam]}]"
        hier = f"{pad}/{naam}" if pad else naam
        if soort in CONTAINERS:
            uit.update(boom(buf, s, e, hier))
        elif soort == "stsd":
            uit[hier] = bytes(buf[b:e])
            entry = next(boxen(buf, s + 8, e), None)
            if entry:
                vast = 78 if entry[0] in VIDEO_ENTRIES else 28
                uit[f"{hier}/{entry[0]}(vast)"] = bytes(buf[entry[1] : entry[2] + vast])
                uit.update(boom(buf, entry[2] + vast, entry[3], f"{hier}/{entry[0]}"))
        else:
            uit[hier] = bytes(buf[b:e])
    return uit


def verschil(a: bytes, b: bytes) -> list[str]:
    """De paden van de boxen die verschillen tussen twee inits."""
    ba, bb = boom(a), boom(b)
    return sorted(p for p in set(ba) | set(bb) if ba.get(p) != bb.get(p))
