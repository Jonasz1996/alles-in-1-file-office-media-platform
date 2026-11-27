"""Sport: een eigen Strava-alternatief binnen het platform (vervangt Endurain).

- parse.py: GPX en TCX lezen (namespaces maken niet uit; hartslag, cadans en vermogen uit de
  extensies van Garmin en anderen).
- elevation.py: het hoogtefilter (GPS-uitval naar 0 en terug, losse pieken, ruis).
- track.py: van punten naar een activiteit: afstand, (bewegings)tijd, stijgen en dalen, splits
  per kilometer, beste tijden.
- pack.py: de punten compact in de database (zlib van vaste arrays; geen numpy in de app).
- service.py: importeren (een bestand, een zip, of een bestand uit Bestanden) en opvragen.
- tiles.py: de kaarttegels via de server (met cache), alleen als de beheerder een bron instelde.
"""
