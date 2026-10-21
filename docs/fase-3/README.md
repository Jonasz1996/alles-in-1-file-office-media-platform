# Fase 3 — Web-app

Status: **in aanbouw**.

## Doel

Een complete web-app bovenop de API van fase 2: een snelle bestandsbrowser voor grote mappen,
uploads van mappen, viewers voor foto's, video, PDF en kantoordocumenten, een fototijdlijn met
albums, en beheer, account, delen, prullenbak en versies in de app zelf. Nederlands en Engels,
licht en donker thema, en als PWA te installeren. Zonder nieuwe bibliotheken (B19).

Het bouwontwerp staat in [bouwontwerp.md](bouwontwerp.md) (revisie 2; revisie 1 in
[bouwontwerp-r1.md](bouwontwerp-r1.md)). De keuzes staan als B19 en verder in
[beslissingen](../fase-0/beslissingen.md).

## Pakketten

- **P0 — Fundament:** foutcodes, migratie 0006, contracten van de API (prullenbak in bulk,
  opnamedatum, geblokkeerde accounts, taal), router, i18n, UI-bouwstenen en de e2e-opzet.
- **P1 — Schil, PWA en prestaties:** navigatie, inloggen, service worker en het
  prestatiebudget.
- **P2 — Bestandsbrowser:** grote mappen in pagina's, selectie, bulkacties en zip.
- **P3 — Uploads:** mappen uploaden, hervatten, dubbele namen en de uploadlade.
- **P4 — Viewers en preview-pijplijn:** afbeeldingen, video en audio, PDF-pagina's en de
  office-lezer.
- **P5 — Foto's:** tijdlijn op opnamedatum en albums.
- **P6 — Beheer, account, delen, prullenbak en versies.**

## Klaar

- P0 (API): foutcodes met catalogi, migratie 0006, de contracten voor de pakketten en hun
  tests.
