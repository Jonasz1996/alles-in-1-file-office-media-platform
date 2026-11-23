# P6 docs-besluiten: de keuzes van fase 6 besloten (0.8.2)

Branch `wip/i3-docs-besluiten`, vanaf `debd1d96`. Alleen docs; geen code, geen test, geen CI.

Op 17 november 2026 koos Jonas "Standaard vastzetten": elke keuze van fase 6 die nog geen antwoord
had, staat op de standaard van het bouwontwerp. Dit pakket zet dat in de docs. De vaste tekst is
**besloten: standaard, Jonas 2026-11-17**. Ook staat er nu dat PR #2 is samengevoegd.

## Wat er veranderde

| Bestand | Wat |
|---|---|
| `docs/fase-0/beslissingen.md` | B49–B52, B54–B56, B58–B71: de status begint met de vaste tekst, met de reden ("Standaard vastzetten") en wat er gebouwd is. B53 en B57 waren al besloten; alleen hun zin "de uitwerking is voorstel" is aangepast. De alinea's over fase 6 onder de tabel zijn herschreven: wat besloten is, wat dat betekent, en hoe het zo kwam. B39–B48 (fase 5) en de alinea daarover zijn niet aangeraakt. |
| `docs/fase-6/bouwontwerp.md` | De kop, de tabel "Wat Jonas koos" (Vraag 4), "Vragen voor Jonas" (Vraag 4 en 8–14 elk met de vaste tekst; bij Vraag 8 het gevolg voor de twee eisen zonder gereedschap), de vier notities over tags, de checklist van X2 (kwaliteit), §4.0 (geen wachten meer op Vraag 8 en 12, geen fase 6b), "Wat een ander antwoord had gekost", R15 en R21. |
| `docs/fase-6/README.md` | De status bovenaan, met PR #2; "Wat je koos"; Bekende grenzen; "Gezichten in detail"; "Vragen voor Jonas": Vraag 4 en 8–14, de vragen bij 0.8.0 (ook het haperen: tellen zoals nu) en bij 0.8.1; Planning; Documenten. |
| `docs/fase-6/integratie-2.md` | "Vragen voor Jonas" (vraag 3 en 4 zonder "blijft open" of "tot Jonas kiest"), de slotalinea, twee verwijzingen in Bekende grenzen en de review, en een nieuw stuk "Na I2: PR #2 en de besluiten". |
| `docs/stappenplan.md` | Fase 6: de alinea's van 0.8.0 en 0.8.1, een alinea "Besluiten (17-11-2026)" met PR #2, Vraag 4, 11, 13 en 14. |
| `README.md` | Bekende grenzen (Vraag 8, BZ6 naast de scan, 36 B) en de regel van fase 6 in de tabel. |
| `docs/runbooks/gezichten.md` | Het gereedschap voor de twee eisen wacht niet meer op Vraag 8. |
| verslagen in `docs/fase-6/` | Open punten en koppen die een keuze "open" of "voorstel" noemden: doorgehaald, met een noot met de vaste tekst (`x2a.md`, `x3b.md`, `z0-web.md`, `z0.md`, `z0-api.md`, `z0b.md`, `z0b-api.md`, `z3.md`, `z6.md`, `integratie.md`, `metingen.md`, `x2b.md`). |

### Verouderde zinnen hersteld

- `x3b.md`, "Wat open blijft na I2": de eerste beeldvraag na een start valt niet meer terug. U1
  (`4aa42e1`) laadt het tekstmodel bij het opwarmen; na een herstart kost dat één keer ±1,7 s.
- `metingen.md`: BZ7 met de echte tokenizer is gedraaid (run 43981579405 op `ae163ac`: 0,107 s,
  32,4 MB erbij). B71 heet geen voorstel meer. Bevinding 3 en 12 en de twee rijen zonder
  gereedschap zeggen wat besloten is.
- `z3.md`: `/over` bestaat sinds 0.8.1. De twee vragen (groepen van duplicaten, de kaart met een
  filter in 0,5 s) zijn besloten.
- `z0b-api.md`: `faces_too_many` staat in de API (`_too_many` in `routers/people_faces.py`), maar
  de validatie van `FacesIn` (`max_length`) antwoordt eerst; de code komt dus nooit. Dat stond al
  in `z0b.md`, Open 2. Het punt zelf blijft open (code, geen keuze).
- `z7.md`: `test_faces_backup.py` bestaat en test beide wegen (`9c7438e`).
- `z6.md`: `photo_pref.beeld` kwam met 0012; Vraag 4 is besloten.
- `x2b.md`: samenvoegen met X3b gebeurde in golf 6 (`aa21e37`, `dcafcea`); laag 4 komt niet.
- `bouwontwerp.md` §4.0: "Schuift X3 naar een fase 6b" is weg; X3 zit in 0.8.1 (Keuze 85).

## Vraag 8 is nee: wat dat betekent

De twee eisen "≥ 95% van de portretten precies het goede gezicht" en "kleurvolgorde ≥ 0,05 hoger"
blijven bij besluit **niet gemeten**. `analyse-kwaliteit` meet die twee niet: het meet de
precisie van de voorstellen, de zuiverheid en de vangst van de groepen. Dat meet alleen Jonas, op
de LXC, tegen zijn eigen ja en nee. Zo staat het nu in de README's, het bouwontwerp, `metingen.md`,
`integratie-2.md`, `x2a.md`, `x2b.md` en het runbook `gezichten.md`.

## Afwijkingen van het plan

1. **B53 en B57.** Het plan zei: niet aanraken. Hun status ("besloten (Jonas, 2026-11-03)") is
   gebleven; alleen de staart "de uitwerking is voorstel" zegt nu dat de uitwerking ook besloten
   is. Anders bleef "voorstel" staan bij twee besloten kaarten.
2. **Meer bestanden dan in het plan.** De grep vond ook open claims in `integratie.md`, `z0.md`,
   `z0-api.md`, `z0b.md`, `z0b-api.md` en in de rij "Gezichten: de kwaliteit" van de README van
   fase 6. Die zijn meegenomen, met een doorhaling of een noot, zodat de geschiedenis leesbaar
   blijft.
3. **Geen eigen linkcontrole in CI.** Het repo heeft geen check die alle Markdown-links nakijkt.
   De docs-controles van CI staan in `infra/single/tests/test_install_search.sh` en
   `test_install_media.sh` (die lezen de README, §3.10 van het bouwontwerp en de runbooks). Die
   twee zijn gedraaid. Daarnaast is een kleine linkcontrole gedraaid (hieronder), alleen in de
   sandbox; hij staat niet in het repo.

## Controles

| Wat | Commando | Uitslag |
|---|---|---|
| Docs-controles van CI (job `infra`) | `bash infra/single/tests/test_install_search.sh` | `Alle 209 controles geslaagd.` |
| | `bash infra/single/tests/test_install_media.sh` | `Alle 129 controles geslaagd.` |
| Relatieve links en ankers in elk gewijzigd `.md`-bestand (ook dit) | `python3 -I links.py . $(git diff --name-only debd1d96 -- '*.md')` | `550 relatieve links in 21 bestanden, 0 kapot` |
| Geen fase-6-keuze meer "open" of "voorstel" | de grep hieronder | 0 treffers in de beslisdocumenten; 51 andere, elk verklaard |

De linkcontrole volgt elke Markdown-link die niet met een schema begint (`http:` enzovoort),
kijkt of het bestand bestaat, en of het anker na `#` een kop is in dat bestand (de slug zoals
GitHub die maakt). Codeblokken telt hij niet mee.

### De grep

Vanuit de wortel van het repo. Wat doorgehaald is (`~~…~~`, ook over meer regels), telt niet
mee: dat is geschiedenis. In `beslissingen.md` telt alleen fase 6 (de rijen B49–B71 en de
alinea's vanaf "Fase 6 (B49–B71)"), dus B39–B48 vallen erbuiten. In het stappenplan telt alleen
fase 6. Dit bestand zelf noemt het patroon, dus het telt niet mee.

```sh
P='nog open|koos nog niet|nog niet koos|blijft open|blijven open|staan open|staat open|niet beantwoord|geen antwoord|nog niet gekozen|nog niet beslist|niet als besloten|niet besloten|blijft (een |het )?voorstel|blijven (een |het )?voorstel|\*\*voorstel\*\*|is (nog )?(een )?voorstel|nog voorstel|tot (Jonas|je|hij) (iets anders )?(kiest|antwoordt|zegt)|kiest Jonas|Jonas kiest|een vraag aan (Jonas|jou)|vraag voor Jonas|tot er een antwoord|Voorstel:'
zonder_doorhaling() { perl -0pe 's{~~(.*?)~~}{my $s=$1; $s=~s/[^\n]/ /g; "  $s  "}gse' "$1"; }
for f in docs/fase-0/beslissingen.md docs/stappenplan.md README.md infra/single/README.md \
         docs/fase-6/*.md docs/fase-6/i3/*.md docs/runbooks/*.md; do
  case $f in
    docs/fase-0/beslissingen.md)  # alleen B49-B71 en de alinea's van fase 6
      zonder_doorhaling "$f" | awk '/^\*\*Fase 6 \(B49/{f6=1} /^## /{f6=0}
        /^\| B(49|5[0-9]|6[0-9]|7[01]) \|/ || f6 {print NR":"$0}' ;;
    docs/stappenplan.md)  # alleen fase 6
      zonder_doorhaling "$f" | awk '/^### Fase 6 /{f6=1} /^### Fase 7 /{f6=0} f6 {print NR":"$0}' ;;
    docs/fase-6/i3/docs-besluiten.md) continue ;;  # dit bestand noemt het patroon zelf
    *) zonder_doorhaling "$f" | awk '{print NR":"$0}' ;;
  esac | grep -i -E "$P" | sed "s|^|$f:|"
done
```

### De uitslag (op de kop van deze branch)

**0 treffers** in de documenten met de keuzes: `docs/fase-0/beslissingen.md` (fase 6),
`docs/stappenplan.md` (fase 6), `README.md`, `infra/single/README.md`, `docs/fase-6/README.md`,
`docs/fase-6/integratie-2.md` en de runbooks `gezichten.md` en `zoeken.md`.

Daarbuiten 51 treffers. Geen ervan is een keuze van fase 6 die nog open staat:

| Soort | Treffers (bestand:regel) | Waarom het mag blijven |
|---|---|---|
| "voorstel" is een voorstel van een gezicht ("Is dit Anna?") of de set uit Commons | `bouwontwerp.md:1172`; `bouwontwerp-r1.md:459`; `bouwontwerp-r2.md:868`; `toetsenbord-x2.md:84`; `x2b.md:62`; `x3a.md:1781`, `2418`, `2420`; `x3b.md:102`, `339`, `778` | Geen status van een keuze. `voorstel-beeld.tsv` en `voorstel-models.lock` zijn bestandsnamen. |
| Iets in de web of een verbinding "blijft open" of geeft "geen antwoord" | `toetsenbord-z6.md:75`, `118`; `x2b.md:194`; `x2a.md:84`; `bouwontwerp.md:5231`; `pg-analyse.md:99`; `runbooks/office.md:181` | Gedrag, geen keuze. |
| Een technisch open punt, zonder keuze voor Jonas | `bouwontwerp.md:370` en `metingen.md:310` (photometa naast een herstart, Defer); `a0.md:406`, `663` (de echte update en `--stappen` van 0.8.0); `x2a.md:288` (`df`, opgelost door de noot van I2 op de regel erna); `x2b.md:517` (de kwaliteit op de LXC); `x3a.md:233`, `322`, `323`, `1007`, `1216` (onderzoek voor beeld); `x3b.md:310` (BZ9 en BZ10 op de LXC); `z0-api.md:143`; `z0b.md:330` (`faces_too_many`); `z2.md:435`; `z3.md:331`; `z7.md:390`; `i3/pg-0013.md:14`, `359` (sloten op PostgreSQL); `runbooks/analyse.md:223` | Werk of een meting, geen keuze met een standaard. Wat Jonas op de LXC doet, staat in "Op de LXC" van de README van fase 6. |
| Geschiedenis | `bouwontwerp-r1.md:2`, `122`; `bouwontwerp-r2.md:5`, `210`, `2431` (oude revisies); `bouwontwerp.md:4960` (de regel: een nieuw pakket is een vraag, B19); `integratie.md:473` (wat I1 toen schreef); `x3a.md:664` (de go/no-go van golf 5, beslist in Keuze 85); `z0-api.md:213` en `z0-web.md:81` (velden die Z7 vastlegde); `z0.md:428` (een test van fase 3); `z3.md:537`, `z7.md:440` (tabellen van een review) | Zo was het toen; latere noten zeggen hoe het afliep. |

De regelnummers gelden voor de kop van deze branch; een samenvoeging kan ze verschuiven.

## Open

- **B49** zegt nog "op PostgreSQL ... zoekt de app zoals in 0.7.0", en de README van fase 6
  (Bekende grenzen) "op PostgreSQL zoekt het platform alleen op naam". Klopt tot P5 (de eigen
  zoekindex op PostgreSQL) samengevoegd is; daarna past P9 die zinnen aan, met de Keuze voor 0.8.2.
- **B39–B48** (fase 5) blijven voorstel: het besluit van Jonas ging over fase 6.
- **`faces_too_many`** komt nooit (de validatie is eerst). Dat is code, geen keuze; het staat in
  `z0b.md`, Open 2.
