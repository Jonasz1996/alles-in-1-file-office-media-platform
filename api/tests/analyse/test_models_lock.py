"""models.lock en de licenties (fase 6, A0; Keuze 58, K1-13): het formaat, de lezer, en de twee
regels van YuNet en SFace. install.sh (X2a) leest hetzelfde bestand met `read -r`, de worker met
lock.parse."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.analyse import lock

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
SHA = "a" * 64


def test_de_twee_modellen_van_gezichten():
    files = lock.load()
    assert [(f.name, f.size, f.sha256) for f in files] == [
        (
            "face_detection_yunet_2026may.onnx",
            229738,
            "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
        ),
        (
            "face_recognition_sface_2021dec.onnx",
            38696353,
            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        ),
    ]
    base = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{COMMIT}/models/"
    assert [f.url for f in files] == [
        base + "face_detection_yunet/face_detection_yunet_2026may.onnx",
        base + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ]
    # De vingerafdruk van precies deze twee regels (X2: LOCK_SHA van de gezichten)
    assert lock.digest(files) == "c34829c2524b2d619166d71855cfcb18d74cda83e0fa0441983f4b7a61a73d60"


def test_opmerkingen_lege_regels_en_een_eigen_bestand():
    text = (
        "# kop\n"
        "\n"
        f"{SHA} 12 a.onnx https://example.org/a.onnx   # na de velden\n"
        f"   {SHA}\t7\tsiglip2-b32-256/model.onnx -\n"
        "#" + SHA + " 1 weg.onnx -\n"
    )
    files = lock.parse(text)
    assert [(f.name, f.size, f.url) for f in files] == [
        ("a.onnx", 12, "https://example.org/a.onnx"),
        ("siglip2-b32-256/model.onnx", 7, None),
    ]
    assert files[1].line() == f"{SHA} 7 siglip2-b32-256/model.onnx -"
    assert lock.parse("") == [] and lock.parse("# alleen een opmerking\n") == []


FOUT = [
    (f"{SHA} 12 a.onnx", "3 velden"),
    (f"{SHA} 12 a.onnx - extra", "5 velden"),
    (f"{SHA.upper()} 12 a.onnx -", "sha256"),
    (f"{SHA[:-1]} 12 a.onnx -", "sha256"),
    (f"{SHA} 0 a.onnx -", "bytes"),
    (f"{SHA} 012 a.onnx -", "bytes"),
    (f"{SHA} -1 a.onnx -", "bytes"),
    (f"{SHA} 1e3 a.onnx -", "bytes"),
    (f"{SHA} 12 ../a.onnx -", "naam"),
    (f"{SHA} 12 /etc/a.onnx -", "naam"),
    (f"{SHA} 12 a//b.onnx -", "naam"),
    (f"{SHA} 12 map/./a.onnx -", "naam"),
    (f"{SHA} 12 a$b.onnx -", "naam"),
    (f"{SHA} 12 a.onnx http://example.org/a.onnx", "url"),
    (f"{SHA} 12 a.onnx ftp://example.org/a.onnx", "url"),
    (f"{SHA} 12 a.onnx https://", "url"),
]


@pytest.mark.parametrize(("line", "why"), FOUT, ids=[f"{i}-{w}" for i, (_, w) in enumerate(FOUT)])
def test_een_regel_die_niet_klopt(line, why):
    with pytest.raises(lock.LockError) as exc:
        lock.parse(f"# kop\n{line}\n")
    assert exc.value.line == 2
    assert why in str(exc.value) and "regel 2" in str(exc.value)


def test_een_naam_staat_er_hooguit_een_keer_in():
    with pytest.raises(lock.LockError) as exc:
        lock.parse(f"{SHA} 1 a.onnx -\n{'b' * 64} 2 a.onnx -\n")
    assert exc.value.line == 2


def test_de_vingerafdruk_van_gezichten_blijft_als_x3_regels_toevoegt():
    """X3 zet zijn regels erbij; de versie van de gezichten hangt alleen van de eigen af."""
    text = lock.LOCK_PATH.read_text()
    faces = lock.parse(text)
    more = lock.parse(text + f"{SHA} 99 siglip2-b32-256/vision.onnx -\n")
    assert len(more) == 3
    assert lock.digest(more[:2]) == lock.digest(faces)
    assert lock.digest(more) != lock.digest(faces)


def test_bash_leest_hetzelfde_bestand():
    """install.sh: `while read -r sha bytes naam url rest`. Een regel die met # begint of leeg
    is, slaat hij over; rest is leeg of een opmerking."""
    script = r"""
while read -r sha bytes naam url rest; do
  case "$sha" in ''|'#'*) continue ;; esac
  case "$rest" in ''|'#'*) ;; *) echo "FOUT $naam"; exit 1 ;; esac
  printf '%s %s %s %s\n' "$sha" "$bytes" "$naam" "$url"
done < "$1"
"""
    out = subprocess.run(
        ["bash", "-c", script, "bash", str(lock.LOCK_PATH)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert out.stdout.splitlines() == [f.line() for f in lock.load()]


def test_de_licenties():
    names = sorted(p.name for p in lock.LICENSES.iterdir())
    assert names == ["sface-Apache-2.0.txt", "yunet-MIT.txt"]
    mit = (lock.LICENSES / "yunet-MIT.txt").read_text()
    assert mit.startswith("MIT License\n")
    assert "Copyright (c) 2020 Shiqi Yu" in mit
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in mit
    apache = (lock.LICENSES / "sface-Apache-2.0.txt").read_text()
    assert "Apache License" in apache and "Version 2.0, January 2004" in apache
    assert "END OF TERMS AND CONDITIONS" in apache
    text = lock.LOCK_PATH.read_text()
    assert "MIT, Copyright (c) 2020 Shiqi Yu" in text and "Apache-2.0" in text


def test_lock_en_licenties_gaan_mee_in_het_image():
    """COPY api/app ./app neemt ze mee; .dockerignore sluit ze niet uit."""
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert "api/app" not in ignore and not any("lock" in p or "licent" in p for p in ignore)
    assert "COPY api/app ./app" in (ROOT / "Dockerfile").read_text()
