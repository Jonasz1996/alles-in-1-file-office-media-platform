# Proefbestanden voor office

Zeven kleine documenten voor de tests van fase 4: de e2e (met de nep-DS en met de echte DS) en
de API-tests. Ze zijn één keer gemaakt met LibreOffice in de sandbox en staan zo in git.
CI maakt ze niet opnieuw.

| Bestand | Bron | Filter van LibreOffice | Bytes |
|---|---|---|---|
| `proef.docx` | `bron/proef.fodt` | `MS Word 2007 XML` | 5 861 |
| `proef.doc` | `bron/proef.fodt` | `MS Word 97` | 11 776 |
| `proef.rtf` | `bron/proef.fodt` | `Rich Text Format` | 4 868 |
| `proef.xlsx` | `bron/proef.fods` | `Calc MS Excel 2007 XML` | 5 623 |
| `proef.xls` | `bron/proef.fods` | `MS Excel 97` | 5 632 |
| `proef.pptx` | `bron/proef.fodp` | `Impress MS PowerPoint 2007 XML` | 10 413 |
| `proef.ppt` | `bron/proef.fodp` | `MS PowerPoint 97` | 462 336 |

`proef.ppt` is groot omdat LibreOffice er een miniatuur van 442 KB in zet (de stroom
`SummaryInformation`). Ingepakt in git is hij ongeveer 20 KB.

## Wat erin staat

De bronnen in `bron/` zijn platte ODF-bestanden (gewone XML), zodat een wijziging in een diff
te lezen is.

- **Tekst** (`proef.fodt`): de kop "Proefdocument van het platform", twee alinea's, een tabel
  Soort/Aantal (Appels 3, Peren 5) en een slotalinea. Tests zoeken op de kop:
  `docx_text(...)` moet "Proefdocument van het platform" bevatten.
- **Rekenblad** (`proef.fods`): blad "Blad1" met een titelrij, de kolommen Soort, Aantal en
  Prijs, de rijen Appels en Peren en een rij Totaal met `=SUM(...)`.
- **Presentatie** (`proef.fodp`): twee dia's, "Proefpresentatie van het platform" en "Tweede
  dia".

## Opnieuw maken

Alleen als de inhoud moet veranderen. Pas eerst de bron aan, dan (vanuit deze map, met een
eigen profiel zodat een draaiende LibreOffice niet meedoet):

```sh
profiel=$(mktemp -d)
lo() { soffice -env:UserInstallation="file://$profiel" --headless --convert-to "$1" --outdir . "$2"; }
lo "docx:MS Word 2007 XML" bron/proef.fodt
lo "doc:MS Word 97" bron/proef.fodt
lo "rtf:Rich Text Format" bron/proef.fodt
lo "xlsx:Calc MS Excel 2007 XML" bron/proef.fods
lo "xls:MS Excel 97" bron/proef.fods
lo "pptx:Impress MS PowerPoint 2007 XML" bron/proef.fodp
lo "ppt:MS PowerPoint 97" bron/proef.fodp
rm -rf "$profiel"
```

Gemaakt met LibreOffice 24.2.7.2 (`/usr/bin/soffice`). De meldingen "failed to launch javaldx"
zijn onschuldig. Daarna controleren dat de app ze aanneemt (`app.office.inspect.check` geeft
voor alle zeven ok) en de tests draaien. Een nieuwe LibreOffice geeft andere bytes; dat is
geen probleem, zolang de inhoud hetzelfde blijft.

sha256 van deze versie:

```
99e0e9d32bbe109be56cb964a064869eee369d47facd9ce69ecd3042dec17218  proef.docx
8e2ac60db18ce16ede6f93256b74b8e444ee3d6b3c667b88bb96ee7193fd9f05  proef.doc
e2f4c080d5841aa1fe9ccd5b4d0eb37d8a5a5bd68d8f1c661c7b07790875a6e5  proef.rtf
28ec38aac3442a2fb5b8fa100ac16efb62de66a57b68f8e08ce872f7d160bcad  proef.xlsx
16539e63a6e76223ef7b5e49f016ffe242233311865cbe6149c2905715e6a730  proef.xls
465a28d1a949d26412d1fcf4bad64b7fde67b1d6ca241f7133ba960f6963c940  proef.pptx
ab7f0a77cd9155adf1175dc18d89a4c897df2963e2fa96273ddb1dbe0042f275  proef.ppt
```
