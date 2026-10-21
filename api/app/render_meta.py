"""Metadata van foto's en video's lezen (opnamedatum, camera, objectief, belichting), in het
renderproces (app/render.py). Bovenaan alleen de standaardbibliotheek, zoals render.py.

Stub van P0; P5 vult hem in (zie docs/fase-3/bouwontwerp.md, P5). Tot dan leest hij niets:
VERSION 0, en lege resultaten. GPS wordt nooit gelezen.

- image_meta(img) -> {"taken_at", "taken_kind": "local", "exif": {...}} voor een geopende
  Pillow-afbeelding;
- video_meta(probe) -> {"taken_at", "taken_kind": "utc" | "local"} uit de JSON van ffprobe;
- run_meta(request) -> {"state": "ready", "results": {sha: meta}} voor mode "meta": alleen de
  headers van veel bestanden in één proces.
"""

from typing import Any

# Omhoog bij elke wijziging in wat er gelezen wordt: de backfill (app/photometa.py) leest dan
# alles met een lagere Blob.meta_version opnieuw
VERSION = 0


def image_meta(img: Any) -> dict[str, Any]:
    return {}


def video_meta(probe: dict[str, Any]) -> dict[str, Any]:
    return {}


def run_meta(request: dict[str, Any]) -> dict[str, Any]:
    return {"state": "ready", "results": {}}
