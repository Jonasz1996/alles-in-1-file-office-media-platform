"""Zoeken op wat er op een foto staat (bouwontwerp fase 6, X3; Keuzes 70–84).

De app rekent zelf nooit: de vectoren maakt en vergelijkt de container analyse (Op.EMBED_IMAGE,
Op.RANK; app/analyse/worker/ops/beeld.py). Hier:

- types.py: de vormen, de instellingen en het formaat van het bestand per eigenaar (alleen de
  standaardbibliotheek: de worker leest hetzelfde formaat);
- vecfile.py: dat bestand uit de rijen van blob_embed maken;
- jobs.py: de stap embed (rij analyse), en de jobs voor het bestand, voor uit en voor wissen; de
  rijen zelf (write_embed(), de enige schrijver van blob_embed, en de vragen van Keuze 69) staan
  in app/beeld_rows.py, buiten deze map, omdat ze ook de foto's in de prullenbak raken (zoals
  app/photo_rows.py);
- limits.py en cache.py: de grenzen en de cache van 60 s van een beeldvraag;
- search.py: GET /search?mode=image (Keuze 78, 79);
- admin.py: aan en uit (per gebruiker en voor iedereen), het blok op /beheer en wissen
  (Keuze 82).
"""
