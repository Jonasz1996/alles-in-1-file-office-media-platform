# Toetsenbord — X1 (tekst in beeld)

X1 maakt tekst in scans en foto's doorzoekbaar ([bouwontwerp.md](bouwontwerp.md), "X1 — OCR";
Keuzes 20, 22–25a). In de web komen drie dingen bij: de rij "Tekst in beeld" in het infopaneel
van de viewer, het vinkje "Tekst in foto's herkennen" onder Account, en de markering van de
zoekvraag in de tekst van een foto. Er komt geen nieuwe sneltoets bij: `i` (het infopaneel),
Mod+K (het veld bovenaan), Tab, Enter, Spatie en Esc bestonden al. *Mod* is Ctrl, op een Mac ⌘.

`e2e/test_zoek_ocr.py` doet de stappen hieronder met alleen Ctrl+K, typen, Tab, Enter, Spatie,
`i` en Esc, met de echte tesseract. Nergens een klik. Wat er gebouwd is: [x1.md](x1.md).

## De rij "Tekst in beeld" (infopaneel)

De rij staat bij een foto of een PDF, ingelogd, bij de huidige versie, en alleen voor wie
"Tekst herkennen" mag vragen: bij een foto de eigenaar, bij een PDF wie mag schrijven
(Keuze 23 en 25a). Wie dat niet mag, ziet de rij niet (de tekst van een PDF vindt hij wel met
Zoeken). Via een publieke link is er geen rij.

| Toets | Wat |
|---|---|
| `i` in de viewer | het infopaneel open of dicht (zoals sinds fase 3) |
| Tab | door het paneel, tot de knop "Tekst herkennen" (nog niet gelezen) of "Opnieuw herkennen" (al gelezen, niets gevonden of mislukt) |
| Enter of Spatie op de knop | vraagt het (`POST /files/{id}/ocr`); de zin erboven wordt "Wacht op tekstherkenning." en een melding zegt "Tekst herkennen staat in de wachtrij." |

- De knop blijft staan terwijl hij wacht (`aria-disabled`), zodat de focus niet verdwijnt. Nog
  een keer Enter doet dan niets. Is de tekst er (het bericht `search.ready` voor dit bestand), dan
  wordt de zin "Tekst gevonden in deze foto. Alleen jij vindt hem met Zoeken." en heet de knop
  "Opnieuw herkennen"; de focus staat er nog op. De e2e controleert dat.
- De zin staat in een `role="status"`: een schermlezer leest elke nieuwe stand voor.
- Een fout (al bezig, te vaak gevraagd, OCR staat uit, al genoeg tekst) staat onder de knop in een
  `role="alert"`, en de knop verwijst ernaar met `aria-describedby`.
- Zonder knop: bij een PDF met al genoeg tekst ("Dit document heeft al genoeg tekst; tekst
  herkennen is niet nodig.") en als tesseract niet in het image zit ("Tekst herkennen is niet
  geïnstalleerd op deze server.").

## "Tekst in foto's herkennen" (Account, blok Foto's)

Een gewoon vinkje in het blok Foto's onder Account, na "Herinneringen". Tab erheen, Spatie zet het
aan of uit; een melding zegt "Opgeslagen.", de focus blijft op het vinkje. Aan: de app leest op de
achtergrond al je foto's, en elke nieuwe foto (Keuze 23). De uitleg onder het vinkje zegt dat de
tekst bij jouw foto's blijft, ook als je een foto deelt. Een fout van de API staat in de
`role="alert"` van het blok, zoals bij de andere vinkjes (Z6).

## Van /zoeken naar de markering in een foto

| Toets | Wat |
|---|---|
| Mod+K, typen, Enter | naar `/zoeken?q=…` (Z4); een foto of scan die het woord alleen in het beeld heeft, staat erbij met het woord in `<mark>` in de snippet |
| Tab naar de treffers, Enter | de viewer met `?bestand=` naast `?q=` |

- **Een foto**: de woorden van de vraag krijgen een kader op hun plek in de foto. De laag heeft
  dezelfde maat en transform als de foto, dus zoomen (+, −, 0), schuiven en draaien (r) nemen hem
  mee. De laag zelf is `aria-hidden`; in een `role="status"` staat "1 woord van je zoekvraag
  gemarkeerd in de foto" (of "Niet gevonden in de tekst van deze foto").
- **Een scan (PDF)**: de tekstlaag van Z4 met de woorden van OCR; het werkt zoals bij een PDF met
  tekst ([toetsenbord-z4.md](toetsenbord-z4.md)): de viewer gaat naar de eerste pagina met het
  woord en markeert het.
- Esc sluit de viewer; de focus staat weer op de treffer.
- Alleen de eigenaar krijgt de woorden van een foto. Een ander (ook met een deling of een kopie)
  krijgt 404 op de woorden en ziet geen kader; de e2e controleert dat met de kopie van Bram.

## Het blok OCR op /beheer (golf 4, met Z7)

In de tab "Zoeken en foto's" (Alt+9, [toetsenbord-z7.md](toetsenbord-z7.md)) staat het blok OCR
met drie regels (Geïnstalleerd, Aan, In de rij) en, als de worker OCR heeft, twee knoppen. Zonder
OCR in de worker staat er "OCR: niet geïnstalleerd." met waarom, en geen knoppen.

| Toets | Wat |
|---|---|
| Tab naar "OCR uitzetten", Enter | OCR uit (`PATCH /admin/search`, `ocr: false`), zonder vraag vooraf; de knop heet nu "OCR aanzetten" (`aria-pressed="false"`), een melding zegt "OCR staat uit.", de focus blijft op de knop |
| Enter op "OCR aanzetten" | de vraag "OCR aanzetten?" met de focus op Annuleren; Esc sluit zonder aanroep (focus terug op de knop); Tab naar "Aanzetten" en Enter zet OCR aan ("OCR staat aan.") |
| Tab naar "Alles opnieuw herkennen", Enter | de vraag "Alles opnieuw herkennen?" met de focus op Annuleren; Esc doet niets; Tab naar "Starten" en Enter (`POST /admin/search/rebuild`, `what: ocr`, 202): "Het opnieuw herkennen is gestart." |
| "Achtergrondwerk pauzeren" (bovenaan de tab) | ook de rij ocr staat stil: een scan of foto blijft "In de rij" tot je hervat |

## Nagelopen

Met alleen het toetsenbord, in de e2e (lokaal Chromium 141, in de job `ocr` van CI Chrome
Headless Shell):

- De knop "Tekst herkennen" is te bereiken met Tab vanaf de viewer; na Enter blijft de focus op
  dezelfde knop tot hij "Opnieuw herkennen" heet (`test_een_foto_van_een_bon_herkennen_vinden_en_markeren`).
- Na Esc in de viewer staat de focus weer op de treffer in `/zoeken`
  (`test_een_scan_zonder_tekstlaag_vinden_en_in_de_viewer_markeren` en
  `test_een_foto_van_een_bon_herkennen_vinden_en_markeren`).
- Het kader van een foto ligt op het woord: het midden binnen 2% van de breedte en de hoogte,
  bij een foto met EXIF-draaiing 6 (de e2e).
- Het vinkje "Tekst in foto's herkennen" is te bereiken met Tab onder Account; Spatie zet het aan
  (PATCH 200, `ocr_photos` true), een nieuwe foto wordt dan vanzelf gelezen en gevonden, en
  Spatie zet het weer uit met de focus nog op het vinkje
  (`test_tekst_in_foto_s_herkennen_met_het_toetsenbord`). De vitest-check op de bron
  (`ocr.test.ts`) houdt het vinkje vast aan `ocr_photos`.
- Het blok OCR op /beheer (golf 4,
  `test_ocr_op_beheer_pauzeren_uitzetten_en_opnieuw_herkennen`): Alt+9, de stand (ja, ja, 0 in de
  rij), pauzeren met Enter (een foto blijft in de rij en de tab telt hem), hervatten (hij wordt
  gelezen), "OCR uitzetten" (de focus blijft op de knop; `POST ocr` geeft 503 `ocr_off`), "Alles
  opnieuw herkennen" (Esc doet niets, Starten zet de scan terug in de rij) en "OCR aanzetten" (Esc
  doet niets, Aanzetten; de scan wordt binnen 90 s gelezen, niet pas na de vijf minuten van de
  pomp die voor "uit" weg stond).
