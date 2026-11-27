"""Het clientprofiel (Keuze 20): de vorm, de grens van 64 strings en 8 KB, onbekende velden weg,
een basisprofiel plus eigen metingen, de uitsluitingen (Hi10P, MED-10) en de families."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.media import profile as P
from app.media.types import PROFILE_BASES, ClientProfile, PlayIn, codec_string_ok

EXAMPLE = {
    "v": 1,
    "base": "web",
    "client": {"kind": "web", "name": "Chrome 141", "app": "0.7.0"},
    "engines": {"mse": True, "mms": False, "native_hls": False},
    "file": ["video/mp4", "video/webm", "audio/mpeg", "audio/flac", "audio/ogg"],
    "codecs": {
        "vp09.00.40.08": {"mse": True, "file": True, "smooth": True},
        "avc1.640028": {"mse": False, "file": False},
        "opus": {"mse": True, "file": True},
    },
    "hdr": {"pq": False, "hlg": False, "dv": False, "screen": False},
    "max": {"height": 1440, "bitrate_kbps": 0},
    "audio": {"channels": 2, "track_switch": False},
}


def test_het_voorbeeld_van_keuze_20():
    p = P.load(EXAMPLE)
    assert p.base == "web" and p.client.name == "Chrome 141"
    assert p.codecs["vp09.00.40.08"].smooth is True
    assert p.codecs["avc1.640028"].mse is False
    assert p.max.height == 1440
    # ook als JSON-tekst
    assert P.load(json.dumps(EXAMPLE)) == p


def test_onbekende_velden_en_strings_gaan_weg():
    data = {
        **EXAMPLE,
        "geheim": "x",
        "client": {"kind": "web", "name": "a", "extra": 1},
        "codecs": {
            "vp09.00.40.08": {"mse": True, "onbekend": 5},
            "<script>": {"mse": True},
            "avc1.zzzzzz": {"mse": True},
            "hvc1.1.6.L120.90": {"mse": True},
        },
        "file": ["video/mp4", "text/html", 5, "video/mp4", "audio/flac"],
    }
    p = P.load(data)
    assert "geheim" not in p.model_dump()
    assert set(p.codecs) == {"vp09.00.40.08", "hvc1.1.6.L120.90"}
    assert p.file == ["video/mp4", "audio/flac"]
    assert not hasattr(p.client, "extra")


def test_hooguit_64_strings():
    many = {f"vp09.00.{i % 100:02d}.08": {"mse": True} for i in range(64)}
    many |= {f"av01.0.{i:02d}M.08": {"mse": True} for i in range(30)}
    p = P.load({"v": 1, "codecs": many})
    assert len(p.codecs) == 64


def test_hooguit_8_kb():
    big = {"v": 1, "client": {"name": "x" * 60}, "padding": "y" * 9000}
    with pytest.raises(ValidationError):
        P.load(big)
    with pytest.raises(ValidationError):
        P.load(json.dumps(big))
    with pytest.raises(ValidationError):
        PlayIn.model_validate({"profile": big})


def test_verkeerde_waarden():
    with pytest.raises(ValidationError):
        P.load({"v": 2})
    with pytest.raises(ValidationError):
        P.load({"base": "netscape"})
    with pytest.raises(ValidationError):
        P.load({"max": {"height": 99999}})
    with pytest.raises(ValidationError):
        P.load({"audio": {"channels": 0}})


@pytest.mark.parametrize("name", PROFILE_BASES)
def test_elk_basisprofiel_is_geldig(name):
    raw = json.loads((P.PROFILES / f"{name}.json").read_text(encoding="utf-8"))
    assert raw["base"] == name and raw["v"] == 1
    assert len(json.dumps(raw, separators=(",", ":"))) <= 8192
    for string in raw.get("codecs", {}):
        assert codec_string_ok(string), string
    base = P.base(name)
    assert base.base == name
    # de uitsluitingen en de vlaggen bestaan altijd
    assert isinstance(raw["deny"], list) and isinstance(raw["deny_by_name"], dict)
    assert set(P.flags(name)) == {"independent_segments"}


def test_onbekend_basisprofiel():
    with pytest.raises(ValueError):
        P.base("netscape")


def test_webos_zonder_independent_segments():
    assert P.flags("webos-5") == {"independent_segments": False}
    assert P.flags("web") == {"independent_segments": True}


def test_de_kandidaten_van_keuze_20():
    got = P.candidates()
    assert got[0] == "avc1.42E01E" and len(got) == 17
    assert all(codec_string_ok(s) for s in got)
    got.append("x")
    assert len(P.candidates()) == 17  # een kopie


def test_basisprofiel_plus_eigen_metingen():
    """Wat de client meet, gaat voor; wat hij niet meestuurt, komt uit het basisprofiel."""
    mine = P.load(
        {
            "v": 1,
            "base": "webos-5",
            "client": {"kind": "tv", "name": "LG"},
            "codecs": {
                "vp09.00.51.08": {"mse": False},  # gemeten: nee
                "av01.0.08M.08": {"mse": True, "file": True},  # erbij
            },
            "hdr": {"screen": True},
        }
    )
    merged = P.merge(mine)
    assert merged.base == "webos-5"
    assert merged.engines.native_hls is True  # uit het basisprofiel
    assert merged.codecs["vp09.00.51.08"].mse is False  # de meting
    assert merged.codecs["vp09.00.51.08"].file is True  # niet gemeten: het basisprofiel
    assert merged.codecs["av01.0.08M.08"].mse is True
    assert merged.codecs["ac-3"].mse is True
    assert merged.hdr.screen is True and merged.hdr.pq is True
    assert merged.max.height == 2160
    # het basisprofiel zelf is niet veranderd
    assert P.base("webos-5").codecs["vp09.00.51.08"].mse is True


def test_een_lege_meting_houdt_het_basisprofiel():
    merged = P.merge(P.load({"v": 1, "base": "ios-safari"}))
    assert merged == P.base("ios-safari")
    # een expliciete lege lijst van bestanden gaat wel voor
    merged = P.merge(P.load({"v": 1, "base": "ios-safari", "file": []}))
    assert merged.file == []


def test_hi10p_uit_in_safari_en_webos_ook_als_gemeten():
    hi10p = {"avc1.6e001e": {"mse": True, "file": True, "smooth": True}}
    for base in ("ios-safari", "webos-5"):
        merged = P.merge(P.load({"v": 1, "base": base, "codecs": hi10p}))
        found = P.support(merged, "avc1.6e001e")
        assert found is not None and not found.mse and not found.file, base


def test_hi10p_uit_voor_firefox_en_aan_voor_chrome():
    hi10p = {"avc1.6e001e": {"mse": True, "file": True}}
    firefox = P.merge(P.load({"v": 1, "client": {"name": "Firefox 131"}, "codecs": hi10p}))
    assert not P.support(firefox, "avc1.6e001e").mse
    chrome = P.merge(P.load({"v": 1, "client": {"name": "Chrome 141"}, "codecs": hi10p}))
    assert P.support(chrome, "avc1.6e001e").mse


def test_families():
    p = P.load(
        {
            "v": 1,
            "codecs": {
                "avc1.640033": {"mse": True, "file": False, "smooth": True},
                "hvc1.2.4.L150.90": {"mse": True},
                "vp09.00.40.08": {"file": True},
                "av01.0.08M.08": {"mse": True},
                "mp4a.40.2": {"mse": True},
            },
        }
    )
    # dezelfde familie met een lager level
    assert P.support(p, "avc1.64001f").mse and P.support(p, "avc1.64001f").smooth is True
    assert P.support(p, "AVC1.64001F").mse
    assert P.support(p, "hvc1.2.4.L93.B0").mse
    assert P.support(p, "vp09.00.21.08").file
    assert P.support(p, "av01.0.05M.08").mse
    # een hoger level, een ander profiel, een andere bitdiepte of tier: niet
    assert P.support(p, "avc1.640034") is None
    assert P.support(p, "avc1.6e001e") is None  # Hi10P is een eigen profiel
    assert P.support(p, "avc1.4d401f") is None
    assert P.support(p, "hvc1.1.6.L93.B0") is None
    assert P.support(p, "hvc1.2.4.H120.90") is None
    assert P.support(p, "vp09.02.40.10") is None
    assert P.support(p, "av01.0.08M.10") is None
    assert P.support(p, "av01.0.05H.08") is None
    # audio telt alleen precies
    assert P.support(p, "mp4a.40.2").mse
    assert P.support(p, "mp4a.40.5") is None
    assert P.support(p, None) is None and P.support(p, "") is None


def test_familie_met_meerdere_leden():
    p = P.load(
        {
            "v": 1,
            "codecs": {
                "avc1.640028": {"mse": True, "smooth": False},
                "avc1.640033": {"file": True},
            },
        }
    )
    found = P.support(p, "avc1.64001e")
    assert found.mse and found.file and found.smooth is False
    assert P.support(p, "avc1.640029").file and not P.support(p, "avc1.640029").mse


def test_familie():
    assert P.family("avc1.64001f") == P.Family(("avc1", "64"), 0, 31)
    assert P.family("hev1.2.4.H150.90") == P.Family(("hvc1", "", 2), 1, 150)
    assert P.family("vp09.02.51.10") == P.Family(("vp09", 2, 10), 0, 51)
    assert P.family("av01.0.13H.10") == P.Family(("av01", 0, 10), 1, 13)
    assert P.family("dvh1.05.06") == P.Family(("dvh1", 5), 0, 6)
    assert P.family("opus") is None and P.family("avc1.6") is None


def test_een_profiel_is_een_pydantic_model():
    assert isinstance(P.merge(P.load(EXAMPLE)), ClientProfile)
