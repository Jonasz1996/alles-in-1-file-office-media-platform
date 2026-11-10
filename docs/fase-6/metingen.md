# Metingen fase 6 (I1)

Dit is het skelet. Stap B van I1 vult het; de poorten staan in §4.4 en §4.5 van het
[bouwontwerp](bouwontwerp.md), de scripts in [e2e.md](e2e.md) en de uitleg bij de uitkomst in
[integratie.md](integratie.md).

## Kort

De poorten van §4.4 en §4.5 in één tabel, elk met gehaald of niet; volgt na stap B.

## Omstandigheden

De commit, de machine, de cores (`taskset`), de versies van SQLite op de host en in
`python:3.12-slim`, de DS, en dat er niets anders liep.

## M1: zoeken en foto's (§4.4)

De poorten van §4.4 uit het artefact van de job `zoek-perf` op de kop van I1 (zoeken, de
herinneringen, de kaart en de duplicaten), met de marge per poort.

## M2: de backfill van de tekst (§4.5)

`e2e/perf/zoek_backfill.py` met de worker (de poort) en met de terugval zonder media (een rapport).

## M3: photometa (§4.5)

GPS, camera en pHash per minuut, met het harnas van I1 (A2).

## M4: land en plaats (§4.5)

De punten per minuut met de echte bronnen, met het harnas van I1 (A2).

## M5: OCR (§4.5)

De poort van X1 (`python -m tests.ocr.poort`), per hoek en per dichtheid.

## M6: schrijven met kopieën (§4.5)

De langste wachttijd van een upload per run, op de host en in `python:3.12-slim`, na I1 (D1).

## M7: zoeken naast media en OnlyOffice (§4.5, deel B)

`e2e/perf/zoek_samen.py` in de maat van de sandbox, met elke poortregel van stap 2, de controle op
`OOMKilled` en het hoogste gebruik van het geheugen; de poorten van de beeldvraag en de container
`analyse` zijn 0.8.1.

## M8: rapporten

`zoek_ui.py`, `bench --los`, het bijwerken van de index uit CI, de grootte van het image, de duur
van `e2e-media-perf` en het blok `=== kort ===` uit CI.

## M9: plaatsen na de update (I1 (A4))

Of elke foto met GPS na de backfill een plaats en een land heeft.

## Gezichten en beeld (0.8.1)

De rijen van X2 en X3 in §4.5 (gezichten en beeld in de container) horen bij 0.8.1 en worden in I1
niet gemeten.

## Bevindingen

Wat de metingen lieten zien dat niet in een poort past, met de oorzaak en wat eraan gedaan is.

## Wat alleen de LXC of CI kan laten zien

`bench --los` op de CPU van Jonas, de kwaliteit van gezichten en beeld op echte foto's, de jobs met
het image en `--stappen`.

## Opnieuw meten

De opdrachten om elke meting opnieuw te doen; volgt na stap B.
