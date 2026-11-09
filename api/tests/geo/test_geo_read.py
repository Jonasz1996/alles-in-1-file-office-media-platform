"""De plek lezen (bouwontwerp fase 6, Keuze 25): de GPS-IFD van EXIF, XMP via
office.reader.sax(), HEIC, en ISO 6709 van een video via meta van de mediaworker (marker media).
Elke waarde wordt nagekeken: wat niet klopt, geeft geen plek (nooit een verkeerde)."""

import io
import json
import math
import subprocess

import pytest
from PIL import Image, TiffImagePlugin
from sqlalchemy import select

from app import jobs, photometa, render_meta
from app.media import local
from app.models import BlobMeta, PhotoGeo
from tests.factories import maker
from tests.test_previews import upload
from tests.test_previews_video_worker import media_on
from tests.test_render_meta import camera_jpeg

AMSTERDAM = {"lat": 52.3669444, "lon": 4.8838889, "alt": None, "source": "exif"}
R = TiffImagePlugin.IFDRational


def jpeg(gps: dict | None = None, xmp: bytes | None = None) -> bytes:
    exif = Image.Exif()
    if gps is not None:
        info = exif.get_ifd(render_meta.GPS_IFD)
        info.update(gps)
    buf = io.BytesIO()
    extra = {"xmp": xmp} if xmp is not None else {}
    Image.new("RGB", (16, 16), "blue").save(buf, "JPEG", exif=exif, **extra)
    return buf.getvalue()


def plek(data: bytes):
    with Image.open(io.BytesIO(data)) as img:
        return render_meta.image_location(img)


def gps(lat=(52.0, 22.0, 1.0), lon=(4.0, 53.0, 2.0), lat_ref="N", lon_ref="E", **rest) -> dict:
    uit = {1: lat_ref, 2: lat, 3: lon_ref, 4: lon}
    for k, v in rest.items():
        uit[{"alt_ref": 5, "alt": 6}[k]] = v
    return {k: v for k, v in uit.items() if v is not None}


def test_de_gps_ifd_heen_en_terug():
    """Pillow schrijft de GPS-IFD, render_meta leest hem: graden, minuten, seconden."""
    assert plek(camera_jpeg()) == AMSTERDAM
    assert plek(jpeg(gps())) == AMSTERDAM
    # Zuid en west, met een hoogte onder zeeniveau (AltitudeRef 1)
    zw = plek(jpeg(gps(lat_ref="S", lon_ref="W", alt=R(125, 10), alt_ref=b"\x01")))
    assert zw == {"lat": -52.3669444, "lon": -4.8838889, "alt": -12.5, "source": "exif"}
    # Rationals zoals een camera ze schrijft, en één getal voor de graden
    rat = plek(jpeg(gps(lat=(R(52, 1), R(2201, 100), R(0, 1)), lon=(R(48838889, 10_000_000),))))
    assert math.isclose(rat["lat"], 52 + 22.01 / 60, abs_tol=1e-7)
    assert rat["lon"] == 4.8838889
    # Boven zeeniveau en een hoogte die onzin is (> 100 km): de plek wel, de hoogte niet
    assert plek(jpeg(gps(alt=R(31, 1), alt_ref=0)))["alt"] == 31.0
    assert plek(jpeg(gps(alt=R(10**9, 1))))["alt"] is None


@pytest.mark.parametrize(
    ("geval", "waarden"),
    [
        ("IFDRational(0, 0)", gps(lat=(R(0, 0), R(0, 0), R(0, 0)))),
        ("noemer 0 in de minuten", gps(lat=(R(52, 1), R(1, 0), R(0, 1)))),
        ("|lat| > 90", gps(lat=(95.0, 0.0, 0.0))),
        ("|lon| > 180", gps(lon=(181.0, 0.0, 0.0))),
        ("(0, 0)", gps(lat=(0.0, 0.0, 0.0), lon=(0.0, 0.0, 0.0))),
        ("zonder LatitudeRef", gps(lat_ref=None)),
        ("zonder LongitudeRef", gps(lon_ref=None)),
        ("een Ref die niet bestaat", gps(lat_ref="X")),
        ("minuten 60", gps(lat=(52.0, 60.0, 0.0))),
        ("seconden 60", gps(lat=(52.0, 1.0, 60.0))),
        ("vier delen", gps(lat=(52.0, 1.0, 1.0, 1.0))),
        ("zonder breedte", gps(lat=None)),
    ],
)
def test_wat_niet_klopt_geeft_geen_plek(geval, waarden):
    assert plek(jpeg(waarden)) is None, geval


class _Exif:
    """Een Exif zoals Pillow hem geeft, met waarden die Pillow zelf niet schrijft."""

    def __init__(self, gps_ifd):
        self.gps_ifd = gps_ifd

    def get_ifd(self, tag):
        assert tag == render_meta.GPS_IFD
        if isinstance(self.gps_ifd, Exception):
            raise self.gps_ifd
        return self.gps_ifd


@pytest.mark.parametrize(
    "waarden",
    [
        gps(lat=(-52.0, 0.0, 0.0)),  # negatieve graden
        gps(lat=(float("nan"), 0.0, 0.0)),
        gps(lon=(float("inf"),)),
        gps(lat=((52, 1), 0.0, 0.0)),  # een tuple in een tuple
        gps(lat="52.1"),  # tekst
        gps(lat=(True, 0.0, 0.0)),
        gps(lat_ref=b"\xff"),
        ValueError("kapotte IFD"),
    ],
)
def test_waarden_die_pillow_niet_schrijft(waarden):
    assert render_meta.exif_location(_Exif(waarden)) is None


def test_zonder_gps_geen_plek_maar_wel_gelezen():
    assert plek(jpeg()) is None
    assert plek(camera_jpeg(gps=False)) is None


def xmp(lat: str, lon: str, alt: str | None = None, *, als_attribuut: bool = False) -> bytes:
    ns = 'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" ' \
         'xmlns:exif="http://ns.adobe.com/exif/1.0/"'  # fmt: skip
    if als_attribuut:
        hoogte = f' exif:GPSAltitude="{alt}"' if alt else ""
        beschrijving = (
            f'<rdf:Description {ns} exif:GPSLatitude="{lat}" exif:GPSLongitude="{lon}"{hoogte}/>'
        )
    else:
        hoogte = f"<exif:GPSAltitude>{alt}</exif:GPSAltitude>" if alt else ""
        beschrijving = (
            f"<rdf:Description {ns}><exif:GPSLatitude>{lat}</exif:GPSLatitude>"
            f"<exif:GPSLongitude>{lon}</exif:GPSLongitude>{hoogte}</rdf:Description>"
        )
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{beschrijving}</rdf:RDF></x:xmpmeta>"
    ).encode()


def test_xmp_als_element_en_als_attribuut():
    verwacht = {"lat": 52.3669444, "lon": -4.8838889, "alt": 12.5, "source": "xmp"}
    assert render_meta.xmp_location(xmp("52,22.01667N", "4,53.03333W", "25/2")) == {
        **verwacht, "lat": 52.3669445, "lon": -4.8838888
    }  # fmt: skip
    assert render_meta.xmp_location(xmp("52,22,1N", "4,53,2W", "12.5", als_attribuut=True)) == (
        verwacht
    )
    # In een JPEG zonder GPS-IFD: de XMP telt
    assert plek(jpeg(xmp=xmp("52,22,1N", "4,53,2E"))) == {**AMSTERDAM, "source": "xmp"}
    # De GPS-IFD gaat voor
    assert plek(jpeg(gps(), xmp=xmp("10,0,0S", "10,0,0W"))) == AMSTERDAM


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        ("95,0,0N", "4,53,2E"),  # te noordelijk
        ("52,61,0N", "4,53,2E"),  # 61 minuten
        ("52,22,1X", "4,53,2E"),  # geen richting
        ("52N", "4E"),  # alleen graden: geen GPSCoordinate
        ("0,0,0N", "0,0,0E"),  # (0, 0)
        ("nan,0N", "4,53,2E"),
        ("1e999,0N", "4,53,2E"),
        ("52,22,1N" + "0" * 50, "4,53,2E"),  # te lang
    ],
)
def test_xmp_die_niet_klopt(lat, lon):
    assert render_meta.xmp_location(xmp(lat, lon)) is None


def test_xmp_met_entiteiten_wordt_geweigerd():
    """Een "billion laughs" (DOCTYPE met entiteiten): office.reader.sax() weigert hem, dus geen
    plek, en het kost niets."""
    bom = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + "".join(
            f'<!ENTITY lol{i} "{("&lol" + (str(i - 1) if i > 1 else "") + ";") * 10}">'
            for i in range(1, 10)
        )
        + "]>"
    ).encode()
    data = bom + xmp("&lol9;", "4,53,2E")
    assert render_meta.xmp_location(data) is None
    assert plek(jpeg(xmp=data)) is None
    # Ook een externe entiteit niet
    extern = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>' + xmp("&e;", "4,53,2E")
    assert render_meta.xmp_location(extern) is None
    # Te groot of geen bytes: niets
    assert render_meta.xmp_location(b"x" * (render_meta.XMP_BYTES + 1)) is None
    assert render_meta.xmp_location(None) is None
    assert render_meta.xmp_location(b"<kapot") is None


def test_heic_met_gps(tmp_path):
    """HEIC (pillow-heif) met een GPS-IFD: via run_meta, zoals photometa hem leest."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    exif = Image.Exif()
    exif.get_ifd(render_meta.GPS_IFD).update(gps())
    pad = tmp_path / "foto.heic"
    Image.new("RGB", (64, 48), "green").save(pad, "HEIF", exif=exif.tobytes())
    out = render_meta.run_meta({"items": [{"sha": "h", "source": str(pad), "mime": "image/heic"}]})
    assert out["results"]["h"]["location"] == AMSTERDAM


@pytest.mark.parametrize(
    ("tekst", "verwacht"),
    [
        ("+52.0907+005.1214/", (52.0907, 5.1214, None)),
        ("+52.0907+005.1214+010.000/", (52.0907, 5.1214, 10.0)),
        ("-33.8688+151.2093-002.5/", (-33.8688, 151.2093, -2.5)),
        ("+5205.442+00507.284/", (52 + 5.442 / 60, 5 + 7.284 / 60, None)),
        ("+520526.52+0050717.04/", (52 + 5 / 60 + 26.52 / 3600, 5 + 7 / 60 + 17.04 / 3600, None)),
        ("+95.0000+005.1214/", None),
        ("+52.0907+185.0000/", None),
        ("+00.0000+000.0000/", None),
        ("+5260.000+00507.284/", None),  # 60 minuten
        ("52.0907,5.1214", None),
        ("+52.0907+005.1214", (52.0907, 5.1214, None)),  # zonder / (zoals sommige telefoons)
        ("+52.0907+005.1214/" + "0" * 60, None),
        ("", None),
    ],
)
def test_iso_6709(tekst, verwacht):
    got = render_meta.iso6709(tekst)
    if verwacht is None:
        assert got is None
        return
    lat, lon, alt = verwacht
    assert got == {"lat": round(lat, 7), "lon": round(lon, 7), "alt": alt, "source": "video"}


def test_de_plek_van_een_video_uit_de_tags():
    """Uit het antwoord van meta: eerst de tag van Apple, dan location (©xyz van ffmpeg)."""
    apple = "com.apple.quicktime.location.ISO6709"
    probe = {"format": {"tags": {apple: "+52.0907+005.1214+001.0/", "location": "+10+010/"}}}
    assert render_meta.video_location(probe)["lat"] == 52.0907
    probe = {"format": {"tags": {}}, "streams": [{"tags": {"location-eng": "+10.5-020.25/"}}]}
    assert render_meta.video_location(probe) == {
        "lat": 10.5, "lon": -20.25, "alt": None, "source": "video"
    }  # fmt: skip
    for kapot in (None, {}, {"format": None}, {"format": {"tags": {"location": "geen plek"}}}):
        assert render_meta.video_location(kapot) is None


def make_video(path, location: str):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=duration=1:size=64x48:rate=5", "-pix_fmt", "yuv420p",
         "-metadata", f"location={location}", "-y", str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.mark.media
@pytest.mark.anyio
async def test_video_met_iso_6709_via_de_worker(client, admin, settings, tmp_path):
    """Een video met location (ISO 6709, zoals een telefoon hem schrijft): de worker leest de
    tags (meta), render_meta de plek, photometa schrijft blob_meta.gps = 1 en photo_geo met
    source video voor de eigenaar."""
    headers, tokens = admin
    media_on(settings)
    worker = await local.start(settings)
    try:
        film = make_video(tmp_path / "film.mp4", "+52.0907+005.1214/").read_bytes()
        node = await upload(client, headers, "film.mp4", film)
        await jobs.run_pending(settings)  # de preview; die zet photometa klaar (na DELAY)
        await jobs.run_pending(settings, at=jobs.now() + photometa.DELAY * 2)
    finally:
        await local.stop(worker)
    async with maker(settings)() as db:
        meta = (await db.scalars(select(BlobMeta))).one()
        geo = (await db.scalars(select(PhotoGeo))).one()
    assert meta.gps == 1
    assert (str(geo.node_id), str(geo.owner_id)) == (node["id"], tokens["user"]["id"])
    assert (geo.lat, geo.lon, geo.source) == (52.0907, 5.1214, "video")
    # De plek komt nooit in preview_meta (P6)
    r = await client.get(f"/api/v1/files/{node['id']}/preview", headers=headers)
    assert "52.09" not in json.dumps(r.json()) and "location" not in json.dumps(r.json())
