# Fase 6: de integratie (I1)

Dit is het skelet. Stap B en C van I1 vullen het; het ontwerp staat in
[bouwontwerp.md](bouwontwerp.md), de tests in [e2e.md](e2e.md) en de getallen in
[metingen.md](metingen.md).

## In het kort

Wat I1 samenbracht tot versie 0.8.0 met de kern (Z0 tot en met Z7 en X1), en wat er voor 0.8.1
overblijft; volgt na stap C.

## Hoe het liep

De volgorde van de pakketten van stap A en van D1 (de wachttijd bij een upload), en wat er onderweg
misging, met de oorzaak.

## Open punten

Wat de golven aan I1 overlieten: de punten met "I1 (Ax)", "I1 (Sx)" en "I1 (D1)" in de z*.md en
x1.md, elk met de commit en de test.

### Gedaan

Per punt wat er veranderde, de commit en de test die het bewijst.

### Nagekeken, al gedaan

De punten die op `16ccb8d` al gedaan bleken, doorgestreept in de z*.md en x1.md met de reden en het
bewijs (het pakket i1-docs).

### Niet gedaan, met de reden

Wat naar 0.8.1, I2 of later gaat, met de reden en de plek waar het in de README staat.

## Wijzigingen aan contracten

Elke wijziging aan een contract (de API, de types van de web, de gebeurtenissen), met de reden en of
ze additief is, zoals een nieuw veld in `AdminSearch` voor I1 (S6).

## Metingen

Per meting de uitkomst tegen de poort met de regels van elke poort uit de uitvoer (gehaald, niet
gehaald of niet te meten, nooit alleen de exitcode), de controle op `OOMKilled` met de grenzen die
echt golden, en de langste wachttijd van een upload per run.

## Standaarden

Welke standaard I1 op grond van de metingen zet of laat staan, met de meting die het draagt.

## Installatietest en terugrollen

De update van 0.7.1 naar 0.8.0 en terug met `test_install_search.sh`: de functies lokaal,
`--stappen` in de job `infra`.

## Wat alleen CI of de LXC kan bewijzen

Wat de sandbox niet kan laten zien (de jobs met het image, `--stappen`, `bench --los` op de LXC, de
kwaliteit op echte foto's), met de plek waar het wel bewezen wordt.

## Afronden en 0.8.0

De versie, de README's, de runbooks, het stappenplan, de beslissingen en de run in CI op de kop van
0.8.0; geen tag zonder het ja van Jonas.

## De review van I1

De review van de code die I1 zelf toevoegt (de punten A, D1 en de punten S met nieuwe code), met de
bevindingen en wat ermee gebeurde.

## De checks

Wat er op de kop van I1 gedraaid is (stap B1), elk alleen en onder `taskset`, met de uitkomst; een
rode test staat erbij met zijn oorzaak, ook als hij daarna groen was.

## De na-controle van de review

Per reparatie van de review: de regel weg, de test rood, de regel terug, de test groen.

## Klaar als: waar elk punt bewezen is

Per regel van §4.0 van het ontwerp de test en de job die het bewijzen (hier, in CI of op de LXC), en
wat voor 0.8.1 open blijft (X2 en X3).
