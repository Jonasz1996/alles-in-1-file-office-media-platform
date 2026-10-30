# Toetsenbord — P3 (Uploads)

Alle sneltoetsen staan via `useShortcuts` in een scope, en dus ook in het overzicht onder `?`
(ShortcutHelp). Een toets van één letter werkt niet zolang de focus in een tekstveld staat.

## Sneltoetsen

| Toets | Scope | Actie | Waar |
| --- | --- | --- | --- |
| `U` | `browser` | Bestanden uploaden (opent de bestandskiezer) | `upload/UploadButtons.tsx` |
| `Shift+U` | `browser` | Een map uploaden (bestandskiezer met `webkitdirectory`) | `upload/UploadButtons.tsx` |
| `Shift+P` | `global` | Alle uploads pauzeren, of (als alles gepauzeerd is) hervatten | `upload/UploadTray.tsx` |
| `V` / `R` (en) | `upload-conflict` | Vervangen: het bestaande bestand wordt een vorige versie | `upload/ConflictDialog.tsx` |
| `B` / `K` (en) | `upload-conflict` | Beide houden: het nieuwe bestand krijgt een nummer | `upload/ConflictDialog.tsx` |
| `O` / `S` (en) | `upload-conflict` | Overslaan: niet uploaden | `upload/ConflictDialog.tsx` |
| `Esc` | (dialoog) | De vraag sluiten: er wordt niets geüpload ("Upload geannuleerd") | `upload/ConflictDialog.tsx` |

- `U` en `Shift+U` werken alleen als je in die map mag uploaden (`enabled: canUpload`) en
  op de pagina Bestanden (die zet de scope `browser`).
- `Shift+P` staat in `global`: hij werkt op elke pagina zolang de uploadlade er staat, en niet
  zolang er een dialoog of menu open is (die scopes blokkeren).
- De letters in de vraag over dubbele bestanden hangen af van de taal (nl: V/B/O, en: R/K/S)
  en staan op de knoppen zelf (`aria-keyshortcuts`). De scope `upload-conflict` ligt bovenop
  de scope `dialog` van de dialoog.

## Zonder sneltoets, met Tab

- **Werkbalk:** "Uploaden" en "Map uploaden" zijn gewone knoppen (Enter of spatie).
- **Uploadlade** (`<section aria-label="Uploads">`, rechtsonder): een gewone tabvolgorde.
  - Inklappen/uitklappen (`aria-expanded`).
  - Alles pauzeren / Alles hervatten / Alles annuleren / Mislukte weggooien.
  - Per bestand: Pauzeren / Hervatten / Annuleren, en bij een mislukte upload Opnieuw (als dat
    kan) en Weggooien. De knoppen hebben de bestandsnaam in hun naam ("Pauzeren: a.txt").
  - "Alles tonen" opent een dialoog met alle uploads in een gevirtualiseerde lijst; `Esc`
    sluit hem.
  - De samenvatting ("1.234 van 5.000 · 2,1 GB van 6 GB · 12 MB/s · nog ~6 min") staat in
    een `aria-live="polite"`-gebied, hooguit 4 keer per seconde bijgewerkt.
- **Onderbroken uploads** (in de lade, na herladen): "Kies {naam} opnieuw" opent de
  bestandskiezer; "Weggooien" haalt de upload van de server; "Later" verbergt de lijst. Een
  verkeerd bestand (andere naam of grootte) geeft een melding met `role="alert"`.
- **Vraag over dubbele bestanden:** de focus begint op "Beide houden" (de veilige keuze).
  Het vakje "Voor allemaal (n)" is met Tab en spatie te bereiken.

## Getest in

`e2e/test_p3_upload.py`:

- `U` → bestandskiezer zonder `webkitdirectory` (`test_uploaden_met_u`);
- `Shift+U` → bestandskiezer met `webkitdirectory` (`test_mapupload_met_shift_u_…`);
- `Shift+P` hervat en pauzeert alles (`test_pauzeren_en_hervatten`);
- `B` en dan `O` in de vraag, en `Esc` sluit hem zonder upload
  (`test_dubbele_bestanden_een_vraag_en_geen_tus_ervoor`);
- de knoppen in de lade (Pauzeren, Hervatten, Weggooien, Kies opnieuw) worden op hun
  toegankelijke naam gevonden.
