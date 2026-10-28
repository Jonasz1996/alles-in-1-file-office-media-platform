# Fase 5 — Media en streaming

Status: **ontwerp** (bouwontwerp revisie 2, 28 oktober 2026). Er is nog niets gebouwd.

## Doel

Films, series en muziek afspelen op elk apparaat, met zo weinig mogelijk omzetten. Uit het
stappenplan:

- bibliotheken met een scanner en het lezen van bestandsnamen;
- metadata (TMDB, MusicBrainz), posters, en zelf koppelen en corrigeren;
- per apparaat kiezen: direct afspelen, remux of transcode;
- een worker voor ffmpeg, met HDR naar SDR;
- sessies met een eigen cache, en opruimen na afloop;
- ondertitels, sprites voor de zoekbalk, en hoofdstukken;
- kijkvoortgang, "Verder kijken" en de volgende aflevering;
- muziek, met albums, artiesten, afspeellijsten en gapless.

Klaar als: een 1080p-film start binnen 2 s (direct play) en 5 s (transcode); zoeken werkt;
3 gelijktijdige transcodes zonder haperen; voortgang synchroniseert tussen apparaten.

## Hoe het werkt, in het kort

- Een film is een gewoon bestand in de boom. Een bibliotheek is een map met een soort erop.
  Een bestaande verzameling komt binnen via een import uit `/data/import`.
- ffmpeg draait alleen in een eigen worker-container: geen netwerk, geen data, een eigen
  gebruiker (uid 10002). De app geeft hem één open bestand en een pipe terug.
- De browser meet zelf wat hij kan. De app kiest dan direct, remux of transcode. Direct en remux
  kosten bijna geen CPU; dat is de normale weg.
- Transcode maakt segmenten van precies 2 s. Een eigen speler in het web (geen hls.js), en native
  HLS voor Safari en iOS.
- Alleen de CPU. Het platform meet zelf hoe snel die is (`bench`), en laat alleen toe wat past.
  Een GPU is klaargezet, niet gebouwd (B5 is open).
- Bijwerken en terugrollen met hetzelfde ene `install.sh`. Lukt iets met media niet, dan werkt
  alles van fase 4 gewoon door.

## Wat er komt

- **M0 — Fundament en spike:** migratie 0008, de contracten, de kern van de worker, de reparaties
  aan downloads (F1–F4), de rijen voor achtergrondwerk, een nep-worker en nep-providers voor de
  tests, en de spike in CI vanaf de eerste dag.
- **M1 — Worker en isolatie:** de worker-container, de afscherming van ffmpeg, en de previews
  van video's via de worker.
- **M2 — Bibliotheken:** de scanner, de eigen parser voor bestandsnamen, groeperen, NFO, en de
  import.
- **M3 — Afspelen:** het profiel van de client, de beslissing, de sessies en de toegang per
  verzoek.
- **M4 — Stromen:** de producent, de knipper, de playlists, de toelating en de cache.
- **M5 — Ondertitels, sprites en hoofdstukken.**
- **M6 — De speler:** `/kijken`, met toetsenbord, ondertitels en zoeken.
- **M7 — Voortgang:** "Verder kijken" en synchroniseren via SSE.
- **M8 — Metadata online:** TMDB en MusicBrainz, met een eigen sleutel, en zelf koppelen.
- **M9 — Muziek en gapless.**
- **M10 — Beheer en uitrol:** het tabblad Media op `/beheer`, de belastingtest, en `install.sh`.

## Planning

±22 werkdagen: M0 ±6, de parallelle golf ±9, de integratie ±3, en ±4 dagen marge voor wat de
spike en de LXC laten zien. Het stappenplan geeft 5 weken (25 dagen). Dat past, krap. Het
ontwerp zegt wat als eerste schuift als het uitloopt (§5).

Wat de sandbox laat zien, staat in [haalbaarheid.md](haalbaarheid.md): de afscherming van de
worker, de kosten van de bron, de tijden van fMP4, en de eerste start op 3 cores.

## Vragen voor Jonas

1. Staan er al films, series of muziek op `/tank`, en waar? Standaard: we bouwen de import, en
   hij kopieert. Verplaatsen kan als de bron onder `/tank/platform/import` staat; daarvoor moet
   jij op de Proxmox-host `chown -R 110001:110001 /tank/platform/import` doen.
2. Welke CPU, en is er een GPU (B5)? Standaard: alleen de CPU. Graag de uitvoer van `lscpu` en
   `ls -l /dev/dri` op de Proxmox-host.
3. "3 gelijktijdige transcodes": drie op gemengde kwaliteit, of hooguit twee op 1080p?
   Standaard: drie (de eerste 1080p, de andere twee 720p). Een 4K-bron telt dubbel.
4. Heb je een TMDB-sleutel? Standaard: geen, alleen lokale metadata.
5. Mag een publieke link transcoderen? Standaard: nee, alleen direct en remux.
6. Profielen onder één account (bijvoorbeeld voor kinderen)? Standaard: nee, voortgang per
   account. De kolom `profile` staat klaar.

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend); revisie 1 in
  [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan als voorstel in B39–B48 in
  [beslissingen](../fase-0/beslissingen.md).
- [haalbaarheid.md](haalbaarheid.md): de metingen in de sandbox bij revisie 2 (V1–V8), met de
  commando's.
