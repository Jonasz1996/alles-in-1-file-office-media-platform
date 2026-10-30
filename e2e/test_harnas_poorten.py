"""Het harnas zelf, zonder browser of server: free_port geeft nooit twee keer dezelfde poort."""

from __future__ import annotations

from conftest import free_port


def test_free_port_geeft_nooit_twee_keer_dezelfde() -> None:
    """Office kiest drie poorten na elkaar (intern, http en https), en elke server er twee. Een
    poort die net weer dicht is, kan de kernel opnieuw geven: 500 keer kiezen gaf zonder de
    lijst van gegeven poorten bijna altijd een dubbele (gemeten: 200 keer in 72 % van de runs)."""
    ports = [free_port() for _ in range(500)]
    assert len(set(ports)) == len(ports)
