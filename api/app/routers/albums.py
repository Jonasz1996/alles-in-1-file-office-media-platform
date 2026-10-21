"""Albums: een node met kind "album" plus de tabellen album en album_item.

Stub van P0: de router staat al in main.py, zonder routes; P5 vult hem.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["foto's"])
