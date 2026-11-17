"""OCR: tekst uit scans en foto's van tekst (bouwontwerp fase 6, X1; Keuze 22–24).

Wat hier staat, draait niet zelf de herkenning: dat doet alleen de mediaworker (Op.OCR in
app/media/worker/ops/ocr.py). Hier:
- pgm.py: het grijze beeld tussen kind 1 (app.render ocr_page) en de worker: de kop, de grens
  op de pixels, en draaien in stappen van 90° (alleen de standaardbibliotheek);
- deskew.py: de eigen rechtzetting van kind 1 (Pillow, projectie over ±12°);
- geometry.py: de middens van de vakjes terug naar het beeld zoals de viewer het toont.
"""
