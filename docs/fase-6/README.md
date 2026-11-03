# Fase 6 — Zoeken en foto's

Status: **ontwerp** (bouwontwerp revisie 2, 3 november 2026). Er is nog niets gebouwd.

## Doel

Alles terugvinden, en meer halen uit je foto's. Uit het stappenplan:

- zoeken in de inhoud van pdf, docx, xlsx, pptx, txt en md;
- OCR voor scans en foto's met tekst;
- EXIF en GPS (datum, camera, plek), een kaart en plaatsnamen zonder internet;
- duplicaten en bijna-duplicaten;
- gezichten en objecten met lokale modellen, en zoeken op wat er op een foto staat;
- herinneringen ("vandaag, N jaar geleden") en slimme albums;
- filters op type, datum, persoon, plek, tag en bibliotheek.

Klaar als: een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms; OCR-tekst is
doorzoekbaar; gezichten zijn te benoemen en te zoeken.

## Hoe het werkt, in het kort

- Eén zoekindex in de SQLite die we al hebben (FTS5), bijgehouden door triggers. Geen nieuwe
  database, geen nieuwe dienst. Zoeken op naam werkt meteen na de update; de inhoud komt op de
  achtergrond.
- Je ziet alleen wat je mag zien: de index filtert op de mappen die jij mag lezen, en elke
  treffer wordt nog een keer gecontroleerd. Geen score en geen totaal, zodat de uitkomst niets
  zegt over bestanden van anderen.
- Tekst uit bestanden lezen gebeurt in de afgeschermde mediaworker van fase 5 (geen netwerk, geen
  data, een eigen gebruiker). Staat media uit, dan in een apart proces met grenzen, en zegt
  `/beheer` dat.
- Plek, personen en tekst uit een foto horen bij het bestand van de eigenaar. Alleen hij ziet ze,
  nooit via een publieke link, en een kopie bij een ander krijgt niets mee. Locatie "uit" wist
  alles.
- Een eigen kaart met alleen landsgrenzen, zonder tegels van buiten. Plaatsnamen en landen komen
  uit vaste bestanden die `install.sh` downloadt (GeoNames en Natural Earth).
- Een slim album deelt nieuwe foto's niet vanzelf; dat kies je apart.
- Bijwerken en terugrollen met hetzelfde ene `install.sh`, met een reservekopie van de database
  vooraf.

## Wat er komt

- **Z0 — Fundament en spike:** migratie 0009, de contracten, de regel "eigen en levend", de
  wachters, het harnas, en de spike (S1–S11), met `bench --los` om je CPU te meten.
- **Z1 — Zoekmachine:** de index, de vraag, het toegangsfilter, snippets, de tijdsgrens, grenzen
  voor de hele app.
- **Z2 — Tekst uit bestanden:** lezen in de worker, de backfill, de woorden per pagina voor de
  viewer.
- **Z3 — Foto-gegevens:** GPS, camera, plaatsen en landen, pHash, duplicaten, de kaart (API).
- **Z4 — Zoeken in de web:** het zoekveld, suggesties, `/zoeken`, de gemarkeerde woorden in de
  viewer.
- **Z5 — Kaart en duplicaten in de web**, en de waarschuwing bij delen van foto's met een plek.
- **Z6 — Personen, herinneringen, slimme albums en filters.**
- **Z7 — Beheer en uitrol:** de tab op `/beheer`, de reservekopie, terugrollen, het runbook.
- **X1 — OCR** (wacht op Vraag 1).
- **X2 — Gezichten automatisch** (wacht op Vraag 2; niet in fase 6).

## Planning

±26 werkdagen (±5 weken): Z0 en vier golven van twee pakketten, elk met een dag voor review, en
3 dagen integratie. Het stappenplan geeft 3 weken; dat past niet met review per golf. Daarom
Vraag 7. Na golf 2 (±dag 14) zijn zoeken, personen, herinneringen en slimme albums af.

Wat de sandbox laat zien, staat in [haalbaarheid.md](haalbaarheid.md): zoeken over 100.000
bestanden met p95 17–80 ms, tekst lezen voor 2,2 ms per PDF-pagina, OCR voor 2–3 s per pagina,
en het land via polygonen in 0,8 ms per foto.

## Vragen voor Jonas

1. Mag tesseract (±20 MB) in het image, voor OCR? Standaard: **nee, tot je ja zegt.** X1 wordt
   gebouwd en in CI getest; zonder ja is een scan alleen op naam te vinden.
2. Mogen gezichten automatisch gevonden worden (numpy en onnxruntime, ±120 MB, en twee modellen)?
   Let op: dat is biometrische data, ook van mensen op de foto die zelf niets kozen. Standaard:
   **nee**; personen benoem je met de hand.
3. Zoeken op wat er op een foto staat ("strand", "hond")? Standaard: **niet in fase 6.**
4. Wie ziet de plek van een foto? Standaard: **alleen de eigenaar**, en het deeldialoog
   waarschuwt dat een download de plek meeneemt. Anders: GPS uit downloads halen (±2 dagen), of
   "wie ik deel, ziet de plek ook".
5. Mogen GeoNames (CC BY 4.0) en de landsgrenzen van Natural Earth (publiek domein) bij de
   installatie gedownload worden? Standaard: **ja**, met sha256, niet fataal, uit te zetten met
   `PLAATSEN=uit`.
6. Wat ligt er onder de kaart? Standaard: **alleen landsgrenzen**, geen tegels van buiten.
7. Mag fase 6 ±5 weken duren in plaats van 3? Standaard: **ja, ±5 weken met alles.** Anders:
   ±4 weken zonder kaart, duplicaten en OCR, of 3 weken met alleen zoeken.

De vragen staan uitgebreid in het [bouwontwerp](bouwontwerp.md) onder "Vragen voor Jonas".

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend); revisie 1 in
  [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan als voorstel in B49–B60 in
  [beslissingen](../fase-0/beslissingen.md).
- [haalbaarheid.md](haalbaarheid.md): de metingen in de sandbox (H1–H8, de proef van ontwerp B, en
  V1–V10 bij revisie 2), met de commando's.
