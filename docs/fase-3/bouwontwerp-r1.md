# Fase 3 (web-app): bouwontwerp

Base: branch `claude/stappenplan-uitwerken-lu4uv3` @ `45411e8` (v0.4.0, migrations 0001–0005). I checked the claims below against the code. Where my reading differed from the readers, I followed the code:

- `styles.css` is 3,760 B. The 7,066 B figure is the built CSS.
- The built JS is 320,175 B, or 94,722 B gzip -9.
- React-dom alone is roughly 60 KB gzip.
- Vite 8.3.4 with Rolldown 1.2.14: chunk config goes in `build.rolldownOptions.output.codeSplitting`.
- Chromium is at `/opt/pw-browsers`, and Python Playwright imports.

---

## 1. Keuzes

Each line gives the default and why.

1. **Geen PDF.js, hls.js, Workbox, TanStack, virtualisatie-, i18n-, UI- of state-bibliotheken.** Alles zelf (B14). De stack-tabel in het stappenplan (regel 194) geldt niet voor fase 3.
2. **Geen nieuwe afhankelijkheden in `web/package.json` of `api/pyproject.toml`, ook geen dev-deps (geen jsdom of testing-library).** UI-logica komt in pure modules en wordt met vitest (node) getest. DOM-gedrag testen we met Playwright e2e (zie Vraag 2).
3. **PDF.** De server rendert pagina's op aanvraag naar WebP met het al aanwezige pypdfium2, met navigatie, zoom en inhoudsopgave. Er komt geen tekstlaag of selectie in fase 3; dat komt met full-text in fase 6. "Openen in nieuw tabblad" blijft de uitweg naar de native viewer.
4. **Video en audio.** Native `<video>`/`<audio>` op de bestaande downloadlink met Range. Codecs die de browser niet kan afspelen geven een duidelijke melding; er is geen transcoding (fase 5).
5. **Office-preview.** Een eigen vereenvoudigde lezer met stdlib zipfile en ElementTree, voor docx, xlsx, pptx, odt, ods en odp: de ingebedde thumbnail plus tekst, tabellen en dia-teksten zonder opmaak. Dit blijft de terugval zonder OnlyOffice (B15). doc, xls, ppt en rtf krijgen in fase 3 geen preview.
6. **Afbeeldingen.** Zoom en pan bouwen we zelf (CSS transform en pointer events). HEIC/HEIF/TIFF kunnen browsers niet tonen; die krijgen een extra WebP van 2048 px.
7. **EXIF lezen we nu al uit** met Pillow en ffprobe, die er al zijn, en slaan het per inhoud (blob) op. GPS staat alleen in eigen kolommen: nooit in `preview_meta` en nooit via links.
8. **Tijdlijn.** Alle eigen afbeeldingen en video's uit de hele mappenboom, zonder de prullenbak. Zonder EXIF-datum geldt de `lastModified` van de client, en anders het uploadmoment, in `PLATFORM_TIMEZONE` (standaard Europe/Amsterdam). Foto's van anderen komen er niet in; die zie je via albums en "Gedeeld met mij".
9. **Opnamedatum.** Een "zwevende" lokale tijd (EXIF heeft geen tijdzone), opgeslagen als UTC en getoond met `timeZone: "UTC"`. De dag staat als integer `YYYYMMDD`, zodat groeperen zonder SQL-dialect werkt.
10. **Album = een Node met kind `album` buiten de boom** (`parent_id` NULL), plus de tabellen `album` en `album_item`. Zo werkt delen met personen, groepen en links (B18) zonder nieuwe deelcode. Gedeelde albums zijn in fase 3 alleen te bekijken. Je voegt alleen eigen foto's en video's toe; er wordt niets gekopieerd.
11. **Bulkacties via batch-endpoints.** Eén verzoek, één transactie per 500 items, een resultaat per item en gegroepeerde events. "Ongedaan maken" is er alleen na "Naar prullenbak".
12. **Downloaden als zip** (selectie of map) zit al in fase 3: streaming met stdlib zipfile, `ZIP_STORED` (niets wordt opnieuw gecomprimeerd) en ZIP64. Het token staat 10 minuten in het geheugen; één node is genoeg.
13. **Mapupload naar een map met dezelfde naam** voegt samen: de bestaande map wordt hergebruikt. Dubbele bestanden vraagt de app één keer per batch: Vervangen (de vorige wordt een versie), Beide houden of Overslaan.
14. **Een selectie delen** kan alleen met personen of groepen: dezelfde deling op elk item. Links blijven per item.
15. **Nieuwe versie uploaden in "Versies…"** krijgt voortgang en Annuleren (XHR PUT), maar is in fase 3 niet hervatbaar. Daarmee hoeft er niets aan tus te veranderen.
16. **Gebruiker verwijderen.** Eerst blokkeren, dan definitief verwijderen als achtergrondtaak: alles gaat weg, ook albums en delingen. Bestanden overdragen aan een ander komt later. De laatste beheerder kan niet gedegradeerd, geblokkeerd of verwijderd worden, met `python -m app.cli make-admin` als noodweg.
17. **Prullenbak** wordt standaard niet automatisch geleegd. Op `/beheer` is dat in te stellen (0 = nooit).
18. **Bestandslijst.**
    - De standaardvolgorde is die van de server: mappen eerst, naam zonder hoofdletters en accenten.
    - Andere sorteringen, ook natuurlijke getallen, werken pas als de hele map geladen is.
    - De eerste pagina heeft 200 rijen; de rest komt op de achtergrond in pagina's van 1.000.
19. **Virtualisatie.** Vaste rijhoogte in de lijst, vierkante cellen in raster en tijdlijn, en scrollen op het venster.
20. **Previews.** Lijstantwoorden blijven licht: geen tekst, archieflijst of EXIF. Wat de viewer nodig heeft, komt van een eigen endpoint per bestand.
21. **PDF-pagina's en office-inhoud alleen met een download-token**, met toegangscontrole per verzoek. Een ingetrokken deling werkt dan meteen, net als bij downloads.
22. **JSON-compressie** alleen voor lijstantwoorden zonder geheimen: children, search, trash, shared-with-me en lookup. Nooit `/auth/*`, previews of foto-pagina's met ondertekende adressen (BREACH).
23. **De web-app wordt bij de build voorgecomprimeerd** (`.br` en `.gz` met `node:zlib`). Python serveert die bestanden alleen.
24. **PWA en offline werken alleen in een secure context**: het https-domein (B11) en localhost. Via `http://LAN-IP` blijft het een gewone web-app zonder service worker.
25. **De service worker cachet nooit `/api`.** Een update gaat pas in na "Vernieuwen", met een kill-switch-build als vangnet.
26. **i18n.**
    - NL is de bron; EN wordt volledig, ook `/beheer`.
    - De taal hoort bij het account (`users.locale`), met de keuze per apparaat als terugval.
    - Wisselen herlaadt de pagina.
    - Paden blijven Nederlands, want links worden gedeeld.
27. **Elke API-fout krijgt een code** (plus params). In NL toont de app de servertekst; in EN vertaalt hij op code, met de servertekst als terugval. Systeem- en https-meldingen krijgen ook codes, en apparaatnamen worden gestructureerd (browser/os).
28. **Thema** licht, donker of systeem, per apparaat. `/boot.js` zet het vóór de eerste paint; de CSP laat geen inline scripts toe.
29. **CSP.** `'unsafe-inline'` voor style verdwijnt: geen `<style>`-elementen meer. React style-props mogen wel, want die lopen via CSSOM.
30. **Lighthouse-profielen.** Mobiel (de standaard) ≥ 90 op `/` en `/s/<link>`; desktop ≥ 90 op `/map/<id>` met 10.000 bestanden. Mobiel op `/map` rapporteren we alleen. We meten met een eigen Playwright-meting die de Lighthouse-score nabootst, plus één handmatige controle in DevTools.
31. **Eén migratie 0006, gemaakt door P0, met alle schemawijzigingen van fase 3.** Daarna komt er in deze golf geen migratie meer bij.
32. **Sneltoetsen.** Nooit Ctrl+N/T/W of Ctrl+Shift+N (die houdt de browser). Wel: `?`, `/`, N, U, Shift+U, G, F2, Delete, Mod+A/C/X/V/F, Shift+F10 en de pijltjes.

### Vragen voor Jonas

1. **Wie mag de GPS-locatie van een foto zien?** Standaard (zo bouwen we het): alleen de eigenaar, in het infopaneel, als coördinaten met een link naar openstreetmap.org. Wie het gedeeld kreeg ziet het niet, en via links nooit. Alternatief: ook wie het gedeeld kreeg.
2. **Mag Playwright (Python) gebruikt worden als testgereedschap?** Alleen in de sandbox en in een CI-job (`pip install playwright`), niet in de image, `pyproject.toml` of `package.json`. Standaard: ja. Bij nee blijven alleen handmatige metingen in Chrome DevTools en vitest op pure logica over.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Naam | Grootte | Draait |
|---|---|---|---|
| **P0** | Fundament (§3) | L | **eerst, alleen** (twee werkers: P0-web en P0-api, disjunct) |
| P1 | Schil, PWA en prestaties | M | parallel |
| P2 | Bestandsbrowser | XL | parallel |
| P3 | Uploads | L | parallel |
| P4 | Viewers en preview-pijplijn | XL | parallel |
| P5 | Foto's: tijdlijn en albums | L | parallel |
| P6 | Beheer, account, delen, prullenbak, versies | L | parallel |

**Eigendom van bestaande bestanden na P0.** "frozen" betekent: na P0 wijzigt niemand het. Een bug of een ontbrekend stuk in een frozen bestand gaat via de *P0-hotfix-lane*: een kleine PR op de basisbranch waar alle pakketten op rebasen.

| Bestand | Eigenaar |
|---|---|
| web: `App.tsx`, `routes.tsx`, `router.ts`, `links.ts`, `main.tsx`, `api.ts`, `api.test.ts`, `crossApi.ts`, `currentUser.ts`, `Dialog.tsx`, `perf.ts`, `ui/**`, `i18n/index.ts`, `i18n/fmt.ts`, `i18n/types.ts`, `i18n/nl.ts`, `i18n/en.ts`, `i18n/areas/{common,shell}.*`, `i18n/errors/common.ts`, `tokens.css`, `base.css`, `viewer/types.ts`, `viewer/index.ts`, `viewer/param.ts`, `package.json`, `tsconfig*.json`, `scripts/check-i18n.mjs` | P0 (frozen) |
| web: `index.html`, `vite.config.ts`, `nginx.conf`, `Dockerfile`, `public/**`, `scripts/budget.mjs`, `scripts/icons.mjs`, `src/pwa/**`, `Login.tsx`, `i18n/areas/{auth,pwa}.*`, `i18n/errors/auth.ts` | P1 |
| web: `Files.tsx`, `NodeActions.tsx`, `FolderPicker.tsx`, `Search.tsx`, `searchQuery.ts`, `Tags.tsx`, `tagColors.ts`, `Thumb.tsx`, `events.ts`, `files.css`, `browser/**`, `files.test.ts`, `events.test.ts`, `thumb.test.ts`, `i18n/areas/browser.*`, `i18n/errors/browser.ts` | P2 |
| web: `upload.ts`, `uploadQueue.ts`, `upload.test.ts`, `upload/**`, `i18n/areas/upload.*`, `i18n/errors/upload.ts` | P3 |
| web: `PreviewPanel.tsx`, `Public.tsx`, `publicApi.ts`, `public.test.ts`, `public.css`, `viewer/**` (except the frozen three), `i18n/areas/{viewer,public}.*`, `i18n/errors/viewer.ts` | P4 |
| web: `photos/**`, `i18n/areas/photos.*`, `i18n/errors/photos.ts` | P5 |
| web: `Account.tsx`, `Beheer.tsx`, `BeheerGroups.tsx`, `BeheerJobs.tsx`, `Trash.tsx`, `Versions.tsx`, `ShareDialog.tsx`, `SharedWithMe.tsx`, `sharing.ts`, `share.css`, `account.test.ts`, `sharing.test.ts`, `admin/**`, `shares/**`, `trash/**`, `i18n/areas/{admin,account,sharing,trash}.*`, `i18n/errors/admin.ts` | P6 |
| api: `models.py`, `config.py`, `errors.py`, `__init__.py`, `migrations/versions/0006_fase3.py`, `tests/conftest.py`, `tests/factories.py`, `db.py`, `events.py`, `storage.py`, `migrate.py`, `text.py`, `totp.py`, `routers/events.py` | P0 (frozen) |
| api: `main.py`, `tests/test_system.py`, `tests/test_web_app.py` | P1 |
| api: `routers/files.py`, `routers/search.py`, `routers/batch.py`, `routers/archive.py`, tests `test_files.py`, `test_copy.py`, `test_files_events.py`, `test_search.py`, `test_versions.py`, `test_events*.py` | P2 |
| api: `routers/uploads.py`, `routers/folder_tree.py`, `tests/test_uploads.py` | P3 |
| api: `render.py` (except the P0 hook lines, see §3.3), `previews.py` (keep P0's hook lines), `mime.py`, `office.py`, `routers/previews.py`, `routers/viewer.py`, `tests/test_previews.py`, `tests/test_mime.py` | P4 |
| api: `render_meta.py`, `photodate.py`, `photometa.py`, `access.py`, `routers/shares.py`, `routers/public.py`, `routers/photos.py`, `routers/albums.py`, `tests/test_public.py`, `tests/test_shares*.py` | P5 |
| api: `routers/auth.py`, `routers/admin.py`, `routers/groups.py`, `routers/trash.py`, `routers/jobs_admin.py`, `routers/myshares.py`, `security.py`, `jobs.py`, `cluster.py`, `certs.py`, `acme.py`, `serve.py`, `useragent.py`, `cli.py`, `usage.py`, `userdelete.py`, tests `test_auth.py`, `test_2fa.py`, `test_account.py`, `test_admin.py`, `test_groups.py`, `test_jobs.py`, `test_cluster.py`, `test_certs.py`, `test_acme_pebble.py`, `test_serve.py`, `test_totp.py` | P6 |
| repo: `.github/workflows/ci.yml`, `e2e/perf/lighthouse_like.py`, `e2e/perf/run_all.py` | P1 |
| repo: `e2e/conftest.py`, `e2e/seed.py`, `e2e/perf/first_view.py`, `e2e/perf/scroll.py`, `e2e/test_smoke.py` | P0 (frozen) |
| repo: `e2e/test_<pkg>*.py`, `e2e/seed_<pkg>.py`, `docs/fase-3/<pkg>.md`, `docs/fase-3/toetsenbord-<pkg>.md` | each package its own |

**Regels voor alle pakketten**

1. Touch only files you own; anything else is import-only. Create new files only under your own folders or names.
2. **Compatibility of exports that others use.** Keep the signature; adding optional parameters is allowed.
   - `files.py`: `get_node`, `get_access`, `get_dir`, `get_live_dir`, `get_version`, `send_file`, `describe`, `one_out`, `notify`, `notify_many`, `shared_below`, `descendants`, `subtree_rows`, `tree`, `drop_versions`, `add_refs`, `collect_garbage`, `free_name`, `is_trashed`, `sibling`, `clean_name`, `user_lock`, `room_left`, `NodeOut`.
   - `public.py`: `current_link`, `CurrentLink`, `inside`, `readable`, `public_node`.
   - `uploads.py`: `release`, `_release`, `_drop`, `remove_expired`.
   - `previews.py`: `url`, `url_key`, `expiry`, `info`, `directory`, `render`.
   - `security.py`: all current names.
   - web: `NodeActions`, `Thumb`, `usePreviews`, `TagChips`, `TagEditor`, `SearchBox`, `ShareDialog`, `Versions`, `UploadQueue` (constructor and `TusApi`), `useServerEvents`, `useReloadOn`, `affectsFolder`, `affectsSharedWithMe`, `nodeActions`, `folderActions`, `crumbs`, `permissionOf`, `isShared`, `PERMISSION_LABELS`, `expiryLabel`.
3. **No migrations and no `models.py` edits.** If you find a schema need, stop and report it; it becomes 0007 at integration.
4. All new UI text goes through `t()` in your own area files, with an `en` translation (tsc enforces completeness). `npm run check:i18n -- --files <owned>` must report 0.
5. Every `HTTPException` in API files you own goes through `errors.fail(status, code, message, **params)`, and every new code gets an `en` entry in your `i18n/errors/<area>.ts`. P0's test `test_error_catalog.py` must stay green.
6. **Database and blob rules:**
   - commit the session before waiting on any lock (`await session.commit()` before `user_lock`/`blobs.lock`);
   - blob `ref_count` changes only under `BlobStore.lock`;
   - quota checks and reservations only under `user_lock(owner)`;
   - authorization only via `app/access.py` and the `files.get_*` helpers;
   - events via `events.publish` after commit, to the owner plus every watcher (`access.watchers`), or via `files.notify`/`notify_many`.
7. **Keyboard.** Every new action is reachable by keyboard and registered with `useShortcuts` in a scope. Document it in `docs/fase-3/toetsenbord-<pkg>.md` and cover it in your e2e test.
8. **Bundle and CSS.** Lazy-load anything heavy (route, dialog, viewer module). No `<style>` elements, no inline scripts, no hard-coded colours: tokens only. CSS goes in your own `.css` file.
9. Existing pytest and vitest stay green; `ruff check`, `ruff format --check` and `tsc -b` stay clean. Code comments and commit messages are in Dutch, like the existing code.

**Runtime dependencies between packages** (only via frozen stubs or contracts from P0, so each package compiles and tests on its own):

| User | Uses | Built by |
|---|---|---|
| P2 | `<Viewer>` | P4 |
| P2 | `UploadButtons`, `uploadDropped`, `hasFiles` | P3 |
| P2 | `AddToAlbumDialog` | P5 |
| P2 | `ShareDialog nodes=` | P6 |
| P2 | `crossApi.restore` (undo) | P6 |
| P5 | `crossApi.trash`, `crossApi.archiveLink` | P2 |
| P5 | ShareDialog album rules | P6 |
| P5 | `<Viewer>` | P4 |
| P6 | `<Viewer>` (versions, shared-with-me) | P4 |
| P4 | `meta.exif` filled | P5 |
| P4 | `PublicAlbum` | P5 |
| everyone | `ui/*`, router, i18n, `fmt` | P0 |

---

### P1 — Schil, PWA en prestaties

**Doel.** Installable PWA with an offline shell, fast first paint, compressed and correctly cached serving, a size budget in CI, a Lighthouse-like measurement, and polish of the login and 2FA flows.

**Dekt.** PWA installable plus offline shell; performance: compression, code-split budget, first paint; serving robustness for SW and splitting; CSP; auth-flow polish (login side); the Lighthouse > 90 measurement (§4).

**Maakt**
- `web/public/manifest.webmanifest`: name "Platform", `short_name`, `start_url` `/`, `scope` `/`, `display: standalone`, `lang: nl`, colours from the tokens, icons 192, 512 and 512-maskable, shortcuts `/gedeeld` and `/zoeken`.
- `web/public/icons/{icon.svg,icon-192.png,icon-512.png,icon-maskable-512.png,apple-touch-icon.png}` and `web/public/favicon.{svg,ico}`.
- `web/scripts/icons.mjs`: draws the logo itself and writes the PNG/ICO with `node:zlib` (deflate plus `zlib.crc32`). The PNGs are committed.
- `web/src/pwa/sw.ts`, about 150 lines:
  - precache: index.html, all `/assets/*`, `boot.js`, the manifest and icons;
  - navigations: network-first, with the cached `index.html` as offline fallback;
  - `/assets/*`: cache-first;
  - never `respondWith` for `/api/*`, `/health`, `/.well-known/*`, Range requests or non-GET;
  - versioned cache names, old caches removed on activate, `skipWaiting` only on a message from the page;
  - `VITE_SW=off` builds a self-unregistering kill switch.
- `web/src/pwa/routing.ts`: pure `classify(request: {url, method, mode, headers}) → "shell" | "asset" | null`.
- `web/src/pwa/register.ts` (replaces P0's stub): registers only when `import.meta.env.PROD && "serviceWorker" in navigator && isSecureContext`.
- `web/src/pwa/AppStatus.tsx` (replaces P0's stub): the offline banner (online/offline events) and the toast "Nieuwe versie beschikbaar — Vernieuwen".
- `web/src/pwa/shell.css` (P0 creates it empty and imports it in `main.tsx`): styles for the static shell.
- `web/scripts/budget.mjs` (replaces P0's stub): gzips the dist, follows the import graph from `index.html`, and fails when over budget (§4).
- `api/tests/test_web_app.py`, `web/src/pwa/routing.test.ts`, `e2e/test_pwa.py`, `e2e/test_login.py`, `e2e/perf/lighthouse_like.py`, `e2e/perf/run_all.py`, `docs/fase-3/p1-schil.md`.

**Wijzigt (exclusief)**
- `web/index.html`:
  - manifest link and theme-color metas for light and dark (P0 already put `boot.js` there);
  - static shell markup inside `#root` (the topbar outline and a spinner, no text, so it needs no i18n);
  - `apple-touch-icon` and favicon links.
- `web/vite.config.ts`:
  - an inline plugin `swPlugin`: `buildStart` emits `src/pwa/sw.ts` as a chunk with `fileName: "sw.js"`, and `generateBundle` replaces `self.__PRECACHE__` and `__BUILD__` with the emitted file list and build hash;
  - an inline plugin `precompress`: `writeBundle` writes `.br` (`brotliCompressSync`, quality 11) and `.gz` (level 9) next to every js, css, html, svg, webmanifest and json file larger than 1 KB;
  - `build.rolldownOptions.output.codeSplitting = { groups: [{ name: "vendor", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ }] }`.
  - Risk: check the Rolldown `emitFile`/`generateBundle` behaviour, not the Rollup docs.
- `web/nginx.conf` (cluster image) mirrors the API: `gzip_static on`, a brotli-free fallback, `location = /sw.js` and `/boot.js` with no-cache, `try_files $uri =404` for paths with an extension, the same CSP and security headers.
- `api/app/main.py`:
  - `web_app`:
    - 404 for a missing file under `assets/` or any path with a suffix;
    - pick `candidate + ".br"` or `".gz"` when it exists and `Accept-Encoding` allows it (q-values respected), with `Content-Encoding`, `Vary: Accept-Encoding` and the original media type;
    - a stat ETag (`W/"<mtime_ns>-<size>"`) plus `If-None-Match` → 304 for no-cache files;
    - `.webmanifest` served as `application/manifest+json`.
  - `WEB_CSP = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'"`.
  - Add `Permissions-Policy: camera=(), microphone=(), geolocation=()` to `SECURITY_HEADERS`.
  - `JsonCompression` ASGI middleware (stdlib gzip): only GET/POST responses with `content-type: application/json`, body ≥ 8 KiB, path matching the allowlist `^/api/v1/(files/[^/]+/children|files/root|search|trash(/overview)?|shared-with-me|files/lookup)$`, and the client accepts gzip. Never anything else.
- `web/Dockerfile` only if needed to carry `.br`/`.gz`.
- `web/src/Login.tsx`:
  - show the form at once, using `api.lastRefresh.setupRequired` and fetching `/auth/status` only when that is null;
  - a separate network-error state ("Geen verbinding met de server", retried on `online`);
  - a "Terug" link on the code step and a show-password toggle;
  - "Wachtwoord vergeten? Vraag je beheerder om een nieuw wachtwoord.";
  - show `totp_locked_until` with `fmt.relative`;
  - `PageTitle` per step; `ui/Field` and `ui/Button`;
  - all strings through t();
  - `markReady("login")`.
- `.github/workflows/ci.yml`:
  - web job: `npm run check:i18n` (report mode) and `npm run budget`;
  - images smoke: `/assets/nope.js` → 404, `/sw.js` → 200 `text/javascript`, `curl -H 'Accept-Encoding: br' /` → `content-encoding: br`, exact CSP;
  - a new job `e2e` (needs: images; Playwright functional tests, hard gate) and `perf` (report-only, uploads JSON as an artifact).
- `api/tests/test_system.py`: update the CSP assertions.

**API.** No new endpoints. Behaviour as above.

**Migratie.** Geen.

**Tests**
- pytest `test_web_app.py`:
  - 404s for `assets/x.js` and `foo.png`;
  - SPA fallback for `/map/<uuid>` and `/fotos`;
  - br and gzip selection, plus `Vary`;
  - 304 with the ETag;
  - manifest content type;
  - exact CSP;
  - JSON compression on a large children listing, but not on `/api/v1/auth/*`, `/api/v1/previews` or `/api/v1/events`, and not below 8 KiB.
- vitest `routing.test.ts`: navigation → shell; `/assets/x.js` → asset; `/api/v1/events`, a tus PATCH, `Range` headers, POST, `/health` and `/.well-known` → null.
- e2e `test_pwa.py` on `http://localhost`:
  - the SW takes control;
  - going offline and reloading shows the shell plus the banner;
  - after a rebuild the toast appears and "Vernieuwen" loads the new build;
  - the manifest and icons are fetchable.
- e2e `test_login.py`: login with TOTP, Terug, network error, lock message, EN locale.

**Klaar als**
- `npm run build` emits `sw.js`, the manifest, the icons and `.br`/`.gz` files.
- `npm run budget` passes.
- All tests above are green.
- `e2e/perf/run_all.py` gives Lighthouse-like mobile ≥ 90 on `/` and on `/s/<link>` (median of 5).
- First view of `/` is under 1.5 s on the LAN profile.
- `check:i18n` shows 0 findings for P1's files.

---

### P2 — Bestandsbrowser

**Doel.** A file browser that stays smooth at 10,000 items per folder, in list and grid views, with selection, bulk actions, a context menu, internal drag and drop, keyboard shortcuts and incremental live updates.

**Dekt.**
- Browser list and grid (virtualized).
- Bulk actions, including zip download and multi-share.
- Context menu.
- Shortcuts.
- Internal drag and drop, and dropping onto rows or crumbs (calling P3).
- A listing endpoint that copes with 10k children.
- Live updates without full reloads.
- A target picker for a selection.
- Fase 10 hooks.

**Maakt**
- `web/src/browser/FileList.tsx`:
  - a div `role="grid"` on `ui/VirtualList`, with `aria-rowcount`/`aria-rowindex`;
  - a sticky header and columns Naam/Grootte/Gewijzigd plus a ⋯ button, collapsing on narrow screens;
  - sortable column headers (`aria-sort`);
  - memoized rows;
  - roving focus (one tab stop);
  - the selection checkbox column;
  - a row status slot for fase 10 job progress.
- `web/src/browser/FileGrid.tsx`: `ui/useColumns` with cells of at least `--cell-min`, using `Thumb size=180` (256 WebP).
- `web/src/browser/folderStore.ts`, a store per open folder:
  - `Map<id, FileNode>` plus the order and total;
  - paging: first page `limit=200`, the rest in pages of 1,000 via `requestIdleCallback`;
  - `upsert`/`remove` from events (collect `node.*` ids for 150 ms, then `POST /files/lookup`);
  - full reload only on `connected`, `tags.changed`, `share.*` in shared folders, or a 404;
  - own actions patch the store from the API result.
- `web/src/browser/sort.ts`: server order (fold plus codepoint) and client sorts (`collator` numeric, folders first), applied only once fully loaded.
- `web/src/browser/actions.ts`, the action registry in pure TS:
  - an action is `{id, label, icon, group, shortcut?, when(sel, folder) → {visible, enabled, reason}, run(sel, ctx)}`;
  - groups Openen, Bewerken, Delen, Info, Prullenbak, plus an empty reserved `omzetten` group;
  - `registerAction()` for fase 10;
  - permissions are `nodeActions()` intersected over the selection.
- `web/src/browser/clipboard.ts`: in-app cut/copy/paste of node ids.
- `web/src/browser/dragState.ts`: `{ids, kinds, mimes}`, subscribable, which fase 10's omzet-lade will need. Plus HTML5 DnD on rows, crumbs and nav targets:
  - type `application/x-platform-nodes`;
  - `setDragImage` with an "n items" badge;
  - move within the same owner, copy with Ctrl/Alt or across owners;
  - auto-scroll near the edges (rAF);
  - a refused target gets `dropEffect="none"` plus a reason in a live region.
- `web/src/browser/filter.ts` and a `FilterBox` ("Filter in deze map", Mod+F; a second Mod+F goes to the browser).
- `web/src/browser/previews.ts`: `useVisiblePreviews(visibleIds)`, an id→Preview cache that fetches only missing or stale ids (debounced 100 ms, ≤ 500 per POST) and refreshes on `preview.ready` and `connected`. `usePreviews(nodes)` stays as a wrapper for compatibility.
- `web/src/browser/preload.ts` (replaces P0's stub): `preloadFolder(id)` starts page 1 into `folderStore` during boot.
- `web/src/browser/api.ts`: listing paging, lookup, folders-only listing.
- `web/src/browser/browser.css`.
- `api/app/routers/batch.py` and `api/app/routers/archive.py` (P0 stubs, already included in main.py).
- Tests: `api/tests/test_listing_paging.py`, `test_lookup.py`, `test_batch.py`, `test_batch_events.py`, `test_archive.py`; `web/src/browser/{actions,folderStore,sort,clipboard,filter}.test.ts`; `e2e/test_browser.py`, `e2e/test_browser_keyboard.py`, `e2e/seed_browser.py`; `docs/fase-3/toetsenbord-p2.md`.

**Wijzigt (exclusief)**
- `Files.tsx`, rewritten on FileList/FileGrid/folderStore:
  - a toolbar with `UploadButtons` (P3), "Nieuwe map" (a Dialog), a Lijst/Raster toggle, sort, FilterBox and SearchBox;
  - a selection bar ("3 geselecteerd" plus actions from the registry);
  - drop on main, rows and crumbs calls `uploadDropped(dt, folderId)` from P3 when `hasFiles(dt)`, and does an internal move or copy otherwise;
  - Viewer via `useViewerParam`;
  - after "Naar prullenbak", a toast with "Ongedaan maken" → `crossApi.restore(ids)`;
  - `markReady("folder")` after the first rows paint;
  - `useRestoreScroll`.
- `NodeActions.tsx`: becomes a `MenuButton` adapter on the registry (export kept).
- `FolderPicker.tsx`: `exclude: Set<string>`, a check over the whole selection (cross-owner → move disabled with the reason, copy allowed), `?kind=dir`, "Nieuwe map", keyboard type-ahead.
- `Search.tsx` and `searchQuery.ts`: results on FileList with a path column; Viewer for hits; `navigate()` instead of a hand-written `pushState`.
- `Tags.tsx`, `tagColors.ts`: a dialog instead of prompt/confirm; chips render a dot or border with `--tag` and text in `--fg`, so they work in dark mode (stored hex unchanged).
- `Thumb.tsx`: a `size` prop (32 or 180), SVG icons from `ui/Icon`, no inline styles.
- `events.ts`: `affectsFolder` and `affectsSharedWithMe` already read `ids` (P0); add `folderEventIds(event, folderId)` for the store.
- `files.css`.
- `api/app/routers/files.py`:
  - **listing**: `GET /files/{id}/children` and `/files/root` accept `?limit=1..5000&after=<cursor>&kind=dir|file`;
  - **`describe()` faster**: `NodeOut.model_construct` with the same output (a test compares the old and new paths);
  - **`POST /files/lookup`**;
  - error codes on every raise.
- `api/app/routers/search.py`: error codes only, and reuse of the faster `describe`.
- `api/tests/test_files.py` etc. only where behaviour is additive.

**API**
```
GET  /api/v1/files/{id}/children?limit=&after=&kind=      (also /files/root)
  → Listing & { next: string|null, total: int }        # zonder limit: alles, next=null (zoals nu)
     cursor = base64url("<kind>\x1f<search_name>\x1f<id>") van de laatste rij; order (kind, search_name, id)
POST /api/v1/files/lookup        { ids: uuid[≤500] }   → { nodes: NodeOut[] }   # zelfde toegang als GET /files/{id}; onzichtbaar = weg
POST /api/v1/batch/trash         { ids: uuid[≤10000] } → { results: ItemResult[] }
POST /api/v1/batch/move          { ids, parent_id, conflict?: "fail"|"rename" } → { results }   # onder blobs.lock zoals change()
POST /api/v1/batch/copy          { ids, parent_id }    → { results }   # ItemResult.node = kopie; som subtrees ≤ MAX_COPY;
                                                                        # onder user_lock(doel-eigenaar) + blobs.lock, quota één keer
ItemResult = { id, ok: bool, code?: "not_found"|"read_only"|"share_root"|"cross_owner"|"name_taken"|"into_itself"|"trashed"|"too_many",
               message?: str, node?: NodeOut }
POST /api/v1/archive/link        { ids: uuid[≤1000], name: str } → { url: "/api/v1/archive?token=…", expires_in: 600 }
GET  /api/v1/archive?token=      → 200 application/zip (streaming, Content-Disposition attachment "<name>.zip",
                                     geen Content-Length/Range); toegang per top-id opnieuw (get_access read);
                                     subtree_rows(live=True); paden uit parent-ids; ZIP_STORED, force_zip64;
                                     schrijver in anyio.to_thread → memory object stream; stopt bij disconnect;
                                     ≤ 50.000 entries (anders 422 too_many vóór de eerste byte); token in een dict met TTL
Events (via files.notify_many van P0): data { ids:[≤500], parent_id, old_parent_id? } per (ontvanger, zichtbare ouder)
```

**Migratie.** Geen. P0 already adds the index `ix_node_parent_order (parent_id, kind, search_name, id)`.

**Tests**
- pytest:
  - **paging**: concatenated pages equal the unpaged listing, the cursor is stable while inserting, `kind=dir`, `total`;
  - **lookup** respects shares and trash;
  - **batch**: partial failures per code, one transaction per 500, shared-folder rules (`share_root`, `read_only`), cross-owner, quota on copy, events grouped and bounded (≤ 500 ids each; recipients include share watchers);
  - **zip**: a valid zip (open it with zipfile), access revoked between link and download → 404, disconnect stops the writer, more than 50k → 422;
  - **describe parity**.
- vitest: `when()` intersections and reasons; the folderStore event merge (create, move away, delete, grouped ids); sort stability; clipboard; filter.
- e2e: seed 10k → open, scroll to the end, select a range with Shift, select all, Delete plus Ongedaan maken, F2 rename, N new folder, cut/paste move, copy, a zip download (file opens), the context menu via right-click, Shift+F10 and long-press (touch emulation), grid toggle G, drag a row onto a folder row and onto a crumb, "Delen" on a selection opens ShareDialog with `nodes`.

**Klaar als**
- The `e2e/perf` gates for `/map/<10k>` pass:
  - first view < 1.5 s on LAN;
  - scrolling smooth: p95 frame ≤ 20 ms at 1×, ≤ 34 ms at 4×;
  - no long task > 50 ms;
  - at most 200 rows rendered.
- Lighthouse-like desktop ≥ 90 on `/map/<10k>` (P1's tool at integration; P2 checks with P0's `first_view`/`scroll`).
- Only one children request plus background pages while uploading 1,000 files (counted from the network log).
- Every action in the registry is reachable by keyboard and in `?` help.
- 0 `window.prompt`/`confirm` calls; `check:i18n` 0.

---

### P3 — Uploads

**Doel.** A global upload tray with totals, speed and ETA; pause and resume; recovery of interrupted uploads after a reload; one conflict choice per batch; folder upload by button and by drop; parallelism per file; a fix for the O(n²) queue.

**Dekt.** Upload-UI: TUS (conflict), progress, pause and resume, folder upload; drag and drop from the OS (folders, the entries walker).

**Maakt**
- `web/src/upload/index.ts` (replaces P0's stub), with the same exports: `UploadButtons`, `hasFiles`, `uploadDropped`, `UploadTray`.
- `web/src/upload/UploadButtons.tsx`: "Uploaden", plus "Map uploaden" (an input with `webkitdirectory`); shortcuts U and Shift+U in scope `browser`.
- `web/src/upload/walk.ts`:
  - `collect(dt: DataTransfer) → {files: {file, path}[], dirs: string[]}` via `webkitGetAsEntry`;
  - `readEntries` in a loop until it returns `[]`;
  - falls back to `.files`;
  - rejects pseudo-file folders.
- `web/src/upload/UploadTray.tsx`:
  - bottom-right, collapsible;
  - an `aria-live=polite` summary: "1.234 van 5.000 · 2,1 GB van 6 GB · 12 MB/s · nog ~6 min";
  - Pauzeren/Hervatten per file and for all; Annuleren; failed items with Opnieuw/Weggooien;
  - "Alles tonen" in a `VirtualList`;
  - a `beforeunload` warning while uploads run.
- `web/src/upload/ConflictDialog.tsx`: "N bestanden bestaan al" with Vervangen (vorige wordt een versie) / Beide houden / Overslaan, plus "voor allemaal".
- `web/src/upload/Interrupted.tsx`: "Onderbroken uploads" from `GET /tus` (name, size, %), with "Kies het bestand opnieuw" (a file input matched on name and size) and "Weggooien".
- `web/src/upload/api.ts`.
- `web/src/upload/upload.css`.
- `api/app/routers/folder_tree.py` (P0 stub, included).
- Tests: `api/tests/test_folder_tree.py`, `test_uploads_list.py`; `web/src/upload/{queue,walk,conflict,eta}.test.ts`; `e2e/test_upload.py`; `docs/fase-3/toetsenbord-p3.md`.

**Wijzigt (exclusief)**
- `uploadQueue.ts`:
  - items in a `Map` plus running totals (bytes total and sent, files done and failed);
  - notifications coalesced to at most 4 per second;
  - `add(parent, files, opts?: {conflict?})` takes the whole batch at once (Set dedupe on storageKey: O(n));
  - states `waiting|running|paused|failed|discarding`;
  - `pause(key)` = `abort(STOP)` without removing the item; `resume(key)`, `pauseAll()`, `resumeAll()`;
  - a scheduler with up to 3 concurrent uploads, 1 when a file is > 256 MB;
  - `addTree(folderId, collected)` creates folders via `folders/tree`, in chunks of 1,000 paths, then queues the files with their real parent ids;
  - **the constructor and `TusApi` stay compatible** (Public.tsx, public.test.ts and sharing.test.ts use them).
- `upload.ts`: pass metadata `conflict` and `lastmodified` (ms) through `tusCreate(parent, name, size, meta)` (P0 added the param). A conflict pre-check: create with `conflict=fail` → `name_taken` → collect the conflicts, ask once, then re-create with the choice; Overslaan keeps `fail`.
- `upload.test.ts`: extend it; existing cases stay.
- `api/app/routers/uploads.py`:
  - metadata `lastmodified` → `Upload.client_mtime` (P0 column);
  - `finish()` passes `client_mtime` to `place_file(…, client_mtime=)` (P0 param);
  - `PUT /files/{id}/upload?mtime=<ms>` is handled in files.py by P2? **No: P0 already reads `?mtime`.** P3 only does tus;
  - `GET /api/v1/tus`;
  - error codes on all 16 raises.

**API**
```
GET  /api/v1/tus → [{ id, name, parent_id, size, offset, created_at, expires_at }]   # eigen: created_by=user, share_id NULL, node_id NULL
Upload-Metadata: filename, parent, conflict(rename|replace|fail), lastmodified(ms, optioneel)
POST /api/v1/files/{id}/folders/tree { paths: str[≤1000] }  → { folders: { "<path>": uuid } }
   # "Vakantie", "Vakantie/2024"; elk segment clean_name; diepte ≤ 32; get_live_dir(id, "write");
   # aanmaken-of-hergebruiken hoofdletterongevoelig (sibling()) onder user_lock(eigenaar van de doelmap),
   # session.commit() vóór het slot; één commit; node.created per nieuwe top-level map (notify_many)
```

**Migratie.** Geen; `upload.client_mtime` comes from P0.

**Tests**
- pytest `folder_tree`:
  - reuse is case-insensitive;
  - two parallel calls create no duplicates (`anyio` task group);
  - write access in a shared folder;
  - rejected names, depth and count;
  - events.
- pytest `uploads_list`: only your own unfinished uploads; `lastmodified` ends up in `node.taken_at` for an image without EXIF (via P0's stamp).
- vitest:
  - queue O(n) with 5,000 files (time budget), totals, pause/resume, concurrency, notification coalescing;
  - the walker with fake entries (more than 100 per batch, empty folders);
  - ETA moving average.
- e2e:
  - folder upload via `setInputFiles` on a directory, so the subfolder structure appears;
  - pause → resume;
  - reload → "Onderbroken uploads" → choose the file again → completes;
  - conflict dialog with "voor allemaal";
  - the tray is visible on `/gedeeld`.

**Klaar als**
- 5,000 files queue without freezing the tab (no long task > 200 ms).
- Folder upload with subfolders works by button, and by drop in Chromium (manual check noted in the docs).
- Everything works by keyboard; `check:i18n` 0.

---

### P4 — Viewers en preview-pijplijn

**Doel.** A full-screen viewer with prev/next, keyboard control and a deep link. Per type: images (zoom, EXIF panel), PDF (page viewer), video and audio (native, honest error messages), text and code (own highlighter, Markdown, CSV), and office (simplified reader). Plus the server-side pipeline: light list payloads, viewer endpoints, PDF pages, the office reader, codecs, the display size and re-render versioning.

**Dekt.** Viewers (all sub-items), pipeline versioning, and the Public page viewer.

**Maakt**
- `web/src/viewer/Viewer.tsx` (replaces P0's stub, default export, props from `viewer/types.ts`):
  - a full-viewport `<dialog>` with a header ("name · 3 / 120") and a toolbar (download, info, new tab, full screen);
  - swipe;
  - preloads the neighbouring large previews;
  - scope `viewer`:
    - navigation: Esc, ←/→, Home/End;
    - zoom and view: +/−/0/1, F (full screen), I (info), D (download), R (rotate);
    - media: Space/K, J/L ±10 s, M (mute);
    - PDF: PgUp/PgDn;
  - `onNeedMore` at the edges.
- Viewer modules, each loaded with `import()`:
  - `viewer/ZoomView.tsx`: transform and pointer events, pinch, wheel around the cursor, double-click toggles fit/100 %, swap to the original or `display` after `img.decode()` once it needs more than 1024 px, animated → original.
  - `viewer/PageViewer.tsx`: generic, so fase 4 can reuse it; placeholders from `page_sizes`, windowing with `variableRange`, `srcset` over the width buckets, fit width/page, 50–400 %, page box, TOC sidebar.
  - `viewer/MediaView.tsx`: `canPlayType` from the stored codecs → "Deze browser kan dit formaat niet afspelen" plus a download button. MediaError 3/4 never gets a new token; network errors and 403 do (keep `mayRenew`). Audio auto-advances on `ended`.
  - `viewer/TextView.tsx`:
    - the first 512 KB via `range()`, then "Meer laden" and windowed lines;
    - `decode.ts`: BOM → utf-8/16; otherwise utf-8 `fatal`, falling back to windows-1252, with a dropdown to switch;
    - `highlight.ts`: a state-machine tokenizer per family (C-like/JS/TS/Go/Rust/Java/C#, Python, shell, JSON, YAML/TOML/INI, XML/HTML, CSS, SQL), never regex backtracking;
    - `highlight.worker.ts` for anything over 100 KB, capped at 1 MB;
    - `markdown.ts`: a subset rendered to React elements, links only http/https/mailto with `rel="noopener noreferrer"`, images as alt text;
    - `csv.ts`: RFC 4180 into a table with a sticky header;
    - never `innerHTML`.
  - `viewer/OfficeView.tsx`: DocumentView / SheetView (tabs plus a grid with sticky headers) / SlidesView, labelled "Vereenvoudigd voorbeeld zonder opmaak".
  - `viewer/InfoPanel.tsx`: groups Camera, Objectief, Belichting, Datum and Locatie (owner only); dates via `fmt.wallClock`.
- `viewer/source.ts`: `defaultSource` (logged in; memoizes download URLs per `(id, etag, inline, version)` for `expires_in − 60 s`) and `publicSource(link)`.
- `viewer/media.ts`: `formatDuration`, `mayRenew`, `playerFor`, moved here; `PreviewPanel.tsx` re-exports them.
- `viewer/api.ts`, `viewer/viewer.css`, `public.css` (P0 moves the public rules out of share.css).
- `api/app/office.py`, stdlib only:
  - per-member size caps like `render._read`;
  - `iterparse` with an early stop;
  - ODF repeated rows and cells capped;
  - docx: headings, paragraphs with bold/italic, lists, tables;
  - xlsx: sheets, sharedStrings, inlineStr, bool, error, dates via numFmt 14–22 and custom formats, date1904; 200 × 50 per sheet;
  - pptx: slides in `sldIdLst` order, title plus bullet levels;
  - the ODF equivalents;
  - metadata from core/app/meta.xml;
  - the thumbnail from `Thumbnails/thumbnail.png` or `docProps/thumbnail.jpeg` (skipping WMF/EMF).
- `api/app/routers/viewer.py` (P0 stub, included).
- Tests: `api/tests/test_viewer.py`, `test_pages.py`, `test_office.py`, `test_render_codecs.py`, `test_render_version.py`; `web/src/viewer/{decode,highlight,markdown,csv,zoom,media,preview}.test.ts`; `e2e/test_viewer.py`; `docs/fase-3/toetsenbord-p4.md`.

**Wijzigt (exclusief)**
- `render.py`:
  - `kind_of`: an `office` branch for OOXML/ODF mimes and extensions → `office()`;
  - `image()`: `meta.animated` (`is_animated`) plus a `2048` display size for HEIC/HEIF/TIFF only;
  - `video()`: `meta.video {codec, profile, pix_fmt}`, `meta.audio {codec, channels, sample_rate}`, `meta.container`;
  - `text()`: cp1252 fallback plus `meta.encoding`;
  - `main()`: a new `mode == "page"` (`{pages:[n…], width}` → writes `<n>.webp`) and `mode == "docinfo"` (page_sizes, toc, labels).
  - **Keep P0's hook lines intact** (the `exif=` param and the `render_meta` calls, §3.3).
- `previews.py`:
  - `SIZES` gains `DISPLAY = 2048`;
  - `info(…, full=False)` is light by default (`width, height, duration, pages, animated, sizes`), and `full=True` returns everything plus `display`;
  - signed page URLs are **not** used; pages are token-protected (Keuze 21);
  - `RENDER_VERSION` and `Blob.render_version`;
  - `enqueue_outdated(settings)` (P0 stub): blobs with `render_version < CURRENT` and an office or HEIC/HEIF/TIFF mime get a chained cursor job (`run_after = now + 60 s`, 200 per run); everything else gets `UPDATE render_version = CURRENT`;
  - `_store` sets `render_version`;
  - **keep P0's `photodate.on_blob_meta` call.**
- `mime.py`: only if office detection needs it.
- `routers/previews.py`: the light list payload (`info(full=False)`); size 2048 accepted.
- `PreviewPanel.tsx`: becomes a thin compatibility layer over the viewer modules (or is removed once nothing imports it except the re-exports).
- `Public.tsx` and `publicApi.ts`:
  - the Viewer with `publicSource`, plus public preview, document and pages calls;
  - `useQueryParam("map")` instead of hand-written `pushState`;
  - i18n;
  - `markReady("public")`;
  - P0's album dispatch to `PublicAlbum` stays.
- `public.test.ts`.

**API**
```
POST /api/v1/previews (+ /public/previews)  → zoals nu, meta licht
GET  /api/v1/files/{id}/preview?version=<vid>  → ViewerDetail
     { state, thumb, large, display: str|null,
       meta: { …volledige preview_meta: width,height,duration,pages,title,artist,album,text?,truncated?,entries?,count?,
               encoding?, animated?, video?, audio?, container?, exif?: Exif },
       taken_at: str|null, location: {lat, lon}|null   # location alleen eigenaar (Vraag 1) }
GET  /api/v1/files/{id}/document?version=
     → { kind: "pdf"|"office", pages: int|null, page_sizes: [[w_pt,h_pt]]|null, toc: [{title,page,level}], labels: str[]|null,
         widths: [640,1280,1920,2560], page_url: "/api/v1/files/{id}/pages/{n}.webp?w={w}&token={download_token}",
         office: OfficeDoc|null }        # OfficeDoc ≤ 512 KB uit previews/<sha>/doc.json
GET  /api/v1/files/{id}/pages/{n}.webp?w=&token=   → image/webp; read_download_token + get_node(read) per verzoek;
     cache previews/<sha>/pages/<w>/<n>.webp (tmp+rename, onder blobs.lock blob nog aanwezig); render n, n+1, n+2 in één
     subproces achter anyio.CapacityLimiter(settings.page_render_workers); LRU-opruimen tot settings.page_cache_mb
     direct na schrijven; ETag "<sha>-<n>-<w>", Cache-Control private, max-age=600
GET  /api/v1/public/preview/{node_id}   (Bearer key)  → ViewerDetail zonder location
GET  /api/v1/public/document/{node_id}  (Bearer key)
GET  /api/v1/public/pages/{node_id}/{n}.webp?w=&key=   # via public.current_link + inside()
```
`OfficeDoc = {type:"document", blocks:[{h|p|li|table…}]} | {type:"sheet", sheets:[{name, rows:string[][], truncated}]} | {type:"slides", size:[w,h], slides:[{title, bullets:[{text, level}]}]}`.

**Migratie.** Geen; `blob.render_version` comes from P0.

**Tests**
- pytest:
  - the light vs full payload;
  - viewer detail with a version;
  - location only for the owner;
  - pages: access revoked → 403/404; a `w` outside the buckets → 422; an out-of-range page → 404; the limiter holds a third request; cache eviction;
  - office fixtures generated in-test (zipfile plus XML strings) for all 6 formats, plus large and repeated-cell bombs that stay under the caps and time limit;
  - codecs from a generated mp4 (ffmpeg in CI);
  - an animated GIF;
  - the render-version backfill bumps non-office blobs without work and queues office blobs after fresh preview jobs (`run_after`).
- vitest: decode (BOM, cp1252), a tokenizer on a pathological 1 MB input under 200 ms, markdown with no raw HTML or `javascript:` links, csv quotes and newlines, zoom math, the canPlay mapping.
- e2e: open each sample type from Files; ←/→ across the folder; Back closes; deep link `?bestand=` opens; zoom with the keyboard; PDF PgDn and TOC; text encoding switch; video with an unsupported codec shows the message (webm vs a fake hevc mime); the public link viewer.

**Klaar als**
- Every viewer is lazy, and its chunk sizes are within budget.
- The list payload holds no text, entries or exif.
- A revoked share stops page images immediately.
- Keyboard-only usage passes the checklist; `check:i18n` 0.

---

### P5 — Foto's: tijdlijn en albums

**Doel.** EXIF and date extraction plus a backfill; a timeline by month and day that scales to 50,000 photos with a scrubber; selection and bulk actions; albums with sharing (people, groups, links) under B18.

**Dekt.** Foto-tijdlijn op datum, albums, selectie en delen; EXIF extraction (the data the viewer panel uses).

**Maakt**
- `api/app/render_meta.py` (replaces P0's stub):
  - `VERSION = 1`;
  - `image_meta(img) → {taken_at, taken_offset, exif:{make, model, lens, software, fnumber, exposure, iso, focal, focal35, flash, bias, orientation}, gps:[lat, lon, alt?]}` via `getexif()`, `get_ifd(0x8769)` and `get_ifd(0x8825)`. Dates are validated (`0000:00:00` and years outside 1900–2100 dropped), rationals become floats, strings are stripped of NUL and capped at `NAME_CHARS`.
  - `video_meta(probe) → {taken_at, gps}`, from `format.tags.creation_time`, `com.apple.quicktime.creationdate` and the ISO 6709 location.
  - `run_meta(request)`, mode `meta`: headers only and no thumbnails, many sources in one child, errors caught per file → `{"state":"ready","results":{sha: meta}}`.
- `api/app/photometa.py` (replaces P0's stub): job type `photometa` as a single chained cursor job (`key photometa:backfill`, payload `{after: sha}`). Each run handles 200 blobs where `meta_version < VERSION` and the mime is image or video, using one child via `previews.render(…, mode="meta")`, then `photodate.on_blob_meta`, then sets `meta_version` and enqueues the next run with `run_after = now`. `on_dead` marks the batch so it cannot loop. `enqueue_backfill(settings)` is called at startup (P0 wired it).
- `api/app/routers/photos.py` and `api/app/routers/albums.py` (P0 stubs, included).
- `web/src/photos/` (replacing P0's stubs where there is one):
  - `PhotosPage.tsx` (`/fotos`);
  - `timeline.ts`: pure layout (month blocks with header + ceil(count/cols) × cell, prefix sums, scrubber mapping);
  - `Scrubber.tsx`: years and months on the right edge, drag plus PageUp/PageDown/Home/End;
  - `AlbumsPage.tsx` (`/albums`, a card grid), `AlbumPage.tsx` (`/album/:id`), `AddToAlbum.tsx` (`AddToAlbumDialog`), `PublicAlbum.tsx`;
  - `api.ts`, `publicAlbumApi.ts`, `photos.css`.
- Tests: `api/tests/test_render_meta.py`, `test_photometa.py`, `test_photos.py`, `test_albums.py`, `test_album_access.py`, `test_public_album.py`; `web/src/photos/{timeline,range}.test.ts`; `e2e/test_photos.py`, `e2e/seed_photos.py` (50k rows with spread dates); `docs/fase-3/toetsenbord-p5.md`.

**Wijzigt (exclusief)**
- `photodate.py` (P0 wrote it): adjustments only, same signatures.
- `access.py`, `resolve()`: for ids without a tree grant and with no trashed ancestor, one extra query, `album_item JOIN share ON share.node_id = album_item.album_id WHERE album_item.node_id IN ids AND live_share() AND for_user(user)`, giving `Grant("read", root_id=item, chain=[Step(item, name, "read")], direct=True)`. `watchers`/`viewers_of` stay tree-based; this is documented.
- `routers/shares.py`:
  - `shareable()` accepts `kind == "album"` (parent NULL);
  - `create_share` and `change_share` reject `write` on albums (422 `album_read_only`);
  - `create_link` rejects `upload` on albums;
  - error codes.
- `routers/public.py`:
  - `inside()`: when `link.node.kind == "album"`, an item is inside if it is in `album_item` and its ancestry is live, giving `[album_id, item]`;
  - `children()` rejects albums with code `album`;
  - `PublicShare.kind` includes `album`.

**API**
```
GET  /api/v1/photos/months → { months: [{ month: "2026-10", count }] }          # desc; GROUP BY taken_day/100 op ix_node_owner_taken
GET  /api/v1/photos?month=YYYY-MM&after=&limit≤500 → { items: PhotoItem[], next }
GET  /api/v1/photos/ids?from=<cursor>&to=<cursor> → { ids: uuid[≤10000], truncated }
PhotoItem = { id, name, mime, media: "image"|"video", taken_at, taken_day, width, height, duration, etag,
              thumb, large, shared }                    # thumb/large via previews.url (P4 houdt de signatuur)
  scope: owner=user, taken_day NOT NULL, deleted_at NULL, parent_id IN live_dirs(root)   # eigen CTE die alleen door dirs loopt
  order: taken_day DESC, taken_at DESC, id DESC; cursor = base64url(taken_day|taken_at|id); gasten 403 (code guest)
POST   /api/v1/albums { name, description? } → AlbumOut                          # Node(kind="album", parent_id NULL) + Album
GET    /api/v1/albums → { albums: AlbumOut[] }                                    # eigen + gedeeld met mij (live)
GET    /api/v1/albums/{id} → AlbumOut        PATCH { name?, description?, cover_id? } → AlbumOut
DELETE /api/v1/albums/{id} → 204             # eerst share.deleted aan ontvangers (zoals shares.py:395-405), dan node weg
GET    /api/v1/albums/{id}/items?after=&limit≤500 → { items: PhotoItem[], next }  # trashed weg via één ancestry-query per pagina
POST   /api/v1/albums/{id}/items { node_ids ≤1000 } → { added, skipped: ItemResult[] }  # alleen eigen image/video: not_own|not_media|already
POST   /api/v1/albums/{id}/items/remove { node_ids } → { removed }
AlbumOut = { id, name, description, owner: OwnerRef, permission: "owner"|"read", count, cover: {thumb, large}|null,
             first_taken_at, last_taken_at, shared, created_at, modified_at }
Events: album.changed {id}, album.deleted {id} → eigenaar + access.watchers(album)
GET  /api/v1/public/album/items?after=&limit≤500 (Bearer key) → { items: PublicPhotoItem[], next }   # zonder shared/owner
Delen: bestaande POST /files/{album_id}/shares (alleen read), /links (alleen read); /shared-with-me geeft kind "album"
```

**Migratie.** Geen. Everything (album tables, node and blob columns, indexes) comes from P0.

**Tests**
- pytest:
  - EXIF from in-test JPEGs (Pillow writes EXIF and GPS), a bad date, HEIC when pillow-heif can write it;
  - video creation_time and ISO 6709;
  - the backfill: chaining, fresh preview jobs first, `on_dead` stops the loop;
  - **timeline**: no photos inside a trashed folder, cursor paging, month counts, a guest gets 403;
  - **album access**: a revoked or expired share, a group member removed, a trashed or purged item, a guest viewer, writes stay 403, adding someone else's file is refused, a public album link, GPS never in any public or shared response.
- vitest: layout offsets, scrubber ↔ offset, range selection across unloaded months (via the `ids` endpoint).
- e2e:
  - 50k seeded: scroll and scrubber smooth (p95 frame ≤ 20 ms at 1×);
  - select with Shift and Mod; "Toevoegen aan album"; share the album with a second user, who sees it under Gedeeld met mij and opens a photo read-only;
  - a public album link;
  - the Viewer with ←/→ through the timeline.

**Klaar als**
- Months and items under 100 ms server time at 50k photos (seeded).
- Every access test is green.
- The timeline shows upload dates before the backfill and EXIF dates after it.
- Keyboard-only usage passes; `check:i18n` 0.

---

### P6 — Beheer, account, delen, prullenbak en versies

**Doel.** Full user management (edit, block, delete), quotas, system status, storage use per node, the jobs view, a split `/beheer`; account storage, theme and language; share-UI additions; a full trash; better version history.

**Dekt.** Admin (users, quota, jobs, system status, storage per node); the share UI; trash; version history; the auth flows on the account side (sessions, 2FA screens, i18n).

**Maakt**
- `web/src/admin/`:
  - `Overview.tsx` (status cards: Status, Versie + uptime, Database, Wachtrij, HTTPS), `Users.tsx`, `UserDialog.tsx`, `Storage.tsx` (stacked bars per node), `Jobs.tsx`, `SecurityTab.tsx`;
  - `usePolling.ts` (pauses while the tab is hidden);
  - `api.ts`, `admin.css`.
- `web/src/shares/MyShares.tsx` (`/gedeeld/door-mij`, replaces P0's stub).
- `web/src/trash/api.ts`.
- `web/src/versions/upload.ts`: `replaceWithProgress(nodeId, file, onProgress, signal)` via XHR PUT `/files/{id}/content`.
- `api/app/usage.py`: `local_storage(settings) → NodeStorage`. The previews and uploads scandir runs in a thread, cached and refreshed from the housekeeping loop every ~10 minutes, never per request.
- `api/app/userdelete.py`: job `user.delete`.
- Tests: `api/tests/test_users_admin.py`, `test_user_delete.py`, `test_admin_status.py`, `test_admin_storage.py`, `test_jobs_admin.py`, `test_trash.py`, `test_myshares.py`, `test_sessions_device.py`, `test_messages_codes.py`; `web/src/admin/*.test.ts`, `web/src/trash/*.test.ts`; `e2e/test_admin.py`, `e2e/test_trash_versions.py`, `e2e/test_sharing_ui.py`; `docs/fase-3/toetsenbord-p6.md`.

**Wijzigt (exclusief)**
- `Beheer.tsx`: sub-routes `/beheer`, `/beheer/gebruikers`, `/beheer/opslag`, `/beheer/taken`, `/beheer/groepen` and `/beheer/beveiliging` (2FA policy plus HTTPS), with `ui/Tabs` (aria-current). SetPassword becomes a Dialog with two password fields. Fixes the quota bug (empty → `null`).
- `BeheerGroups.tsx` and `BeheerJobs.tsx`: Confirm/Prompt dialogs, CSS instead of inline styles.
- `Account.tsx`:
  - a section "Opslag" (`UsageBar` from `/auth/me`);
  - Taal (`setLocale`, saved to the account) and Thema;
  - the sessions confirm via `ui/confirm`;
  - device labels from `browser`/`os`;
  - i18n.
- `Trash.tsx`:
  - `VirtualList`, a SelectionStore, a toolbar "Terugzetten (n)", "Definitief verwijderen (n)" and "Prullenbak legen" behind confirm;
  - a "Was in" column, folder sizes, the total;
  - `useReloadOn(node.*)`;
  - Enter to restore, Shift+Delete to purge;
  - stale errors cleared.
- `Versions.tsx`: upload with progress and cancel; "Bekijken" opens the `<Viewer>` with `version`; `useReloadOn` on `node.changed` for this id; confirm dialogs.
- `ShareDialog.tsx`:
  - an expiry date for people and groups;
  - a combobox with `aria-activedescendant` and ↑↓/Enter;
  - `nodes?` multi mode (people and groups only; items you do not own shown disabled with the reason; a loop over `POST /files/{id}/shares`);
  - album rules: hide "Kan bewerken" and the upload link when `kind === "album"`;
  - confirm dialogs.
- `SharedWithMe.tsx`: `nodeHref()` (albums → `/album/:id`), `<Viewer>` for files.
- `sharing.ts` (`fmt` and `t()`), `share.css`, `account.test.ts`, `sharing.test.ts`.
- `routers/auth.py`:
  - `PATCH /users/{id}` and `DELETE /users/{id}`;
  - refuse disabled users in `login` and `refresh` (403 `account_disabled`);
  - `list_users` with one grouped usage query (removes the N+1);
  - sessions get `browser`/`os`;
  - error codes on all raises.
- `security.py`: `logged_in_user` refuses `disabled_at`.
- `useragent.py`: `device_parts()` → `(browser, os)`.
- `routers/admin.py`: `system`, `notices` and `/admin/storage`, plus the trash retention setting.
- `cluster.py`: notices with codes.
- `certs.py`, `acme.py`, `serve.py`: an error `code` plus params next to the existing Dutch text in HttpsStatus (`error_code`, `listener_error_code`).
- `jobs.py`: `counts_by_type`, `running`, `oldest_queued`, `delete_failed`, plus housekeeping hooks (the storage cache refresh, and trash retention purge in batches of 500 under `blobs.lock`).
- `routers/jobs_admin.py`, `routers/trash.py`, `routers/myshares.py`, `routers/groups.py` (codes), `cli.py` (`make-admin <email>`: makes the user an admin and unblocks them).

**API**
```
PATCH  /api/v1/users/{id} { display_name?, email?, role?, quota_bytes? (≥0, 0 = onbeperkt), disabled?: bool } → UserOut
       409 email_taken | last_admin (degraderen/blokkeren) | guest_has_files (rol→gast terwijl eigen bestanden); blokkeren
       trekt alle sessies in (revoke_other_logins(session, id, None))
DELETE /api/v1/users/{id} → 202 { job_id }    # 409 not_disabled tenzij eerst geblokkeerd; 409 last_admin
   job user.delete: announce share.deleted voor alle delingen van de gebruiker; uploads vrijgeven (uploads.release);
   alle nodes van de eigenaar (boom + kind="album") in batches van 500 onder blobs.lock: drop_versions, add_refs(-1),
   delete kinderen vóór ouders; collect_garbage; daarna de user-rij (NOOIT op de cascade van node.owner_id leunen)
GET    /api/v1/users → UserOut[] met disabled, usage: { files_bytes, trash_bytes, versions_bytes, file_count }
GET    /api/v1/admin/status → zoals nu (messages blijft) + system: { version, mode, started_at, uptime_s, db_kind, db_bytes,
       jobs: { queued, running, failed, oldest_queued_at, runner_alive }, https: { state, expires_at, days_left } }
       + notices: [{ code, params, text }]      # https-fout of verloop < 14 dagen maakt status "degraded"
GET    /api/v1/admin/storage → { nodes: NodeStorage[], totals: { logical_bytes, stored_bytes, versions_bytes, trash_bytes,
       previews_bytes, uploads_bytes, uploads_reserved_bytes, db_bytes, replicas }, users: [{ id, display_name, quota_bytes,
       files_bytes, trash_bytes, versions_bytes, file_count }] }
       NodeStorage = { name, disk: Disk|null, parts: { blobs, previews, uploads, database, other }, measured_at }
GET    /api/v1/admin/jobs?before= → { counts, by_type: [{ type, status, count }], running: [{ id, type, key, attempts, started_at }],
       oldest_queued_at, failed, next_before }
DELETE /api/v1/admin/jobs/{id} → 204 | 409 not_failed
GET    /api/v1/admin/trash → { retention_days }   PUT { retention_days: 0..3650 }   # Setting "trash_retention_days"
GET    /api/v1/trash → zoals nu (lijst; compat)
GET    /api/v1/trash/overview → { items: [{ node: NodeOut, location: [{id,name}]|null, size, restore_to: "original"|"root" }],
       count, total_bytes }
POST   /api/v1/trash/restore { ids ≤10000 } → { results: ItemResult[] }   # onder blobs.lock, notify_many
POST   /api/v1/trash/purge   { ids ≤10000 } → { results }                 # batches van 500 onder blobs.lock; collect_garbage
DELETE /api/v1/trash → { purged }                                         # idem, alles
GET    /api/v1/shares → [{ share: ShareOut, node: NodeOut, path: [{id,name}] }]   # eigen delingen en links; gasten 403
GET    /api/v1/auth/sessions → zoals nu + browser: str|null, os: str|null
```

**Migratie.** Geen; `user.disabled_at` comes from P0.

**Tests**
- pytest:
  - the last-admin rules (demote, block, delete, race with two admins);
  - email taken; role to guest while owning files;
  - a blocked user: login, refresh, the access token and the SSE stream all stop;
  - user delete: blob ref counts drop and the blob files disappear from disk, albums and shares are gone, recipients get `share.deleted`;
  - status `system` and `notices`;
  - storage parts in standalone mode (scan cached, not per request);
  - jobs by type, running, delete failed;
  - trash overview location and folder size, bulk restore and purge, empty, retention via housekeeping;
  - my shares;
  - session device parts;
  - the `make-admin` CLI.
- vitest: usage segment math, notice code → text, trash selection helpers, sharing expiry with `fmt` (existing tests stay).
- e2e:
  - admin creates, edits, blocks and deletes a user;
  - the quota bar;
  - `/beheer/*` tabs by keyboard;
  - Account language switch to EN, with the whole UI in English after the reload;
  - trash bulk and empty;
  - a version upload with progress, Bekijken, terugzetten;
  - ShareDialog expiry for people;
  - "Gedeeld door mij".

**Klaar als**
- No N+1 in `/users`; `/beheer` lazy-loaded.
- No native confirm or prompt left anywhere in P6's files.
- All admin texts in EN; `check:i18n` 0; keyboard checklist passes.

---

## 3. Fundament (P0, eerst en sequentieel)

P0 is small in behaviour but wide in surface. It is the only package that touches the hotspots. Two workers can build it at the same time, because the halves are disjoint: **P0-web** (`web/`, `e2e/`) and **P0-api** (`api/`). It merges as one PR into the fase-3 base (tag `fase3-basis`).

The app must look and work the same as now, apart from the new shell, theme switch, route table and dialogs.

### 3.1 P0-web: interfaces (frozen after P0)

**CSS.** `web/src/tokens.css` (custom properties only, light/dark) and `web/src/base.css` replace `styles.css`. A token pass over `files.css` and `share.css`: the public rules move to `public.css` (P4), and `.dialog` and `button.primary` to `base.css`. The `<style>` in PreviewPanel moves to CSS.

```
--bg --surface --surface-2 --fg --muted --line --accent --accent-fg --ok --warn --bad
--hover --selected --selected-fg --danger-bg --backdrop --focus --focus-w:2px --tag-1..7
--space-1..6 (0.25 .5 .75 1 1.5 2rem) --radius-s/m/l (4/6/10px) --text-s/m/l/xl (.875/1/1.15/1.4rem) --leading:1.45
--shadow-1/2 --z-sticky:10 --z-menu:100 --z-toast:200 (dialoog = top layer) --motion:150ms (0 bij reduced-motion)
--row-h:44px --row-h-compact:36px --cell-min:160px --font --mono
:root[data-theme=dark]{…}  :root[data-theme=light]{…}  @media (prefers-color-scheme:dark){:root:not([data-theme=light]){…}}
:focus-visible{outline:var(--focus-w) solid var(--focus);outline-offset:2px}
```
`web/public/boot.js` (synchronous in `<head>`, try/catch): sets `data-theme`, `lang` and `<meta name=theme-color>` from localStorage keys `platform-theme` and `platform-locale`.

**Router** (`router.ts`, `routes.tsx`, `links.ts`):
```ts
export interface Location { path: string; search: string; query: URLSearchParams }
export function navigate(href: string, opts?: { replace?: boolean }): void       // vergelijkt pathname+search+hash
export function back(fallback: string): void
export function useLocation(): Location
export function usePath(): string                                                  // compat
export function useQueryParam(name: string): [string | null, (v: string | null, o?: { replace?: boolean }) => void]
export function match(pattern: string, path: string): Record<string, string> | null  // "/map/:id", "/beheer/:tab?"
export function isPlainLeftClick(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): boolean
export function Link(p: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; replace?: boolean }): JSX.Element  // aria-current="page"
export function useRestoreScroll(ready: boolean): void                             // scrollY per history.state.key
export type NavSection = "files" | "photos" | "shared" | "trash" | "admin" | "account";
export function useNavSection(section: NavSection | null): void                    // pagina overschrijft de markering (gedeelde map)
export interface RouteProps { params: Record<string, string> }
export interface RouteDef { pattern: string; load: () => Promise<{ default: ComponentType<RouteProps> }>;
  title: MessageKey; section: NavSection | null; roles?: ("admin" | "user" | "guest")[]; preload?: (p: Record<string, string>) => void }
export function nodeHref(n: Pick<FileNode, "id" | "kind" | "parent_id">): string
  // dir → /map/id · album → /album/id · file → /map/<parent>?bestand=id, of /gedeeld?bestand=id als parent onbekend
```
**Routes** (all defined in P0; each one lazy):

| Path | Page | Owner | Notes |
|---|---|---|---|
| `/` | Files | P2 | guests are sent to `/gedeeld` |
| `/map/:id` | Files | P2 | |
| `/zoeken` | Search | P2 | |
| `/gedeeld` | SharedWithMe | P6 | |
| `/gedeeld/door-mij` | MyShares | P6 | stub |
| `/prullenbak` | Trash | P6 | |
| `/fotos` | photos/PhotosPage | P5 | stub |
| `/albums` | photos/AlbumsPage | P5 | stub |
| `/album/:id` | photos/AlbumPage | P5 | stub |
| `/beheer/:tab?` | Beheer | P6 | admin only |
| `/account` | Account | P6 | |
| `/s/:token` | Public | P4 | outside the login |
| `*` | NotFound | P0 | plus NoAccess (P0) |

After each navigation: `document.title`, focus on `<PageTitle>`, an announcement in the live region. A failed chunk load reloads once (sessionStorage flag, 10 s). At boot, the route chunk for `/` or `/map` is prefetched in parallel with `/auth/refresh`, and `preload` starts the listing.

**i18n** (`i18n/`):
```ts
export type Locale = "nl" | "en";
// areas/<area>.nl.ts:  export default { title: "Bestanden", selected: { one: "{n} geselecteerd", other: "{n} geselecteerd" } } as const
// areas/<area>.en.ts:  const en: Translation<typeof nl> = {...}   → ontbrekende sleutel = tsc-fout
export type MessageKey;                                        // "browser.selected" — dot-paden over alle areas
export function t(key: MessageKey, vars?: Record<string, string | number>): string   // {name}; plural via vars.n + Intl.PluralRules
export function locale(): Locale
export function initI18n(): Promise<void>                      // vóór de eerste render; en.ts als lazy chunk
export function setLocale(l: Locale, o?: { account?: boolean }): Promise<void>   // localStorage (+ PATCH /auth/me), dan reload
export function errorText(e: unknown): string                  // nl: servertekst; en: errors[code].en met params, anders servertekst
export const fmt: { date(v, style?: "short"|"medium"|"long"): string; dateTime(v): string; time(v): string;
  wallClock(v, style?): string /* timeZone UTC, voor taken_at */; bytes(n: number): string /* 1024-basis, "1,5 KB" in nl */;
  number(n, o?: Intl.NumberFormatOptions): string; percent(f: number): string; relative(v, now?: number): string;
  duration(s: number): string; list(items: string[]): string };
export const collator: Intl.Collator;                          // numeric, sensitivity base
export function fold(s: string): string;                       // = app/text.py fold
// errors/<area>.ts: export default { read_only: { en: "You can only view this" }, … } satisfies ErrorCatalog
```
- Priority: account (`user.locale`) > `localStorage platform-locale` > `navigator.languages` (`nl*` → nl, otherwise en) > nl.
- After login, if the account locale differs, save it and reload once.
- `formatBytes`/`formatDate` in `api.ts` delegate to `fmt`, so the existing tests stay green.
- `scripts/check-i18n.mjs [--strict] [--files …]`: reports JSX text and literal `aria-label|title|placeholder|alt` outside `t()`; `// i18n-ignore` opts a line out.
- P0 creates every area file and error file listed in §2.0, with the route-title keys already in them.

**UI** (`ui/`), on top of the existing Dialog. All of these are real implementations:
```ts
Button(p: ButtonHTMLAttributes & { variant?: "default"|"primary"|"danger"|"ghost"|"link"; icon?: IconName; busy?: boolean })
IconButton(p: ButtonHTMLAttributes & { icon: IconName; label: string; pressed?: boolean })     // label = aria-label + title
type IconName = "folder"|"file"|"image"|"video"|"audio"|"pdf"|"document"|"archive"|"album"|"upload"|"folder-upload"|"download"|
  "share"|"link"|"trash"|"restore"|"more"|"close"|"check"|"chevron-left"|"chevron-right"|"chevron-up"|"chevron-down"|"search"|
  "filter"|"grid"|"list"|"sort-asc"|"sort-desc"|"zoom-in"|"zoom-out"|"rotate"|"fullscreen"|"info"|"play"|"pause"|"settings"|
  "user"|"logout"|"sun"|"moon"|"language"|"keyboard"|"warning"|"error"|"copy"|"move"|"rename"|"tag"|"history"|"new-folder"|"photo"
Icon(p: { name: IconName; size?: 16|20|24|32; label?: string })                                 // eigen 24px-paden, currentColor
interface MenuItem { id: string; label: string; icon?: IconName; shortcut?: Combo; disabled?: boolean; reason?: string;
  danger?: boolean; checked?: boolean; separatorBefore?: boolean; items?: readonly MenuItem[]; onSelect?: () => void }
Menu(p: { items: readonly MenuItem[]; anchor: { x: number; y: number } | HTMLElement; label: string;
  onClose(reason: "select"|"escape"|"outside"|"tab"): void })   // role=menu, roving, ↑↓ Home End, type-ahead, → ← submenu,
                                                                  // geklemd/gespiegeld; host = closest dialog[open] ?? body
MenuButton(p: { items: readonly MenuItem[] | (() => readonly MenuItem[]); label: string; icon?: IconName; children?: ReactNode })
useContextMenu<T>(get: (t: T) => readonly MenuItem[] | null, label: string): { bind(t: T): DOMHandlers; open(t: T, at): void; element: ReactNode }
                                                     // contextmenu, Shift+F10 / ContextMenu-toets, long-press 500 ms / 8 px
useLongPress(cb: (at: { x: number; y: number }) => void, o?: { ms?: number; tolerance?: number }): PointerHandlers
confirm(o: { title: string; message?: ReactNode; confirm: string; cancel?: string; danger?: boolean; typeToConfirm?: string }): Promise<boolean>
prompt(o: { title: string; label: string; initial?: string; confirm: string; type?: "text"|"password"|"number";
  validate?: (v: string) => string | null; selectBaseName?: boolean }): Promise<string | null>
DialogHost(): JSX.Element        // in App; focus op de veilige knop; Enter/Escape
Dialog(p: { title; onClose; children; size?: "s"|"m"|"l"|"full"; actions?: ReactNode })   // pusht shortcut-scope "dialog"
toast(msg: string, o?: { kind?: "info"|"ok"|"error"; action?: { label: string; run(): void }; timeoutMs?: number; id?: string }): () => void
announce(msg: string, politeness?: "polite"|"assertive"): void;  ToastRegion(): JSX.Element
Tabs(p: { label: string; tabs: { id: string; label: string; href?: string; badge?: string }[]; current: string; onSelect?(id: string): void })
Field(p: { label: string; hint?: ReactNode; error?: string | null;
  children(c: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }): ReactNode })
Spinner(p: { label?: string }); Skeleton(p: { width?: string; height?: string; lines?: number })
EmptyState(p: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode })
PageTitle(p: { children: string; actions?: ReactNode })          // h1 tabIndex=-1, document.title
UsageBar(p: { used: number; total: number | null; label: string })   // <meter>; warn ≥ 80 %, bad ≥ 95 %
ThemeSwitch(), LanguageSwitch(), ShortcutHelp(), OfflineScreen(p: { onRetry(): void })
// virtualisatie
fixedRange(scrollTop, viewport, rowH, count, overscan): { start: number; end: number }                    // puur
variableRange(offsets: readonly number[] /* prefix sums, length n+1 */, scrollTop, viewport, overscan): { start; end }
useVirtualRows(o: { count: number; rowHeight: number | ((i: number) => number); overscan?: number;
  scroller?: "window" | RefObject<HTMLElement>; listRef: RefObject<HTMLElement> }):
  { start: number; end: number; totalHeight: number; offsetOf(i: number): number; scrollToIndex(i: number, align?: "auto"|"start"|"center"): void }
VirtualList<T>(p: { count: number; get(i: number): T | undefined; rowHeight: number; getKey(item: T | undefined, i: number): string;
  renderRow(item: T | undefined, i: number, style: CSSProperties): ReactNode; header?: ReactNode; overscan?: number;
  scroller?: "window" | RefObject<HTMLElement>; role?: "grid"|"listbox"|"list"; ariaLabel: string; onRangeChange?(s: number, e: number): void })
useColumns(ref: RefObject<HTMLElement>, minCell: number, gap?: number): { columns: number; cellWidth: number }  // ResizeObserver
// selectie
interface SelectionState { selected: ReadonlySet<string>; anchor: string | null; focus: string | null }
clickSelect(s, order: readonly string[], id: string, m: { shift: boolean; toggle: boolean }): SelectionState      // puur
keySelect(s, order, toId: string, m: { shift: boolean; toggle: boolean }): SelectionState
class SelectionStore { getSnapshot(); subscribe(cb): () => void; set(s); clear(); selectAll(order); toggle(id); has(id) }
useSelection(store: SelectionStore): SelectionState
// toetsenbord (ui/keys.ts)
type Combo = string;   // "mod+a" "shift+f10" "delete" "?" "arrowdown" "g"
matchKey(e: KeyboardEvent, c: Combo, isMac?: boolean): boolean;  formatCombo(c: Combo, isMac?: boolean): string
isTypingTarget(t: EventTarget | null): boolean                      // input, textarea, select, contenteditable, IME-compositie
useShortcuts(b: { combo: Combo | Combo[]; run(e: KeyboardEvent): void; label?: string; when?(): boolean; allowInInput?: boolean }[],
  o?: { scope?: string; enabled?: boolean }): void                  // één listener; scope-stack; dialoog blokkeert lager
pushScope(name: string): () => void;  activeShortcuts(): { scope: string; combo: Combo; label: string }[]
type Move = "up"|"down"|"left"|"right"|"home"|"end"|"pageup"|"pagedown"
moveIndex(cur: number, m: Move, count: number, columns?: number, pageRows?: number): number;  keyToMove(e): Move | null
useRovingFocus(o: { count; index; setIndex(i); columns?; scrollToIndex(i); getElement(i): HTMLElement | null }): KeyHandler
```

**App shell** (`App.tsx`):
- topbar with brand, nav (aria-current) and the account menu: Account, Thema, Taal, Sneltoetsen (`?`), Uitloggen;
- a "Naar inhoud" skip link;
- `<UploadTray/>` slot (P3), `<AppStatus/>` slot (P1), `<DialogHost/>`, `<ToastRegion/>`, `<ShortcutHelp/>`;
- boot: a network error leads to `<OfflineScreen>` (retried on `online`), not to Login;
- the `totp_setup_required` and recovery-code flows are unchanged.

**Other frozen modules:**
- `perf.ts`: `markReady(what: string): void`, which sets `performance.mark("app:ready:"+what)` once and logs it with `?perf`.
- `api.ts`:
  - `request<T>(method, path, body?, init?)` and `rawRequest(path, init)` become public;
  - `tusCreate(parent, name, size, meta?: Record<string,string>)`;
  - `FileNode.kind: "file"|"dir"|"album"`, `User.locale: Locale|null`, `User.disabled?: boolean`, `ApiError.params?`;
  - `errorFromBody` reads `params`;
  - `lastRefresh: { setupRequired: boolean|null }` (from the 401 of `/auth/refresh`);
  - `setAccountLocale(l)`.
- `PublicLink.request<T>(path, init)` becomes public (`publicApi.ts`, before P4 takes it over).
- `crossApi.ts`:
```ts
export interface ItemResult { id: string; ok: boolean; code?: string; message?: string; node?: FileNode }
export interface BatchResult { results: ItemResult[] }
export const crossApi: {
  trash(ids: string[]): Promise<BatchResult>;                                            // P2 · POST /api/v1/batch/trash
  move(ids: string[], parentId: string, conflict?: "fail"|"rename"): Promise<BatchResult>; // P2
  copy(ids: string[], parentId: string): Promise<BatchResult>;                            // P2
  archiveLink(ids: string[], name: string): Promise<{ url: string; expires_in: number }>; // P2 · POST /api/v1/archive/link
  restore(ids: string[]): Promise<BatchResult>;                                           // P6 · POST /api/v1/trash/restore
  addToAlbum(albumId: string, nodeIds: string[]): Promise<{ added: number; skipped: ItemResult[] }>; // P5
};
```
- `viewer/types.ts`, `viewer/index.ts` and `viewer/param.ts`:
```ts
export interface ViewerItem { id: string; name: string; mime: string | null; size: number; modified_at: string; etag?: string;
  version?: { id: string; label: string } }
export type ViewerSource = { readonly kind: string };               // alleen P4 maakt ze
export interface ViewerProps { items: readonly ViewerItem[]; index: number; onIndexChange(i: number): void; onClose(): void;
  onNeedMore?(direction: 1 | -1): void; source?: ViewerSource }
export const Viewer: LazyExoticComponent<ComponentType<ViewerProps>>    // viewer/index.ts → import("./Viewer")
export function useViewerParam(): [string | null, (id: string | null) => void]  // ?bestand=; open = push, volgende = replace,
                                                                                 // sluiten = back() of replace bij deep link
```
- **Stubs with frozen signatures** (the owner replaces the implementation):
  - `viewer/Viewer.tsx`: wraps the current PreviewPanel for `items[index]` (P4).
  - `upload/index.ts`: `UploadButtons({folderId, canUpload})`, `hasFiles(dt)`, `uploadDropped(dt, folderId)` (`dt.files` → `uploadQueue.add`), `UploadTray()` returning null (P3).
  - `pwa/register.ts` (`registerServiceWorker()` no-op) and `pwa/AppStatus.tsx` (null) (P1); `pwa/shell.css` (empty) (P1).
  - `browser/preload.ts` (`preloadFolder()` no-op) (P2).
  - `photos/PhotosPage.tsx`, `AlbumsPage.tsx`, `AlbumPage.tsx` ("Komt eraan" with `EmptyState`), `AddToAlbum.tsx` (`AddToAlbumDialog({nodeIds, onClose})`), `PublicAlbum.tsx` (`{link, share}`), and the dispatch `share.kind === "album"` in Public.tsx (P5).
  - `shares/MyShares.tsx` (P6).
  - `ShareDialog` props type gains `nodes?: FileNode[]`, ignored for now (P6).
  - `scripts/budget.mjs`, which prints only (P1).
- **Existing pages adapted to the route table:** Files without `onShared`, using `useNavSection`; Files and SharedWithMe use `<Viewer>` instead of PreviewPanel. `thumb.test.ts` is split, with the PreviewPanel tests moving to `viewer/preview.test.ts` (P4). `events.ts`: `affectsFolder`/`affectsSharedWithMe` read `data.ids ?? [data.id]`.
- **Build setup:** `tsconfig.json` gets project references, with `tsconfig.worker.json` (lib WebWorker) for `src/**/*.worker.ts` and `src/pwa/sw.ts`; the main tsconfig excludes those files. `package.json` scripts: `check:i18n`, `budget`.

### 3.2 P0-api: contracts (frozen after P0)

**Migration `0006_fase3.py`** (down_revision `0005`). Plain `ADD COLUMN`, no batch mode (the rule from 0004/0005). It has a downgrade.
```
user:        locale VARCHAR(5) NULL; disabled_at DATETIME NULL
blob:        taken_at DATETIME NULL; lat FLOAT NULL; lon FLOAT NULL; meta_version INT NOT NULL DEFAULT 0;
             render_version INT NOT NULL DEFAULT 0
node:        taken_at DATETIME NULL; taken_day INT NULL
             INDEX ix_node_owner_taken(owner_id, taken_day, taken_at, id); INDEX ix_node_blob(blob_id);
             INDEX ix_node_parent_order(parent_id, kind, search_name, id)
upload:      client_mtime DATETIME NULL
album:       id UUID PK FK node.id ON DELETE CASCADE; description TEXT NOT NULL DEFAULT ''; cover_id UUID NULL FK node.id ON DELETE SET NULL
album_item:  album_id FK node.id CASCADE; node_id FK node.id CASCADE; added_by NULL FK user.id SET NULL; added_at DATETIME;
             PK(album_id, node_id); INDEX ix_album_item_node(node_id)
data:        node.taken_at/taken_day voor bestaande image/*|video/*-bestanden = wall_clock(modified_at, settings.timezone),
             in Python-batches van 500 (zoals 0004:106-110)
```
`models.py` gets the matching columns plus `Album` and `AlbumItem`. The comment documents the floating-time convention and that `kind` can now be `album`.

**Python contracts:**
```python
# app/errors.py
def fail(status: int, code: str, message: str, *, headers: dict | None = None, **params) -> HTTPException
    # detail = {"message", "code", "params"?}; main.py: RequestValidationError → 422
    # {"detail": {"message": "Ongeldige invoer", "code": "invalid_input", "params": {"field": "body.name"}, "errors": [...]}}
# app/config.py: timezone: str = "Europe/Amsterdam"; page_render_workers: int = 2; page_cache_mb: int = 2048
# app/__init__.py: __version__ (main.py importeert hem); main.py lifespan: app.state.started_at; include van de stub-routers
#   batch, archive (P2), folder_tree (P3), viewer (P4), photos, albums (P5), trash, jobs_admin, myshares (P6);
#   await previews.enqueue_outdated(settings) en await photometa.enqueue_backfill(settings) (stubs → 0), elk in try/except
# app/routers/trash.py  ← list_trash/restore/purge letterlijk verhuisd uit files.py (zelfde paden en gedrag)
# app/routers/jobs_admin.py ← admin_jobs/retry_failed/retry_job letterlijk verhuisd uit routers/previews.py
# app/routers/auth.py: UserOut.locale, UserOut.disabled; PATCH /api/v1/auth/me {locale: "nl"|"en"|null} → UserOut;
#   401 van /auth/refresh: detail {"message", "code": "not_logged_in", "params": {"setup_required": bool}}
# app/photodate.py  (daarna van P5)
def wall_clock(moment: datetime, tz: str) -> datetime          # UTC-moment → zwevende lokale tijd, als UTC opgeslagen
def day_of(taken_at: datetime) -> int                          # YYYYMMDD
def is_media(mime: str | None) -> bool                         # image/* of video/*
def stamp_values(mime, blob_taken_at: datetime | None, fallback: datetime, tz: str) -> dict   # {"taken_at", "taken_day"} of None's
def stamp(node: Node, *, blob_taken_at, fallback, tz) -> None
async def on_blob_meta(session: AsyncSession, blob: Blob, meta: dict) -> None
    # haalt "taken_at" en "gps" uit meta (GPS komt NOOIT in preview_meta), zet blob.taken_at/lat/lon/meta_version,
    # en UPDATE node SET taken_at, taken_day WHERE blob_id = sha AND taken_day IS NOT NULL (EXIF wint van terugval)
# app/routers/files.py (daarna van P2)
async def place_file(..., client_mtime: datetime | None = None) -> Node     # stamp in beide takken; bestaande blob.taken_at telt
#   copy(): copied() neemt taken_at/taken_day mee; restore_version(): stamp met de blob van de versie;
#   PUT /files/{id}/upload?mtime=<ms> → client_mtime
async def notify_many(session, nodes: Sequence[Node], type: str, *,
                      old_parents: Mapping[uuid.UUID, uuid.UUID | None] | None = None, seen=None, below=None) -> None
    # na de commit; zelfde ontvangers en parent_id-zichtbaarheid als notify(); één event per (ontvanger, ouder, oude ouder)
    # met {"ids": [≤500], "parent_id", "old_parent_id"?}; één node → het oude formaat {"id", ...}
# app/render_meta.py (stub, daarna van P5): VERSION = 0; image_meta(img) -> {}; video_meta(probe) -> {};
#   run_meta(request) -> {"state": "ready", "results": {}}
# app/photometa.py (stub, P5): async def enqueue_backfill(settings) -> int: return 0
# app/previews.py: async def enqueue_outdated(settings) -> int: return 0 (stub, P4)
```

### 3.3 Hook lines P0 places in P4-owned files (P4 must not remove them)
- `render.py`: `image(source, out, max_pixels, meta=None, *, exif=False)`. `render()` passes `exif=True` only for kind `image` (not for epub or audio covers). Inside the open image: `if exif: meta.update(render_meta.image_meta(img))`. In `video()`: `meta.update(render_meta.video_meta(probe))`. In `main()`: `if request.get("mode") == "meta": result = render_meta.run_meta(request)`.
- `previews.py` `_store`, before `session.commit()`: `await photodate.on_blob_meta(session, blob, meta)`.

### 3.4 e2e and measurement harness (P0, frozen)
- `e2e/conftest.py`:
  - starts `python -m app.serve` with a temporary `PLATFORM_DATA_DIR`, `PLATFORM_WEB_DIST=web/dist` and free ports;
  - fixture `admin`: setup, then 2FA via `app.totp` codes, then `require_2fa` off;
  - `user_factory`;
  - `context_as(user)`: logs in through the browser context's request, so the refresh cookie sits in the context;
  - Chromium from `/opt/pw-browsers` (env `PLAYWRIGHT_BROWSERS_PATH`).
- `e2e/seed.py`: `seed_folder(owner, n, mix)` inserts rows directly into the SQLite of the stopped server (one blob, `ref_count` += n), for 10k in seconds.
- `e2e/perf/first_view.py` and `scroll.py` (§4).
- `e2e/test_smoke.py`: login → Files → upload → Viewer stub → theme switch → logout.
- Run with: `cd api && uv run --with playwright pytest ../e2e`.

### 3.5 Tests and klaar als P0
- **pytest:**
  - `test_migration_0006.py`: a 0005 DB with data → upgrade → fallback dates set → downgrade;
  - `test_errors.py`: the validation shape and `fail`;
  - `test_locale.py`;
  - `test_photodate.py`: stamp on upload, tus, copy and version restore; fan-out; GPS kept out of `preview_meta`;
  - `test_notify_many.py`: grouping, ≤ 500 per event, recipients including share watchers and `below`;
  - `test_error_catalog.py`: every code in `api/app` (`fail(`, `"code":`) has an `en` entry in `web/src/i18n/errors/*.ts`. P0 adds the ~31 existing codes.
- **vitest:** `router.test.ts` (match, query, modifier keys), `i18n.test.ts` (key and placeholder parity for every area, plurals, fmt in nl and en), `keys.test.ts`, `virtual.test.ts`, `selection.test.ts`, `menu.test.ts` (next enabled item, type-ahead), `toast.test.ts`.
- **Klaar als:**
  - all existing tests green; ruff, tsc and build clean; `e2e/test_smoke.py` green;
  - `check:i18n` reports 0 for P0's files;
  - no `window.confirm`/`prompt` in P0's files;
  - the theme toggle works without a flash;
  - the decisions are recorded as B19 onwards in `docs/fase-0/beslissingen.md`, and `docs/fase-3/README.md` has a skeleton.

---

## 4. Prestatie

**Without Lighthouse as a dependency.** Everything runs from `e2e/perf/` with Python Playwright and CDP, against the real server (`python -m app.serve`, or the built image in CI) and a production build. It needs no npm package and nothing extra in the image.

1. **Size budget**, a hard CI gate (`npm run budget`, P1). React plus react-dom is about 60 KB gzip, which leaves about 40 KB for the shell.

| What | Limit (gzip) |
|---|---|
| Initial JS (entry plus static imports, including the vendor chunk) | ≤ 100 KB |
| Files route chunk | ≤ 45 KB |
| Any other lazy chunk | ≤ 50 KB |
| Highlighter or worker chunk | ≤ 60 KB |
| Initial CSS | ≤ 12 KB |

2. **First view < 1.5 s on LAN** (`first_view.py`, P0; a gate for P1, P2 and P4).
   - Profile: CDP latency 2 ms, 100 Mbit/s, CPU 1×, a fresh browser context (cold: no SW, no HTTP cache), and also warm.
   - Measured: `navigationStart` → `performance.mark("app:ready:<what>")`, set by the page after its first meaningful paint (folder rows, login form, public content).
   - Pages: `/` (logged out), `/map/<10k>` (logged in, seeded), `/s/<folder link>`.
   - Gate: median of 5 < 1,500 ms and p95 < 2,000 ms.
3. **Lighthouse > 90** (`lighthouse_like.py`, P1).
   - **Metrics:**
     - FCP from paint entries;
     - LCP from a buffered `largest-contentful-paint` observer;
     - CLS from layout-shift session windows (5 s / 1 s, excluding `hadRecentInput`);
     - TBT as Σ(duration − 50 ms) over long tasks between FCP and TTI, where TTI is the start of the first 5 s window with no long task and ≤ 2 open requests (CDP Network events);
     - Speed Index from CDP `Page.startScreencast` frames, with visual progress via histogram distance to the last frame, as speedline does (using Pillow from the API env).
   - **Score:** the same log-normal curves and weights as Lighthouse. Check these values once against DevTools.

     | Metric | Weight | Mobile p10 / median | Desktop p10 / median |
     |---|---|---|---|
     | FCP | 0.10 | 1800 / 3000 ms | 934 / 1600 ms |
     | Speed Index | 0.10 | 3387 / 5800 ms | 1311 / 2300 ms |
     | LCP | 0.25 | 2500 / 4000 ms | 1200 / 2400 ms |
     | TBT | 0.30 | 200 / 600 ms | 150 / 350 ms |
     | CLS | 0.25 | 0.10 / 0.25 | 0.10 / 0.25 |

   - **Throttling:** mobile is CDP latency 562.5 ms, 1,474.56 kbit/s down, 675 kbit/s up, CPU 4×, viewport 412×823 at DPR 1.75. Desktop is 40 ms, 10 Mbit/s, CPU 1×, 1350×940.
   - **Gate** (Keuze 30): mobile ≥ 90 on `/` and `/s/<link>`; desktop ≥ 90 on `/map/<10k>`; the median of 5 runs counts.
   - **Cross-check:** at integration, Jonas or the integrator runs the Lighthouse panel built into Chrome DevTools once on the same pages and records the result in `docs/fase-3/prestaties.md`.
4. **10,000 files scroll smoothly** (`scroll.py`, P0; a gate for P2 and P5).
   - Seed 10k (list and grid) and 50k photos.
   - Inject a rAF frame recorder and a long-task observer, then scroll with `mouse.wheel(0, 400)` every 50 ms for 6 s, then keyboard PageDown × 20 and End.

   | Check | Limit |
   |---|---|
   | p95 frame interval at CPU 1× | ≤ 20 ms |
   | p95 frame interval at CPU 4× | ≤ 34 ms |
   | Long tasks during scrolling | none over 50 ms |
   | Rendered `[role=row]` | ≤ 200 |
   | DOM nodes | ≤ 4,000 |
   | After End | row 10,000 (`aria-rowindex`) visible and focused |

5. **Where it runs.**
   - Gates 2–4 run hard in the sandbox and at the integration run (stable hardware).
   - In CI the `perf` job is report-only (runner noise); the budget (1) and the functional e2e are hard gates there.
   - Results go to `docs/fase-3/prestaties.json` and a table in `prestaties.md`.
6. **What makes it achievable.**
   - Precompressed assets; immutable `/assets` and no-cache plus 304 for the rest.
   - A static shell in `index.html`, so FCP does not wait for JS.
   - Lazy routes and viewers, plus a stable vendor chunk.
   - Route chunk and listing fetched in parallel with `/auth/refresh`.
   - The first page of 200 rows; light preview payloads; previews only for visible ids.
   - No web fonts; virtualization; and gzip on the large listing JSON.

---

## 5. Volgorde

1. **P0 Fundament, alone.**
   - P0-web and P0-api in parallel (disjoint), merged as one PR into the fase-3 branch, tagged `fase3-basis`.
   - Gate: §3.5.
   - From then on the frozen files only change through the **P0-hotfix-lane**: a small PR on the base that every package rebases on.
2. **Parallel wave**, P1 to P6, each in its own git worktree off `fase3-basis`. Each package delivers one PR with its tests and its `docs/fase-3/<pkg>.md` plus keyboard checklist.
3. **Merge order:** P1 (serving, CI, budget gate) → P3 → P2 → P4 → P6 → P5.
   - P5 goes last because of the security-central change in `access.py`, and because its e2e uses P2's batch and zip, P4's Viewer and P6's ShareDialog album rules.
   - Before each merge: rebase on the integration branch.
   - After each merge: full `pytest`, `ruff`, `tsc -b`, `vitest`, `build`, `budget`, `e2e/test_smoke.py` and that package's e2e.
   - Conflicts can only appear outside the ownership table. Each one is a bug in this design: fix it in the PR and note it.
4. **Integration (sequential, after all merges).**
   - **Tighten:**
     - add the pytest that bans `HTTPException` without a code in `api/app`;
     - switch `check:i18n --strict` on in CI;
     - remove any remaining stubs and compatibility layers (PreviewPanel re-exports, `NodeActions` adapter if unused).
   - **Full e2e** against the built platform image in a container on `localhost`:
     - first-run setup with 2FA → admin creates a second user and a guest;
     - upload a folder with subfolders (resume after a reload);
     - seed 10k in one folder → scroll, select, bulk move, copy and trash plus undo, zip download, context menu and keyboard-only operation;
     - open every viewer type from a sample set (JPEG with EXIF and GPS, animated GIF, HEIC, PDF of 50 pages, mp4/webm, mp3, cp1252 txt, md, csv, json, docx, xlsx, pptx, odt);
     - photo timeline with backfilled EXIF dates → create an album → share it with the second user (read-only) and as a public link (GPS not visible) → the guest sees only what is shared;
     - share a selection with a group; "Gedeeld door mij";
     - trash: bulk restore and empty → blobs gone from disk; versions: upload with progress, view, restore;
     - admin: block (sessions end), delete (job, blobs cleaned), quota bar, storage per node, jobs;
     - switch to EN → every screen English, API errors translated;
     - PWA: SW active on localhost, offline reload shows the shell, the update toast works;
     - the keyboard checklist from every `toetsenbord-*.md`, done with the keyboard only.
   - **Performance:** `e2e/perf/run_all.py` (gates from §4) plus one manual DevTools Lighthouse run; results in `docs/fase-3/prestaties.md`.
   - **Close-out:**
     - `docs/fase-3/README.md` (what is done, known limits: album viewers get no live `preview.ready`; no PDF text layer; no transcoding);
     - fase-3 checkboxes in `docs/stappenplan.md`;
     - B19 onwards final;
     - version 0.5.0 in `pyproject.toml`, `app/__init__.py` and `package.json`.
   - **Done when:** every item in the fase-3 list in the stappenplan is covered by a merged package, and all four "Klaar als" criteria are proven through the measurements and e2e above.