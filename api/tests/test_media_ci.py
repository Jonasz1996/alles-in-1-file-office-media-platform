"""De CI-job e2e-media eist H.264 en AAC (§4.8, review K2).

S13 draaide in de job e2e-media-perf (run 40787463797, op e35445c): Chrome for Testing
156.0.8078.4 zegt ja tegen avc1.640028 en mp4a.40.2 via MSE. Dan hoort e2e-media
MEDIA_REQUIRE_H264 op "1" te zetten: een test van H.264 of AAC die overslaat omdat de browser de
codec kwijt is, faalt dan, in plaats van CI groen te houden. Deze test leest ci.yml zoals
test_csp_nginx.py dat doet: als tekst, zonder yaml.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def job(name: str) -> list[str]:
    """De regels van één job (onder jobs:, twee spaties in), tot de volgende job."""
    lines = CI.read_text().splitlines()
    start = lines.index(f"  {name}:")
    out = []
    for line in lines[start + 1 :]:
        if re.match(r"^  \S", line):
            break
        out.append(line)
    return out


def env(name: str, key: str) -> str | None:
    for line in job(name):
        m = re.match(rf'^\s+{key}:\s*"([^"]*)"\s*$', line)
        if m:
            return m.group(1)
    return None


def test_e2e_media_eist_h264_en_aac_sinds_s13():
    assert env("e2e-media", "MEDIA_REQUIRE_H264") == "1"


def test_de_job_van_s13_bestaat_nog():
    """Zonder e2e-media-perf meet niemand meer of de browser van CI H.264 kan."""
    lines = job("e2e-media-perf")
    assert any("-m media_perf" in line for line in lines)
    assert any("S13" in line for line in lines)
