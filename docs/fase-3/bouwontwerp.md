> Werkdocument: het bouwontwerp van fase 3, revisie 2. Waar 'revisie 1' staat, zie bouwontwerp-r1.md.

# Fase 3 (web-app): bouwontwerp, revisie 2

Base: branch `claude/stappenplan-uitwerken-lu4uv3` @ `45411e8` (v0.4.0, migrations 0001–0005). Revisie 2 fixes the valid issues from the critic; see §6 for the mapping and for what I rejected. I checked these claims against the code and the sandbox. Where a reader differed, I followed the code.

**Bundle and build**
- `styles.css` is 3,760 B; the 7,066 B figure is the built CSS.
- The built JS is 320,175 B, or 94,722 B gzip -9. React-dom alone is about 60 KB gzip.
- Vite 8.3.4 with Rolldown 1.2.14: chunk config goes in `build.rolldownOptions.output.codeSplitting`.

**Jobs and previews**
- `jobs.enqueue` dedupes on key against `ACTIVE = ("queued", "running")` and has no `run_after` parameter.
- `previews.make()` returns early when `blob.preview in DONE`.

**Auth and limits**
- `security.login_limiter` allows 10 per 300 s; `public.open_limiter` allows 20 per 60 s.
- `require_2fa` defaults to `True`.
- `auth.use_code` also accepts recovery codes.
- The SSE stream re-checks `family_revoked`.
- `files.download` resolves the token user with `session.get(User)`, with no state check.

**Errors**
- There are 144 `HTTPException(` raises in `api/app` and 24 distinct codes.
- 34 test assertions compare `["detail"]` to a plain string.

**Nodes**
- `NodeOut.kind` is `str` and `Node.kind` is `String(8)`.
- The user root is found through `user.root_id`, so album nodes with `parent_id` NULL do not collide with roots.

**Render child.** It gets a stripped environment (`PATH`, `LANG`, `HOME`), so it has no timezone.

**Sandbox browser**
- Chromium 141 (rev 1194) is at `/opt/pw-browsers/chromium`. The installed Python Playwright 1.64 expects rev 1248, but it drives this Chromium fine through `executable_path` (I checked).
- `canPlayType` gives `''` for H.264 and AAC, and `probably` for VP9/Opus, MP3 and AV1.

---

## 1. Keuzes

Each line gives the default and why.

1. **Geen PDF.js, hls.js, Workbox, TanStack, virtualisatie-, i18n-, UI- of state-bibliotheken.** Alles zelf (B14). De stack-tabel in het stappenplan (regel 194) geldt niet voor fase 3.
2. **Geen nieuwe afhankelijkheden in `web/package.json` of `api/pyproject.toml`, ook geen dev-deps.** UI-logica komt in pure modules en wordt met vitest (node) getest. DOM-gedrag testen we met Playwright e2e (Vraag 1). `check-i18n.mjs` gebruikt de parser van het al aanwezige `typescript`.
3. **PDF.** De server rendert pagina's op aanvraag naar WebP met het al aanwezige pypdfium2. Navigatie, zoom en inhoudsopgave zitten erin; een tekstlaag niet (die komt in fase 6). "Openen in nieuw tabblad" blijft de uitweg naar de native viewer.
4. **Video en audio.** Native `<video>`/`<audio>` op de bestaande downloadlink met Range. Of een formaat speelt, beslist de browser: MediaError 3/4 geeft "kan niet afspelen" plus een downloadknop. Geen transcoding (fase 5).
5. **Office-preview.** Een eigen vereenvoudigde lezer (stdlib zipfile + ElementTree) voor docx, xlsx, pptx, odt, ods en odp: de ingebedde thumbnail plus tekst, tabellen en dia-teksten zonder opmaak. doc, xls, ppt en rtf krijgen in fase 3 geen preview.
6. **Afbeeldingen.** Zoom en pan bouwen we zelf. HEIC/HEIF/TIFF krijgen een extra WebP van 2048 px.
7. **EXIF zonder locatie.** Met Pillow en ffprobe lezen we de opnamedatum, camera, objectief en belichting uit. Ze komen in `blob.preview_meta.exif` en de datum in `blob.taken_at`. **GPS lezen en bewaren we in fase 3 niet**; locatie en kaart horen bij fase 6 ("EXIF en GPS: … locatie; kaartweergave"). Fase 6 verhoogt `render_meta.VERSION`, zodat dezelfde backfill alles opnieuw leest.
8. **Tijdlijn.** Alle eigen afbeeldingen en video's uit de hele boom, zonder de prullenbak. Zonder EXIF-datum geldt de `lastModified` van de client, en anders het uploadmoment. Foto's van anderen zie je via albums en "Gedeeld met mij".
9. **Opnamedatum.**
   - EXIF zonder offset geldt als "zwevende" lokale tijd.
   - Een tijd met offset (EXIF `OffsetTimeOriginal`, QuickTime `creationdate`) houdt zijn lokale kloktijd.
   - Een UTC-tijd (ffprobe `creation_time …Z`) zet het API-proces om naar `PLATFORM_TIMEZONE` (standaard Europe/Amsterdam); het render-subproces heeft geen tijdzone.
   - Alles wordt opgeslagen als UTC-kolom met de kloktijd en getoond met `timeZone: "UTC"`. De dag staat als integer `YYYYMMDD`.
10. **Album = een Node met kind `album` buiten de boom** (`parent_id` NULL), plus de tabellen `album` en `album_item`.
    - Delen met personen, groepen en links (B18) werkt zonder nieuwe deelcode. Gedeelde albums zijn alleen te bekijken.
    - Je voegt alleen eigen foto's en video's toe; er wordt niets gekopieerd.
    - **Album-nodes zijn geen boomnodes:** elk boom-endpoint (kopiëren, verplaatsen, hernoemen, prullenbak, tags, versies, zip, zoeken) weigert ze met 422 `album`.
11. **Bulkacties via batch-endpoints.** Eén verzoek, één transactie per 500 items, een resultaat per item en gegroepeerde events. "Ongedaan maken" is er alleen na "Naar prullenbak".
12. **Downloaden als zip** (selectie, map of album).
    - Streaming met stdlib zipfile, `ZIP_STORED` en ZIP64.
    - Tot 10.000 top-ids per link; toegang in bulk gecontroleerd.
    - Het token leeft 10 minuten in het geheugen; één node is genoeg.
13. **Mapupload naar een bestaande map met dezelfde naam** voegt samen.
    - Botst een mapnaam met een *bestand*, dan mislukken de bestanden onder dat pad zichtbaar in de lade.
    - Dubbele bestanden vraagt de app één keer per batch, na een naamcontrole zonder uploads aan te maken: Vervangen (de vorige wordt een versie), Beide houden of Overslaan.
14. **Een selectie delen** kan alleen met personen of groepen, tot 100 items (anders de hint "deel de map"). Links blijven per item.
15. **Nieuwe versie uploaden in "Versies…"** krijgt voortgang en Annuleren (XHR PUT), maar is in fase 3 niet hervatbaar.
16. **Gebruiker verwijderen.** Eerst blokkeren, dan definitief verwijderen als achtergrondtaak.
    - Een geblokkeerde gebruiker kan niets meer, ook niet downloaden met een uitgegeven token.
    - De laatste beheerder kan niet gedegradeerd, geblokkeerd of verwijderd worden. `python -m app.cli make-admin` is de noodweg.
17. **Prullenbak** wordt standaard niet automatisch geleegd; op `/beheer` is dat in te stellen (0 = nooit).
18. **Bestandslijst.**
    - De standaardvolgorde is die van de server: mappen eerst, naam zonder hoofdletters en accenten.
    - Andere sorteringen werken pas als de hele map geladen is.
    - De eerste pagina heeft 200 rijen; de rest komt op de achtergrond in pagina's van 1.000.
19. **Virtualisatie.** Vaste rijhoogte in de lijst, vierkante cellen in raster en tijdlijn, scrollen op het venster. De rij met focus blijft altijd gemount.
20. **Previews.** Lijstantwoorden blijven licht; wat de viewer nodig heeft, komt van een eigen endpoint per bestand. Oude previews zonder nieuwe velden krijgen een terugval in de client: proberen af te spelen, gif/webp/apng als mogelijk geanimeerd behandelen, en de tekencodering in de client bepalen.
21. **PDF-pagina's en office-inhoud alleen met een download-token**, met toegang per verzoek. De viewer vernieuwt het token vóór het verloopt.
22. **JSON-compressie** alleen voor lijstantwoorden zonder geheimen: children, search, trash, shared-with-me en lookup. Nooit `/auth/*`, previews of foto-pagina's met ondertekende adressen (BREACH). Boven 64 KiB comprimeert een thread, niet de event loop.
23. **De web-app wordt bij de build voorgecomprimeerd** (`.br`/`.gz` met `node:zlib`).
24. **PWA en offline alleen in een secure context**: het https-domein (B11) en localhost.
25. **Service worker.**
    - Gebouwd als los IIFE-bestand.
    - Precachet alleen de schil; lazy chunks komen cache-first bij gebruik.
    - Wordt geregistreerd na `load` en idle.
    - Nooit `/api`. Een update gaat pas in na "Vernieuwen", met een kill-switch-build als vangnet.
26. **i18n.**
    - NL is de bron; EN wordt volledig.
    - De taal hoort bij het account (`users.locale`), met de keuze per apparaat als terugval. Wisselen herlaadt de pagina.
    - Paden blijven Nederlands.
    - **Woordenboeken laden per gebied lazy;** alleen common, shell en auth zitten in de eerste bundel.
    - `t()` op moduleniveau is verboden.
27. **Elke API-fout krijgt een code** (plus params). P0 zet alle bestaande raises in één mechanische ronde om.
    - Elke code staat in precies één catalogus.
    - In NL toont de app de servertekst; in EN vertaalt hij op code.
28. **Thema** licht, donker of systeem, per apparaat. `/boot.js` zet het vóór de eerste paint.
29. **CSP.** `'unsafe-inline'` voor style verdwijnt. React style-props mogen (die lopen via CSSOM). Een CSP-schending laat elke e2e-test falen.
30. **Lighthouse "> 90".**
    - De eigen meting moet ≥ 91 geven: mobiel op `/` en `/s/<link>`, desktop op `/map/<10k>`.
    - De harde acceptatie is één echte DevTools-Lighthouse-run per pagina die > 90 geeft, bij de integratie.
    - Wijken ze meer dan 3 punten af, dan herijken we de eigen meting.
31. **Eén migratie 0006, gemaakt door P0, met alle schemawijzigingen van fase 3.**
32. **Sneltoetsen.**
    - Nooit Ctrl+N/T/W of Ctrl+Shift+N.
    - Wel: `?`, `/`, N, U, Shift+U, G, F2, Delete **of Mod+Backspace**, Shift+Delete **of Shift+Mod+Backspace**, Mod+A/C/X/V/F, Shift+F10, de pijltjes.
    - In een ingezoomde afbeelding pannen de pijltjes en navigeert Shift+←/→.
33. **e2e draait vanuit de broncode.** `python -m app.serve` gebruikt de `web/dist` van `npm run build`, dezelfde build als de web-stage van de image. Wat specifiek is voor de image (compressie, CSP, `sw.js`), test de curl-smoke in de images-job.
34. **Rate limits zijn uit te zetten met `PLATFORM_RATE_LIMITS=false`**, alleen voor de e2e-server. Standaard staan ze aan, met een waarschuwing in het log als ze uit staan.

### Vragen voor Jonas

1. **Mag Playwright (Python) gebruikt worden als testgereedschap?** Alleen in de sandbox en in een CI-job (`uv run --with playwright`, in CI met `playwright install chromium`). Het komt niet in de image, `pyproject.toml` of `package.json`. Standaard: ja. Bij nee blijven alleen vitest op pure logica en handmatige metingen in Chrome DevTools over.

(De GPS-vraag van revisie 1 vervalt: locatie hoort bij fase 6.)

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Naam | Grootte | Draait |
|---|---|---|---|
| **P0** | Fundament (§3) | XL | **eerst, alleen.** Drie werkers: P0-api, P0-web en P0-e2e (disjunct) |
| P1 | Schil, PWA en prestaties | M | parallel |
| P2 | Bestandsbrowser | XL | parallel |
| P3 | Uploads | L | parallel |
| P4 | Viewers en preview-pijplijn | XL | parallel |
| P5 | Foto's: tijdlijn en albums | L | parallel |
| P6 | Beheer, account, delen, prullenbak, versies | L | parallel |

**Eigendom na P0.** "frozen" betekent: na P0 wijzigt niemand het. Een bug of ontbrekend stuk in een frozen bestand gaat via de *P0-hotfix-lane*: een kleine PR op de basisbranch waar alle pakketten op rebasen.

**Web**

| Bestand | Eigenaar |
|---|---|
| `App.tsx`, `routes.tsx`, `router.ts`, `router.test.ts`, `links.ts`, `main.tsx`, `api.ts`, `api.test.ts`, `crossApi.ts`, `currentUser.ts`, `Dialog.tsx`, `perf.ts`, `shellPages.tsx`, `uploadStatus.ts`, `ui/**`, `i18n/index.ts`, `i18n/fmt.ts`, `i18n/types.ts`, `i18n/locale.ts`, `i18n/i18n.test.ts`, `i18n/areas/{common,shell}.*`, `i18n/errors/common.ts`, `tokens.css`, `base.css`, `viewer/types.ts`, `viewer/index.ts`, `viewer/param.ts`, `upload/index.ts` (signatures only, see P3), `package.json`, `tsconfig*.json`, `scripts/check-i18n.mjs` | P0 (frozen) |
| `index.html`, `vite.config.ts`, `nginx.conf`, `Dockerfile`, `public/**` except `boot.js`, `scripts/budget.mjs`, `scripts/icons.mjs`, `src/pwa/**`, `Login.tsx`, `i18n/areas/{auth,pwa}.*`, `i18n/errors/auth.ts` | P1 |
| `Files.tsx`, `NodeActions.tsx`, `FolderPicker.tsx`, `Search.tsx`, `searchQuery.ts`, `Tags.tsx`, `tagColors.ts`, `Thumb.tsx`, `events.ts`, `files.css`, `browser/**`, `files.test.ts`, `events.test.ts`, `thumb.test.ts`, `i18n/areas/browser.*`, `i18n/errors/browser.ts` | P2 |
| `upload.ts`, `uploadQueue.ts`, `upload.test.ts`, `upload/**` (`index.ts`: implementation only, exports frozen), `i18n/areas/upload.*`, `i18n/errors/upload.ts` | P3 |
| `PreviewPanel.tsx`, `Public.tsx`, `publicApi.ts`, `public.test.ts`, `public.css`, `viewer/**` (except the frozen three), `i18n/areas/{viewer,public}.*`, `i18n/errors/viewer.ts` | P4 |
| `photos/**`, `i18n/areas/photos.*`, `i18n/errors/photos.ts` | P5 |
| `Account.tsx`, `Beheer.tsx`, `BeheerGroups.tsx`, `BeheerJobs.tsx`, `Trash.tsx`, `Versions.tsx`, `versions.css`, `ShareDialog.tsx`, `SharedWithMe.tsx`, `sharing.ts`, `share.css`, `account.test.ts`, `sharing.test.ts`, `admin/**`, `shares/**`, `trash/**`, `versions/**`, `i18n/areas/{admin,account,sharing,trash,versions}.*`, `i18n/errors/{admin,sharing}.ts` | P6 |

`public/boot.js` belongs to P0 (frozen).

**API**

| Bestand | Eigenaar |
|---|---|
| `models.py`, `config.py`, `errors.py`, `__init__.py`, `migrations/versions/0006_fase3.py`, `db.py`, `events.py`, `storage.py`, `migrate.py`, `text.py`, `totp.py`, `routers/events.py`, `tests/conftest.py`, `tests/factories.py`, `tests/test_error_catalog.py`, `tests/test_errors.py`, `tests/test_locale.py`, `tests/test_migration_0006.py`, `tests/test_jobs_chain.py`, `tests/test_disabled.py`, `tests/test_rate_limits.py`, `tests/test_notify_many.py`, `tests/test_trash_restore_bulk.py` | P0 (frozen) |
| `main.py`, `tests/test_system.py`, `tests/test_web_app.py` | P1 |
| `routers/files.py`, `routers/search.py`, `routers/batch.py`, `routers/archive.py`, tests `test_files.py`, `test_copy.py`, `test_files_events.py`, `test_search.py`, `test_versions.py`, `test_events*.py` | P2 |
| `routers/uploads.py`, `routers/folder_tree.py`, `tests/test_uploads.py` | P3 |
| `render.py` (keep P0's hook lines, §3.3), `previews.py` (keep P0's hook lines), `mime.py`, `office.py`, `routers/previews.py`, `routers/viewer.py`, `tests/test_previews.py`, `tests/test_mime.py` | P4 |
| `render_meta.py`, `photodate.py`, `photometa.py`, `access.py`, `routers/shares.py`, `routers/public.py`, `routers/photos.py`, `routers/albums.py`, `tests/test_public.py`, `tests/test_shares*.py`, `tests/test_photodate.py` | P5 |
| `routers/auth.py`, `routers/admin.py`, `routers/groups.py`, `routers/trash.py`, `routers/jobs_admin.py`, `routers/myshares.py`, `security.py`, `jobs.py`, `cluster.py`, `certs.py`, `acme.py`, `serve.py`, `useragent.py`, `cli.py`, `usage.py`, `userdelete.py`, tests `test_auth.py`, `test_2fa.py`, `test_account.py`, `test_admin.py`, `test_groups.py`, `test_jobs.py`, `test_cluster.py`, `test_certs.py`, `test_acme_pebble.py`, `test_serve.py`, `test_totp.py` | P6 |

**Repo**

| Bestand | Eigenaar |
|---|---|
| `.github/workflows/ci.yml`, `e2e/perf/lighthouse_like.py`, `e2e/perf/run_all.py` | P1 |
| `e2e/conftest.py`, `e2e/seed.py`, `e2e/perf/first_view.py`, `e2e/perf/scroll.py`, `e2e/test_smoke.py`, `e2e/test_theme.py`, `e2e/pytest.ini`, `e2e/ruff.toml`, `docs/fase-3/e2e.md` | P0 (frozen) |
| `e2e/test_<pkg>*.py`, `e2e/seed_<pkg>.py`, `docs/fase-3/<pkg>.md`, `docs/fase-3/toetsenbord-<pkg>.md` | each package its own |

**Regels voor alle pakketten**

1. **Files.** Touch only files you own; everything else is import-only. Create new files only under your own folders or names.
2. **Compatibility of exports others use.** Keep the signature and the semantics; adding optional parameters is allowed.
   - `files.py`: `get_node`, `get_access`, `get_dir`, `get_live_dir`, `get_version`, `send_file`, `describe`, `one_out`, `notify`, `notify_many`, `shared_below`, `descendants`, `subtree_rows`, `tree`, `drop_versions`, `add_refs`, `collect_garbage`, `free_name`, `is_trashed`, `sibling`, `clean_name`, `user_lock`, `room_left`, `NodeOut`, `place_file`.
   - `public.py`: `current_link`, `CurrentLink`, `inside`, `readable`, `public_node`.
   - `uploads.py`: `release`, `_release`, `_drop`, `remove_expired`.
   - `previews.py`: `url`, `url_key`, `expiry`, `info`, `directory`, `render`, `THUMB`, `LARGE`, and `RENDER_VERSION` once P4 adds it (an `int`; `e2e/seed.py` reads it with `getattr(…, 0)`).
   - `render_meta.py`: `VERSION`, `image_meta`, `video_meta`, `run_meta`.
   - `photodate.py`: `wall_clock`, `day_of`, `is_media`, `stamp`, `stamp_values`, `from_ms`, `split_capture`, `on_blob_meta` (used by `files.py`, `uploads.py`, `previews.py` and `e2e/seed.py`; migration 0006 has its own copy of `wall_clock` and `day_of`).
   - `security.py`: all current names plus `active_user`.
   - `jobs.py`: all current names plus `chain`.
   - `errors.py`: `fail`, `item_fail`, `item_of`, `ItemResult` (with `params`).
   - Web components and hooks: `NodeActions`, `Thumb`, `category`, `Category`, `usePreviews`, `TagChips`, `TagEditor`, `tagStyle`, `SearchBox`, `ShareDialog`, `Versions`, `useServerEvents`, `useReloadOn`.
   - Used by the frozen shell (`App.tsx`): `events.ts` `liveEvents` (`start`, `stop`); `Login` with props `{onLoggedIn(user), message}`; `Account.tsx` the named export `RecoveryCodes({codes, onDone})`; `uploadQueue.ts` keeps registering the queue with `uploadStatus.attach({subscribe, busy, stopAll})` (the shell imports only `uploadStatus.ts`, so the queue and the tus client stay out of the first load).
   - `photos/index.ts`: `AddToAlbumDialog`, `PublicAlbum` (exports frozen, implementation P5; used by P2 and P4).
   - The route title keys in package areas that `routes.tsx` names (`browser.title`, `photos.title`, …): the key stays, the text is the package's.
   - Web helpers: `affectsFolder`, `affectsSharedWithMe`, `nodeActions`, `folderActions`, `crumbs`, `permissionOf`, `isShared`, `PERMISSION_LABELS`, `expiryLabel`, `folderHref`, `hitPath`, `parentHref`, `pathLabel`, `readSearch`, `searchHref`, `hasFilter`, `linkToken`.
   - **`expiryLabel` and `formatExpiry` use only `common` keys** (`common.expires.*`, P0): they also run on `/s/:token`, which loads only `public` and `upload`.
   - **`UploadQueue`** (constructor, `TusApi`, methods):
     - `add(parent, files, opts?)` resolves only when every item of that batch has finished, failed or been discarded.
     - `getSnapshot()` returns a stable `readonly UploadItem[]` that is replaced (new array) on every change and equals `[]` when the queue is empty.
     - `subscribe(cb)` is notified on every change (not coalesced).
     - `run`, `cancel`, `discard` and `onFinished` keep their current meaning.
     - Coalesced updates for the tray go through a separate `subscribeSummary(cb)`.
3. **No migrations and no `models.py` edits.** If you find a schema need, stop and report it; it becomes 0007 at integration.
4. **UI text.** All new UI text goes through `t()` in your own area files, with an `en` translation (tsc enforces completeness). Never call `t()` at module level: export functions or getters. `npm run check:i18n -- --files <owned>` must report 0.
5. **API errors.**
   - Every error in API files you own goes through `errors.fail(status, code, message, **params)`; every per-item error through `errors.item_fail(id, code, message, **params)` (or `errors.item_of(id, http_exception)`, which keeps the params). The web shows an item error with `errorText(item)`, the same lookup as for an `ApiError`.
   - A new code goes in **your** `i18n/errors/<area>.ts` with an `en` text. A code may appear in only one catalog; P0 pre-registers every code a frozen contract mentions in `common.ts`.
   - `test_error_catalog.py` (P0) checks this and bans `HTTPException(` outside `errors.py`.
6. **Database and blob rules:**
   - commit the session before waiting on any lock (`await session.commit()` before `user_lock`/`blobs.lock`);
   - blob `ref_count` changes only under `BlobStore.lock`;
   - quota checks and reservations only under `user_lock(owner)`;
   - authorization only via `app/access.py` and the `files.get_*` helpers; a download or page token is resolved through `security.active_user`;
   - events via `events.publish` after commit, to the owner plus every watcher (`access.watchers`), or via `files.notify`/`notify_many`;
   - no CPU-heavy work on the event loop (zip, gzip > 64 KiB, XML: in `anyio.to_thread` or the render child).
7. **Keyboard.** Every new action is reachable by keyboard and registered with `useShortcuts` in a scope. Document it in `docs/fase-3/toetsenbord-<pkg>.md` and cover it in your e2e test. P0's `Files.tsx` already pushes the scope `browser` (`useScope("browser")`), so P3's U/Shift+U work in P3's own branch; P2 keeps that line. A scope pushed inside a `Dialog` lands above the dialog's own scope (Dialog pushes in a layout effect).
8. **Bundle and CSS.**
   - Lazy-load anything heavy (route, dialog, viewer module). A lazy component that others render uses `lazyWithAreas` for its own i18n areas.
   - No `<style>` elements, no inline scripts, no `setAttribute("style")`.
   - No hard-coded colours: tokens only. CSS goes in your own `.css` file.
9. **Checks.** Existing pytest and vitest stay green; `ruff check`, `ruff format --check` and `tsc -b` stay clean. Code comments and commit messages are in Dutch.

**Runtime dependencies between packages** (only via frozen stubs or contracts from P0, so each package compiles and tests on its own):

| User | Uses | Built by |
|---|---|---|
| P1 | auth error codes and `totp_locked` params, `lastRefresh.setupRequired` | P0 |
| P2 | `<Viewer>` | P4 (P0 stub) |
| P2 | `UploadButtons`, `uploadDropped`, `hasFiles` | P3 (P0 stub) |
| P2 | `AddToAlbumDialog` | P5 (P0 stub) |
| P2 | `ShareDialog nodes=` | P6 (P0 prop, ignored) |
| P2 | `crossApi.restore` (undo) | **P0** (minimal bulk restore); P6 extends `trash.py` |
| P5 | `crossApi.trash`, `crossApi.archiveLink` | P2 |
| P5 | ShareDialog album rules; `nodeActions(album)` | P6 |
| P5, P6 | `<Viewer>` | P4 |
| P4 | `meta.exif` filled | P5 (`render_meta`) |
| P4 | `PublicAlbum` | P5 (P0 stub) |
| P2, P4 | `security.active_user`, `jobs.chain` | P0 |
| everyone | `ui/*`, router, i18n, `fmt`, `errors` | P0 |

---

### P1 — Schil, PWA en prestaties

**Doel.** Installable PWA with an offline shell, fast first paint, compressed and correctly cached serving, a size budget in CI, a Lighthouse-like measurement, and polish of the login and 2FA flows.

**Dekt.** PWA installable plus offline shell; performance (compression, code-split budget, first paint); serving robustness; CSP; the login-side auth flows; the Lighthouse > 90 measurement (§4).

**Maakt**
- **Manifest and icons**
  - `web/public/manifest.webmanifest`: name "Platform", `start_url`/`scope` `/`, `display: standalone`, `lang: nl`, colours from the tokens, icons 192/512/512-maskable, shortcuts `/gedeeld` and `/zoeken`.
  - `web/public/icons/{icon.svg,icon-192.png,icon-512.png,icon-maskable-512.png,apple-touch-icon.png}` and `web/public/favicon.{svg,ico}`.
  - `web/scripts/icons.mjs`: draws the logo and writes PNG/ICO with `node:zlib` (deflate plus `zlib.crc32`). The PNGs are committed.
- **`web/src/pwa/sw.ts`** (about 150 lines, imports only `./routing.ts`):
  - precache **only the shell**: `index.html`, the entry chunk, the vendor chunk, initial CSS, the initial i18n chunks, `boot.js`, the manifest and the icons;
  - navigations: network-first, with the cached `index.html` as offline fallback;
  - `/assets/*`: cache-first, filled at runtime (lazy chunks, the en chunk, the worker);
  - never `respondWith` for `/api/*`, `/health`, `/.well-known/*`, Range requests or non-GET;
  - versioned cache names; old caches removed on activate;
  - `skipWaiting` only on a message from the page;
  - `VITE_SW=off` builds a self-unregistering kill switch.
- `web/src/pwa/routing.ts`: pure `classify(request: {url, method, mode, headers}) → "shell" | "asset" | null`.
- `web/src/pwa/register.ts` (replaces P0's stub):
  - registers only when `import.meta.env.PROD && "serviceWorker" in navigator && isSecureContext`;
  - waits for `load`, then `requestIdleCallback` (2 s timeout).
- `web/src/pwa/AppStatus.tsx` (replaces P0's stub): the offline banner and the toast "Nieuwe versie beschikbaar — Vernieuwen".
- `web/src/pwa/shell.css`.
- `web/scripts/budget.mjs` (replaces P0's stub):
  - gzips the dist and follows the import graph from `index.html`;
  - fails over budget (§4);
  - fails when `dist/sw.js` contains a top-level `import` or `export`;
  - fails when an i18n area dictionary other than common/shell/auth is in the initial graph.
- **Tests and docs:** `api/tests/test_web_app.py`, `test_json_compression.py`, `web/src/pwa/routing.test.ts`, `e2e/test_pwa.py`, `e2e/test_login.py`, `e2e/perf/lighthouse_like.py`, `e2e/perf/run_all.py`, `docs/fase-3/p1-schil.md`.

**Wijzigt (exclusief)**
- `web/index.html`:
  - the manifest link and theme-color metas (per colour scheme with `media` is fine: `boot.js` and `ui/theme.ts` set **every** `meta[name=theme-color]` to the chosen theme's colour and remove `media`);
  - static shell markup inside `#root` (no text);
  - the apple-touch-icon and favicon links.
- `web/vite.config.ts`:
  - `build.manifest = true`.
  - Inline plugin **`swPlugin`**, in `closeBundle`:
    - read `dist/.vite/manifest.json` and compute the shell list (the `index.html` entry plus its static imports and CSS, `boot.js`, manifest, icons);
    - then call `vite.build()` programmatically with `configFile: false`, `build.lib = { entry: "src/pwa/sw.ts", formats: ["iife"], name: "PlatformSW", fileName: () => "sw.js" }`, `outDir: "dist"`, `emptyOutDir: false`, `copyPublicDir: false`, and `define: { __PRECACHE__: JSON.stringify(list), __BUILD__: JSON.stringify(hash) }`;
    - then remove `dist/.vite`.
    - This gives one file with no imports, independent of the app graph.
  - Inline plugin **`precompress`** (`closeBundle`, ordered after `swPlugin`): writes `.br` (quality 11) and `.gz` (level 9) next to every js, css, html, svg, webmanifest and json file over 1 KB.
  - `build.rolldownOptions.output.codeSplitting = { groups: [{ name: "vendor", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ }] }`.
  - Risk: check Rolldown's behaviour for nested `build()` in `closeBundle` early (first commit of P1).
- `web/nginx.conf` (cluster image) mirrors the API:
  - `gzip_static on`;
  - `location = /sw.js` and `/boot.js` with no-cache;
  - `try_files $uri =404` for paths with an extension;
  - the same CSP and security headers.
- `api/app/main.py`:
  - `web_app`:
    - 404 for a missing file under `assets/` or any path with a suffix;
    - choose `.br`/`.gz` by `Accept-Encoding` (q-values), with `Content-Encoding`, `Vary` and the original media type;
    - a stat ETag (`W/"<mtime_ns>-<size>"`) plus `If-None-Match` → 304 for no-cache files;
    - `.webmanifest` as `application/manifest+json`.
  - `WEB_CSP = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'"`. Add `Permissions-Policy: camera=(), microphone=(), geolocation=()`.
  - **`JsonCompression`** ASGI middleware (stdlib gzip, level 6):
    - only GET/POST with `content-type: application/json`;
    - body ≥ 8 KiB;
    - path matches `^/api/v1/(files/[^/]+/children|files/root|search|trash(/overview)?|shared-with-me|files/lookup)$`;
    - the client accepts gzip.
    - Bodies over 64 KiB are compressed in `anyio.to_thread.run_sync`.
  - The lifespan line that copies `settings.rate_limits` into `RateLimiter.enabled` (P0 wrote it) stays.
- `web/Dockerfile` only if needed for `.br`/`.gz`.
- `web/src/Login.tsx`:
  - Show the form at once from `api.lastRefresh.setupRequired`; fetch `/auth/status` only when that is null.
  - A network-error state ("Geen verbinding met de server", retried on `online`).
  - "Terug" on the code step, a show-password toggle, and "Wachtwoord vergeten? Vraag je beheerder om een nieuw wachtwoord.".
  - The lock message from `ApiError.params.until` (code `totp_locked`, P0) via `fmt.relative`.
  - Account-blocked message (code `account_disabled`).
  - `PageTitle` per step; `ui/Field`/`ui/Button`; t(); `markReady("login")`.
- `.github/workflows/ci.yml`:
  - **web job:** `npm run check:i18n` (report) and `npm run budget`.
  - **images smoke:**
    - `/assets/nope.js` → 404;
    - `/sw.js` → 200 `text/javascript` with no `import`;
    - `Accept-Encoding: br` on `/` → `content-encoding: br`;
    - exact CSP.
  - **New job `e2e`** (needs: web), a hard gate. It runs these steps:
    - install ffmpeg and `uv sync`;
    - `npm ci && npm run build`;
    - `uv run --with playwright python -m playwright install --with-deps chromium`;
    - `uv run --with playwright pytest ../e2e -m "not perf"`.
  - **Job `perf`**: report-only, uploads JSON as an artifact.
- `api/tests/test_system.py`: update the CSP assertions.

**API.** No new endpoints.

**Migratie.** Geen.

**Tests**
- pytest `test_web_app.py`:
  - 404 for `assets/x.js` and `foo.png`;
  - SPA fallback for `/map/<uuid>` and `/fotos`;
  - br/gzip selection, q=0 respected, `Vary`;
  - 304 with the ETag;
  - manifest type;
  - exact CSP.
- pytest `test_json_compression.py`:
  - a large children listing is compressed, and decompressing gives the identical body;
  - not on `/auth/*`, `/previews`, `/events`, nor below 8 KiB;
  - while a 4 MB listing is being compressed, a concurrent `/health` answers within 50 ms.
- vitest `routing.test.ts`: navigation → shell; `/assets/x.js` → asset; `/api/v1/events`, tus PATCH, Range, POST, `/health` and `/.well-known` → null.
- e2e `test_pwa.py` on `http://localhost`:
  - the SW takes control after load;
  - offline reload shows the shell plus the banner;
  - after a rebuild the toast appears and "Vernieuwen" loads the new build;
  - lazy chunks visited once also load offline;
  - the manifest and icons are fetchable.
- e2e `test_login.py`:
  - login with TOTP (a dedicated user per case) and Terug;
  - network error (route abort);
  - the lock message after 5 wrong codes;
  - a blocked account;
  - the EN locale.

**Klaar als**
- `npm run build` emits `sw.js` (IIFE), the manifest, the icons and `.br`/`.gz`.
- `npm run budget` passes.
- All tests above are green.
- `run_all.py` gives Lighthouse-like mobile **≥ 91** on `/` and `/s/<link>` (median of 5).
- First view of `/` is under 1.5 s (LAN).
- `check:i18n` reports 0 for P1's files.

---

### P2 — Bestandsbrowser

**Doel.** A file browser that stays smooth at 10,000 items per folder, in list and grid, with selection, bulk actions, a context menu, internal drag and drop, keyboard shortcuts and incremental live updates.

**Dekt.** Browser list and grid (virtualized); bulk actions including zip and multi-share; context menu; shortcuts; drag and drop (internal, plus OS drops via P3); a listing for 10k; live updates; the target picker; the album guard on tree endpoints; fase 10 hooks.

**Maakt**
- `web/src/browser/FileList.tsx`:
  - a div `role="grid"` on `ui/VirtualList` with `keepIndex = focusIndex`, plus `aria-rowcount`/`aria-rowindex`;
  - a sticky header with Naam/Grootte/Gewijzigd and a ⋯ button, collapsing on narrow screens;
  - sortable headers (`aria-sort`); memoized rows;
  - roving focus (one tab stop);
  - the checkbox column;
  - a row status slot for fase 10.
- `web/src/browser/FileGrid.tsx`: `ui/useColumns` with cells of at least `--cell-min`, `Thumb size=180` (256 WebP), `keepIndex`.
- `web/src/browser/folderStore.ts`, a store per open folder:
  - `Map<id, FileNode>` plus the order and total;
  - paging: first page `limit=200`, the rest in pages of 1,000 via `requestIdleCallback`;
  - events: collect `node.*` ids for 150 ms, then `POST /files/lookup`;
  - drops ids that are missing, have `deleted_at` set, or have `parent_id !== folderId`;
  - full reload only on `connected`, `tags.changed`, `share.*` in shared folders, or a 404;
  - own actions patch the store from the API result.
- `web/src/browser/sort.ts`: server order (fold plus codepoint) and client sorts (`collator` numeric, folders first), applied only when fully loaded.
- `web/src/browser/actions.ts`, the action registry:
  - an action is `{id, labelKey, icon, group, shortcut?, when(sel, folder) → {visible, enabled, reason}, run(sel, ctx)}`;
  - groups Openen, Bewerken, Delen, Info, Prullenbak, plus a reserved empty `omzetten` group;
  - `registerAction()` for fase 10;
  - permissions are `nodeActions()` intersected over the selection;
  - labels resolve through `t()` at call time, never at module level.
- `web/src/browser/clipboard.ts`: in-app cut/copy/paste of ids.
- `web/src/browser/dragState.ts`: `{ids, kinds, mimes}`, subscribable. HTML5 DnD on rows, crumbs and nav targets:
  - type `application/x-platform-nodes`;
  - `setDragImage` with a badge element built from CSS classes (no style attribute);
  - move within the same owner, copy with Ctrl/Alt or across owners;
  - auto-scroll near the edges (rAF);
  - a refused target gets `dropEffect="none"` plus a reason in a live region.
  - **OS drops:** `onDrop` calls `uploadDropped(e.dataTransfer, folderId)` *synchronously*, before any `await`, permission check or lazy import, when `hasFiles(dt)` (contract in §3.1).
- `web/src/browser/filter.ts` and `FilterBox` ("Filter in deze map", Mod+F; a second Mod+F goes to the browser).
- `web/src/browser/previews.ts`: `useVisiblePreviews(visibleIds)`, an id→Preview cache that fetches only missing or stale ids (debounced 100 ms, ≤ 500 per POST) and refreshes on `preview.ready` and `connected`. `usePreviews(nodes)` stays as a wrapper.
- `web/src/browser/preload.ts` (replaces P0's stub): `preloadFolder(id)` starts page 1 during boot.
- `web/src/browser/api.ts`, `web/src/browser/browser.css`.
- `api/app/routers/batch.py` and `api/app/routers/archive.py` (P0 stubs, already included).
- **Tests and docs:**
  - pytest: `test_listing_paging.py`, `test_lookup.py`, `test_batch.py`, `test_batch_events.py`, `test_archive.py`, `test_album_guard.py`;
  - vitest: `web/src/browser/{actions,folderStore,sort,clipboard,filter}.test.ts`;
  - e2e: `test_browser.py`, `test_browser_keyboard.py`, `seed_browser.py`;
  - `docs/fase-3/toetsenbord-p2.md`.

**Wijzigt (exclusief)**
- `Files.tsx`, rewritten on FileList/FileGrid/folderStore:
  - a toolbar with `UploadButtons` (P3), "Nieuwe map" (a Dialog), Lijst/Raster, sort, FilterBox and SearchBox;
  - a selection bar with actions from the registry;
  - drop handling as above;
  - Viewer via `useViewerParam`;
  - after "Naar prullenbak", a toast with "Ongedaan maken" → `crossApi.restore(ids)`;
  - "Delen" on a selection opens `ShareDialog nodes=` (≤ 100; above that, disabled with the reason "Deel de map in plaats van zoveel losse items");
  - `markReady("folder")` after the first rows paint; `useRestoreScroll`.
  - keeps P0's `useScope("browser")` and the smoke DOM contract (§3.4): the empty-folder text "Deze map is leeg." and a click on a file's name opens the viewer.
- `NodeActions.tsx`: becomes a `MenuButton` adapter on the registry (export kept).
- `FolderPicker.tsx`: `exclude: Set<string>`, a check over the whole selection (cross-owner → move disabled with the reason, copy allowed), `?kind=dir`, "Nieuwe map", type-ahead.
- `Search.tsx` and `searchQuery.ts`: FileList with a path column; Viewer for hits; `navigate()`.
- `Tags.tsx` and `tagColors.ts`: a dialog instead of prompt/confirm; chips work in dark mode through a `--tag` custom property set by a class per colour index (stored hex unchanged).
- `Thumb.tsx`: a `size` prop (32 or 180), `ui/Icon`, no inline styles (`category`/`Category` exports kept).
- `events.ts`: add `folderEventIds(event, folderId)`.
- `files.css` (the `.version-*` rules already moved to `versions.css` in P0).
- `api/app/routers/files.py`:
  - **listing**: `GET /files/{id}/children` and `/files/root` accept `?limit=1..5000&after=<cursor>&kind=dir|file`;
  - **`describe()` faster** via `NodeOut.model_construct`, same output;
  - **`POST /files/lookup`**;
  - **album guard**: new export `get_tree_access(session, user, id, need)` = `get_access` + 422 `album` for kind album. It is used by every tree endpoint in files.py: rename/move (PATCH), trash, copy, tags, versions and restore_version, PUT content, download/content (an album has no content). `get_access` itself is unchanged, because shares.py needs it for albums.
- `api/app/routers/search.py`: search hits and `tag_counts` exclude `kind == "album"`; reuse the faster `describe`.
- `api/tests/test_files.py` etc. only where behaviour is additive.

**API**
```
GET  /api/v1/files/{id}/children?limit=&after=&kind=      (ook /files/root)
  → Listing & { next: string|null, total: int }        # zonder limit: alles, next=null (compat)
     cursor = base64url("<kind>\x1f<search_name>\x1f<id>"); order (kind, search_name, id)
POST /api/v1/files/lookup   { ids: uuid[≤500] } → { nodes: NodeOut[] }
     # toegang als GET /files/{id}, maar ZONDER nodes met deleted_at of een ouder in de prullenbak
     # (één ancestry-query voor de hele set); onzichtbaar of weg = niet in het antwoord
POST /api/v1/batch/trash    { ids: uuid[≤10000] } → { results: ItemResult[] }
POST /api/v1/batch/move     { ids, parent_id, conflict?: "fail"|"rename" } → { results }   # onder blobs.lock zoals change()
POST /api/v1/batch/copy     { ids, parent_id } → { results }   # ItemResult.node = kopie; som subtrees ≤ MAX_COPY;
                                                               # user_lock(doel-eigenaar) + blobs.lock, quota één keer
ItemResult (errors.ItemResult) = { id, ok: bool, code?: "not_found"|"read_only"|"share_root"|"cross_owner"|"name_taken"|
               "into_itself"|"trashed"|"too_many"|"album", message?: str, node?: NodeOut }
POST /api/v1/archive/link   { ids: uuid[≤10000], name: str } → { url: "/api/v1/archive?token=…", expires_in: 600 }
     # 422 album als een id een album is; toegang in bulk: één query voor eigen nodes + één access.resolve voor de rest
     # (zelfde betekenis als get_access(read), dus album-items via een albumdeling tellen mee zodra P5 erin zit)
GET  /api/v1/archive?token= → 200 application/zip, streaming, attachment "<name>.zip", geen Content-Length/Range;
     # token → security.active_user (geblokkeerd = 404); toegang per top-id opnieuw in bulk; subtree_rows(live=True);
     # paden uit parent-ids; ZIP_STORED, force_zip64; schrijver in anyio.to_thread → memory object stream;
     # stopt bij disconnect; ≤ 50.000 entries (anders 422 too_many vóór de eerste byte); token-dict met TTL
Events (files.notify_many): { ids:[≤500], parent_id, old_parent_id? } per (ontvanger, zichtbare ouder)
```

**Migratie.** Geen; P0 adds `ix_node_parent_order`.

**Tests**
- pytest:
  - **paging**: concatenated pages equal the unpaged listing, the cursor is stable while inserting, `kind=dir`, `total`;
  - **lookup**: respects shares; omits own trashed nodes and nodes under a trashed folder;
  - **batch**: partial failures per code, one transaction per 500, `share_root`/`read_only`, cross-owner, quota on copy, events grouped (≤ 500 ids) to share watchers too;
  - **zip**: a valid zip; access revoked between link and download → 404; a blocked user → 404; disconnect stops the writer; > 50k → 422; 10,000 top-ids are checked in ≤ 3 queries;
  - **album guard**: a `Node(kind="album")` inserted via factories is refused with 422 `album` by copy, batch/copy, batch/move, batch/trash, PATCH, tags, versions, content, archive/link; it is not a search hit and not in tag_counts;
  - **describe parity**.
- vitest:
  - `when()` intersections and reasons;
  - folderStore merge (create, move away, delete, trashed via lookup, grouped ids);
  - sort stability; clipboard; filter;
  - no `t()` at module level in `actions.ts` (via `check:i18n`).
- e2e (seed 10k):
  - open, scroll to the end, Shift-range select, select all;
  - Delete **and Mod+Backspace**, plus Ongedaan maken;
  - F2 rename, N new folder, cut/paste move, copy;
  - zip download (the file opens);
  - the context menu via right-click, Shift+F10 and long-press (touch emulation);
  - grid G;
  - drag onto a folder row and onto a crumb;
  - "Delen" on a selection opens ShareDialog;
  - after wheel scrolling plus ArrowDown, `document.activeElement` is the focused row.

**Klaar als**
- The `e2e/perf` gates for `/map/<10k>` in **list and grid** pass:
  - first view < 1.5 s on LAN;
  - p95 frame ≤ 20 ms at 1× and ≤ 34 ms at 4×;
  - no long task > 50 ms;
  - ≤ 200 rows rendered.
- Lighthouse-like desktop ≥ 91 on `/map/<10k>` (checked at integration with P1's tool).
- Only one children request plus background pages while uploading 1,000 files.
- Every registry action is reachable by keyboard and appears in `?` help.
- 0 `window.prompt`/`confirm`; `check:i18n` 0.

---

### P3 — Uploads

**Doel.** A global upload tray with totals, speed and ETA; pause and resume; recovery after a reload; one conflict choice per batch without side effects; folder upload by button and by drop; parallelism; an O(n) queue.

**Dekt.** Upload-UI: TUS, progress, pause and resume, folder upload; OS drag and drop (folders).

**Maakt**
- `web/src/upload/index.ts` (implementation of P0's frozen exports): `UploadButtons`, `hasFiles`, `uploadDropped`, `UploadTray`.
  - `uploadDropped(dt, folderId): void` snapshots `dt.items` → `webkitGetAsEntry()` entries and `Array.from(dt.files)` **synchronously**, then hands the snapshot to async code.
  - `index.ts` and `walk.ts` are static imports (no lazy load on the drop path).
  - `UploadTray` is `lazyWithAreas(() => import("./UploadTray"), ["upload"])`, rendered once the queue is non-empty.
- `web/src/upload/UploadButtons.tsx`: "Uploaden" and "Map uploaden" (`webkitdirectory`); U and Shift+U in scope `browser` (pushed by P0's `Files.tsx`). The button "Uploaden" keeps exactly that accessible name (smoke DOM contract, §3.4).
- `web/src/upload/walk.ts`:
  - `collect(entries, files) → Promise<{files: {file, path}[], dirs: string[]}>`;
  - `readEntries` in a loop until `[]`;
  - falls back to `files`;
  - rejects pseudo-file folders.
- `web/src/upload/UploadTray.tsx`:
  - bottom-right, collapsible;
  - an `aria-live=polite` summary: "1.234 van 5.000 · 2,1 GB van 6 GB · 12 MB/s · nog ~6 min", via `subscribeSummary` (≤ 4/s);
  - Pauzeren/Hervatten per file and for all; Annuleren;
  - failed items with Opnieuw/Weggooien and the reason (also `name_taken` from the folder tree);
  - "Alles tonen" in a `VirtualList`;
  - a `beforeunload` warning while uploads run.
- `web/src/upload/ConflictDialog.tsx`: "N bestanden bestaan al" with Vervangen (vorige wordt een versie) / Beide houden / Overslaan, plus "voor allemaal".
- `web/src/upload/Interrupted.tsx`: "Onderbroken uploads" from `GET /tus`, with "Kies het bestand opnieuw" (matched on name and size) and "Weggooien".
- `web/src/upload/plan.ts`, a pure function: `planConflicts(files, takenByFolder, choice) → {create: {file, parent, conflict}[], skipped}`.
- `web/src/upload/api.ts`, `web/src/upload/upload.css`.
- `api/app/routers/folder_tree.py` (P0 stub, included).
- **Tests and docs:**
  - pytest: `api/tests/test_folder_tree.py`, `test_names_check.py`, `test_uploads_list.py`;
  - vitest: `web/src/upload/{queue,walk,plan,eta,compat}.test.ts`;
  - e2e: `test_upload.py`;
  - `docs/fase-3/toetsenbord-p3.md`.

**Wijzigt (exclusief)**
- `uploadQueue.ts`:
  - items in a `Map` plus running totals;
  - **`add()` keeps its rule-2 semantics** (it resolves when the batch is done);
  - `getSnapshot()` is a new array on every change; `subscribe` is not coalesced; `subscribeSummary` is coalesced to ≤ 4/s;
  - `add(parent, files, opts?: {conflict?, onCreate?})` with O(n) dedupe on storageKey;
  - states `waiting|running|paused|failed|discarding`;
  - `pause(key)` = `abort(STOP)` without removing the item; `resume(key)`, `pauseAll()`, `resumeAll()`;
  - a scheduler with up to 3 concurrent uploads, 1 when a file is > 256 MB;
  - `addTree(folderId, collected)`: `folders/tree` in chunks of 1,000 paths, then `names/check` only for folders that already existed, then one ConflictDialog, then queue the files.
- `upload.ts`: metadata `conflict` and `lastmodified` (ms) via `tusCreate(parent, name, size, meta)`. Files without a clash are created with `conflict=rename`, so a race never overwrites; files with a clash get the chosen `replace`/`rename`, or are skipped.
- `upload.test.ts`: extended; existing cases stay.
- `api/app/routers/uploads.py`:
  - metadata `lastmodified` → `Upload.client_mtime`;
  - `finish()` passes it to `place_file(…, client_mtime=)`;
  - `GET /api/v1/tus`.

**API**
```
GET  /api/v1/tus → [{ id, name, parent_id, size, offset, created_at, expires_at }]   # eigen: created_by=user, share_id NULL, node_id NULL
Upload-Metadata: filename, parent, conflict(rename|replace|fail), lastmodified(ms, optioneel)
POST /api/v1/files/{id}/names/check { names: str[≤1000] } → { taken: [{ name, id, kind }] }
   # get_live_dir(id, "write"); dezelfde vergelijking als sibling() (lower(name), deleted_at NULL); één query;
   # maakt niets aan en reserveert geen quota
POST /api/v1/files/{id}/folders/tree { paths: str[≤1000] }
   → { folders: { "<path>": uuid }, created: string[], errors: { "<path>": "name_taken"|"invalid_name"|"too_deep" } }
   # elk segment clean_name; diepte ≤ 32; get_live_dir(id, "write"); session.commit() vóór user_lock(eigenaar doelmap);
   # aanmaken-of-hergebruiken via sibling(): een bestaande MAP wordt hergebruikt, een bestaand BESTAND geeft
   # errors[path]="name_taken" en alle paden eronder komen ook in errors; één commit; node.created per nieuwe top-level map
```

**Migratie.** Geen.

**Tests**
- pytest `folder_tree`:
  - reuse is case-insensitive;
  - two parallel calls create no duplicates;
  - write access in a shared folder;
  - a file named like a path segment gives `errors` for it and its subpaths, while the other paths succeed;
  - depth and count limits; events.
- pytest `names_check`:
  - finds clashes case-insensitively and ignores trashed nodes;
  - read-only share → 403;
  - creates no Upload rows and no reservation.
- pytest `uploads_list`:
  - only your own unfinished uploads;
  - `lastmodified` ends up in `node.taken_at` for an image without EXIF.
- vitest:
  - queue O(n) with 5,000 files (time budget), totals, pause/resume, concurrency, summary coalescing;
  - **compat**: the `public.test.ts`/`sharing.test.ts` patterns (`await add` → every `onFinished` → `getSnapshot()` equals `[]`) stay green;
  - the walker (> 100 per batch, empty folders);
  - `plan.ts`; ETA moving average.
- e2e:
  - folder upload via `setInputFiles` on a directory;
  - pause → resume;
  - reload → Onderbroken uploads → choose again → completes;
  - conflict dialog with "voor allemaal" (the network log shows no tus POSTs before the choice);
  - the tray on `/gedeeld`;
  - a synthetic folder drop via `DataTransfer` in Chromium.

**Klaar als**
- 5,000 files queue with no long task > 200 ms.
- Folder upload works by button and by drop.
- Everything works by keyboard.
- `check:i18n` 0; the `public.test.ts` and `sharing.test.ts` that P3 does not own stay unchanged and green.

---

### P4 — Viewers en preview-pijplijn

**Doel.** A full-screen viewer with prev/next, keyboard control and a deep link: images (zoom, EXIF panel), PDF (page viewer), video and audio (native), text and code, office. Plus the server pipeline: light list payloads, viewer endpoints, PDF pages, the office reader, codecs, the display size and re-render versioning.

**Dekt.** Viewers (all sub-items); pipeline versioning; the public page viewer.

**Maakt**
- **`web/src/viewer/Viewer.tsx`** (replaces P0's stub, default export, props from `viewer/types.ts`):
  - a full-viewport `<dialog>` with a header ("naam · 3 / 120") and a toolbar (download, info, new tab, full screen);
  - swipe; preloads the neighbours' large previews;
  - scope `viewer`:
    - Esc; ←/→ and Home/End (at fit) and Shift+←/→ (always);
    - +/−/0/1; F, I, D, R;
    - Space/K, J/L, M;
    - PgUp/PgDn (PDF);
  - `onNeedMore` at the edges.
- **Viewer modules**, each loaded with `import()`:
  - **`viewer/ZoomView.tsx`**:
    - transform and pointer events, pinch, wheel around the cursor;
    - double-click toggles fit/100 %;
    - **when zoom > fit, the arrows pan by 10 % of the viewport (Shift+arrows navigate)**;
    - swaps to `display` or the original after `img.decode()` once more than 1024 px is needed;
    - `meta.animated === true`, or `animated` missing and mime gif/webp/apng → the original.
  - **`viewer/PageViewer.tsx`** (generic, for fase 4 too):
    - placeholders from `page_sizes` and windowing with `variableRange`;
    - `srcset` over the buckets; fit width/page; 50–400 %; a page box; a TOC sidebar;
    - **token renewal**: re-fetch `/document` at `token_expires_in − 60 s`, and once on an image error (guard `mayRenew`).
  - **`viewer/MediaView.tsx`**:
    - stored codecs feed only a hint;
    - always try to play; MediaError 3/4 → "Deze browser kan dit formaat niet afspelen" plus a download button;
    - network errors and 403 get one new token (`mayRenew`);
    - audio auto-advances on `ended`.
  - **`viewer/TextView.tsx`**:
    - the first 512 KB via `range()`, then "Meer laden" and windowed lines;
    - `decode.ts` (BOM → utf-8/16; otherwise utf-8 `fatal` → windows-1252, with a dropdown; this also covers a missing `meta.encoding`);
    - `highlight.ts`: a state-machine tokenizer, never regex backtracking; `highlight.worker.ts` for anything over 100 KB, capped at 1 MB;
    - `markdown.ts`: a subset as React elements, links only http/https/mailto, images as alt text;
    - `csv.ts`: RFC 4180;
    - never `innerHTML`.
  - **`viewer/OfficeView.tsx`**: Document/Sheet/Slides, labelled "Vereenvoudigd voorbeeld zonder opmaak".
  - **`viewer/InfoPanel.tsx`**: Camera, Objectief, Belichting, Datum (`fmt.wallClock`). No location group.
- `viewer/source.ts`:
  - `defaultSource` memoizes download URLs per `(id, etag, inline, version)` for `expires_in − 60 s`;
  - `publicSource(link)`.
- `viewer/media.ts`: `formatDuration`, `mayRenew`, `playerFor`, moved here; `PreviewPanel.tsx` re-exports them.
- `viewer/api.ts`, `viewer/viewer.css`.
- **`api/app/office.py`**, stdlib only:
  - per-member size caps; `iterparse` with an early stop; ODF repeats capped;
  - docx (headings, paragraphs bold/italic, lists, tables);
  - xlsx (sharedStrings, inlineStr, bool, error, numFmt dates 14–22 and custom formats, date1904; 200 × 50 per sheet);
  - pptx (`sldIdLst` order, title plus bullet levels);
  - the ODF equivalents; metadata;
  - the thumbnail (skipping WMF/EMF).
- `api/app/routers/viewer.py` (P0 stub, included).
- **Tests and docs:**
  - pytest: `api/tests/test_viewer.py`, `test_pages.py`, `test_office.py`, `test_render_codecs.py`, `test_render_version.py`;
  - vitest: `web/src/viewer/{decode,highlight,markdown,csv,zoom,media,preview,renew}.test.ts`;
  - e2e: `test_viewer.py`;
  - `docs/fase-3/toetsenbord-p4.md`.

**Wijzigt (exclusief)**
- `render.py`:
  - `kind_of`: an `office` branch → `office()`;
  - `image()`: `meta.animated` plus `2048` for HEIC/HEIF/TIFF;
  - `video()`: `meta.video {codec, profile, pix_fmt}`, `meta.audio {codec, channels, sample_rate}`, `meta.container`;
  - `text()`: cp1252 fallback plus `meta.encoding`;
  - `main()`: `mode == "page"` and `mode == "docinfo"`;
  - **P0's hook lines stay.**
- `previews.py`:
  - `SIZES` + `DISPLAY = 2048`;
  - `info(…, full=False)` is light by default;
  - `RENDER_VERSION`; `_store` sets `blob.render_version`;
  - **`make()` skips only when `blob.preview in DONE and blob.render_version >= RENDER_VERSION`.** During a re-render the old preview stays visible until `_store` replaces it.
  - `enqueue_outdated(settings)` (P0 stub) becomes a chained cursor job `preview.outdated`, key `preview:outdated`, payload `{after: sha}`. Each run takes 200 blobs with `render_version < RENDER_VERSION`:
    - office and HEIC/HEIF/TIFF mimes → `jobs.enqueue_many` preview jobs (`key preview:<sha>`, `run_after = now + 60 s`);
    - everything else → `UPDATE render_version = RENDER_VERSION`, using the client fallbacks of Keuze 20;
    - then `jobs.chain(…, run_after=now)`.
  - **P0's `photodate` hook lines stay.**
- `mime.py`: only if office detection needs it.
- `routers/previews.py`: the light list payload; size 2048 accepted.
- `PreviewPanel.tsx`: a thin compatibility layer.
- `Public.tsx` and `publicApi.ts`:
  - the Viewer with `publicSource`; public preview, document and pages calls;
  - `useQueryParam("map")`; i18n; `markReady("public")`;
  - the expiry sentence through a `public` key with `{expiry}` (e.g. `public.linkExpiry: " · de link {expiry}"`), not built by hand around `expiryLabel`;
  - each listed item stays a row (`<tr>` or `role=row`; a header row has `columnheader`): smoke DOM contract, §3.4;
  - the album dispatch stays;
  - queue usage unchanged (rule 2).
- `public.test.ts`.

**API**
```
POST /api/v1/previews (+ /public/previews)  → zoals nu, meta licht
GET  /api/v1/files/{id}/preview?version=<vid>  → ViewerDetail
     { state, thumb, large, display: str|null,
       meta: { …preview_meta: width,height,duration,pages,title,artist,album,text?,truncated?,entries?,count?,
               encoding?, animated?, video?, audio?, container?, exif?: Exif },
       taken_at: str|null }
     Exif = { make?, model?, lens?, fnumber?, exposure?, iso?, focal?, focal35?, flash?, bias?, orientation? }  # nooit GPS
GET  /api/v1/files/{id}/document?version=
     → { kind: "pdf"|"office", pages: int|null, page_sizes: [[w_pt,h_pt]]|null, toc: [{title,page,level}], labels: str[]|null,
         widths: [640,1280,1920,2560], page_url: "/api/v1/files/{id}/pages/{n}.webp?w={w}&token={download_token}",
         token_expires_in: int, office: OfficeDoc|null }
GET  /api/v1/files/{id}/pages/{n}.webp?w=&token=   → image/webp
     # read_download_token → security.active_user (geblokkeerd = 404) → get_node(read) per verzoek;
     # cache previews/<sha>/pages/<w>/<n>.webp (tmp+rename; onder blobs.lock blob nog aanwezig);
     # n, n+1, n+2 in één subproces achter anyio.CapacityLimiter(settings.page_render_workers);
     # LRU-opruimen tot settings.page_cache_mb; ETag "<sha>-<n>-<w>", Cache-Control private, max-age=600
GET  /api/v1/public/preview/{node_id}   (Bearer key) → ViewerDetail
GET  /api/v1/public/document/{node_id}  (Bearer key) → idem; page_url met key; token_expires_in = resterende key-tijd
GET  /api/v1/public/pages/{node_id}/{n}.webp?w=&key=   # public.current_link + inside(); key_expired → pagina opent de link opnieuw
```
`OfficeDoc` is unchanged from revisie 1.

**Migratie.** Geen; `blob.render_version` comes from P0.

**Tests**
- pytest:
  - light vs full payload; viewer detail with a version;
  - **no GPS in any response** (a JPEG with GPS EXIF);
  - pages:
    - revoked share → 404; blocked user → 404;
    - `w` outside the buckets → 422; out-of-range page → 404;
    - the limiter holds a third request; cache eviction;
    - with `download_token_minutes = 0`: 401, then 200 after a new `/document`;
  - office fixtures for 6 formats plus bombs (caps and time limit);
  - codecs from a generated mp4 and webm (ffmpeg); an animated GIF;
  - **re-render**:
    - an office blob with `preview=ready` and `render_version=0` has `doc.json` and the new version after `enqueue_outdated` plus `run_pending`;
    - non-office blobs are only bumped;
    - preview jobs run after fresh ones (`run_after`);
    - the chain runs to the end over 450 blobs (three runs).
- vitest:
  - decode (BOM, cp1252); a tokenizer on a pathological 1 MB input under 200 ms;
  - markdown with no raw HTML or `javascript:`; csv;
  - zoom and pan math (arrow pan only above fit);
  - the MediaError → message mapping;
  - token renew timing.
- e2e:
  - open each sample type; ←/→; Shift+←/→ while zoomed; arrows pan while zoomed;
  - Back closes; the `?bestand=` deep link;
  - PDF PgDn and TOC; text encoding switch;
  - **media**: webm (VP9/Opus) and mp3 play; for mp4/H.264 the test reads `canPlayType` and asserts "speelt" or the honest message (the sandbox Chromium gives the message);
  - the public link viewer.

**Klaar als**
- Every viewer is lazy and within budget.
- The list payload holds no text, entries or exif.
- A revoked share or a blocked user stops page images immediately.
- PDF pages keep loading after the token's lifetime.
- Keyboard-only passes; `check:i18n` 0.

---

### P5 — Foto's: tijdlijn en albums

**Doel.** EXIF and date extraction plus a backfill; a timeline by month and day for 50,000 photos with a scrubber; selection and bulk actions; albums with sharing (people, groups, links) under B18.

**Dekt.** Foto-tijdlijn op datum, albums, selectie en delen; EXIF extraction (date, camera, exposure) for the viewer panel.

**Maakt**
- **`api/app/render_meta.py`** (replaces P0's stub), `VERSION = 1`:
  - `image_meta(img) → {taken_at, taken_kind: "local", exif: {make, model, lens, software, fnumber, exposure, iso, focal, focal35, flash, bias, orientation}}`:
    - via `getexif()` and `get_ifd(0x8769)`; **the GPS IFD is not read**;
    - dates validated (`0000:00:00` and years outside 1900–2100 dropped);
    - with `OffsetTimeOriginal` still `local` (the wall time is kept);
    - rationals become floats; strings stripped of NUL and capped.
  - `video_meta(probe) → {taken_at, taken_kind: "utc"|"local"}`:
    - `format.tags.creation_time` (ending in `Z` → `utc`);
    - `com.apple.quicktime.creationdate` with an offset → `local` plus its wall time;
    - location tags are ignored.
  - `run_meta(request)`, mode `meta`: headers only, many sources in one child, errors per file → `{"state":"ready","results":{sha: meta}}`.
- **`api/app/photometa.py`** (replaces P0's stub):
  - job type `photometa`, a chained cursor job (key `photometa:backfill`, payload `{after: sha}`);
  - each run takes 200 blobs with `meta_version < VERSION` and an image or video mime;
  - one child via `previews.render(…, mode="meta")`, then per blob `capture = photodate.split_capture(meta)` and `await photodate.on_blob_meta(session, blob, capture, meta.get("exif"), settings.timezone)`;
  - then `jobs.chain(session, "photometa", {after}, "photometa:backfill")`;
  - `on_dead` sets `meta_version = VERSION` for that batch, so it cannot loop;
  - `enqueue_backfill(settings)` at startup (`jobs.enqueue` with the key; it dedupes against a queued or running chain).
- `api/app/routers/photos.py` and `api/app/routers/albums.py` (P0 stubs, included).
- `web/src/photos/`:
  - `PhotosPage.tsx` (`/fotos`); `timeline.ts` (pure layout, prefix sums, scrubber mapping); `Scrubber.tsx` (drag plus PageUp/PageDown/Home/End);
  - `AlbumsPage.tsx`, `AlbumPage.tsx` ("Downloaden" via `GET /albums/{id}/ids` → `crossApi.archiveLink`; disabled with the reason when `truncated`), `AddToAlbum.tsx` (`AddToAlbumDialog`), `PublicAlbum.tsx`;
  - `api.ts`, `publicAlbumApi.ts`, `photos.css`.
- **Tests and docs:**
  - pytest: `api/tests/test_render_meta.py`, `test_photometa.py`, `test_photos.py`, `test_photos_speed.py`, `test_albums.py`, `test_album_access.py`, `test_public_album.py`;
  - vitest: `web/src/photos/{timeline,range}.test.ts`;
  - e2e: `test_photos.py`, `seed_photos.py` (50k rows over `seed.make_blobs(2000)`, with spread dates);
  - `docs/fase-3/toetsenbord-p5.md`.

**Wijzigt (exclusief)**
- `photodate.py` (P0 wrote it): adjustments only, same signatures.
- `access.py`, `resolve()`:
  - for ids with no tree grant and no trashed ancestor, one extra query (`album_item JOIN share ON share.node_id = album_item.album_id WHERE album_item.node_id IN ids AND live_share() AND for_user(user)`);
  - it gives `Grant("read", root_id=item, chain=[Step(item, name, "read")], direct=True)`;
  - `watchers`/`viewers_of` stay tree-based (documented);
  - this makes archive/link (P2) and download work for album items automatically.
- `routers/shares.py`:
  - `shareable()` accepts `kind == "album"`;
  - `write` on albums → 422 `album_read_only`; `upload` links on albums are refused.
- `routers/public.py`:
  - `inside()` for album links: an item is in `album_item` with a live ancestry → `[album_id, item]`;
  - `children()` rejects albums (code `album`);
  - `PublicShare.kind` includes `album`.

**API**
```
GET  /api/v1/photos/months → { months: [{ month: "2026-10", count }] }          # desc; GROUP BY taken_day/100 op ix_node_owner_taken
GET  /api/v1/photos?month=YYYY-MM&after=&limit≤500 → { items: PhotoItem[], next }
GET  /api/v1/photos/ids?from=<cursor>&to=<cursor> → { ids: uuid[≤10000], truncated }
PhotoItem = { id, name, mime, size, modified_at, media: "image"|"video", taken_at, taken_day, width, height, duration, etag,
              thumb, large, shared }
  scope: owner=user, taken_day NOT NULL, deleted_at NULL, parent_id IN live_dirs(root); gasten 403 (code guest)
  order: taken_day DESC, taken_at DESC, id DESC; cursor = base64url(taken_day|taken_at|id)
POST   /api/v1/albums { name, description? } → AlbumOut
GET    /api/v1/albums → { albums: AlbumOut[] }                                    # eigen + gedeeld met mij (live)
GET    /api/v1/albums/{id} → AlbumOut        PATCH { name?, description?, cover_id? } → AlbumOut
DELETE /api/v1/albums/{id} → 204             # eerst share.deleted aan ontvangers, dan node weg
GET    /api/v1/albums/{id}/items?after=&limit≤500 → { items: PhotoItem[], next }  # trashed weg via één ancestry-query per pagina
GET    /api/v1/albums/{id}/ids → { ids: uuid[≤10000], truncated }                 # live items, voor zip en selectie
POST   /api/v1/albums/{id}/items { node_ids ≤1000 } → { added, skipped: ItemResult[] }   # codes not_own|not_media|already
POST   /api/v1/albums/{id}/items/remove { node_ids } → { removed }
AlbumOut = { id, name, description, owner: OwnerRef, permission: "owner"|"read", count, cover: {thumb, large}|null,
             first_taken_at, last_taken_at, shared, created_at, modified_at }
Events: album.changed {id}, album.deleted {id} → eigenaar + access.watchers(album)
GET  /api/v1/public/album/items?after=&limit≤500 (Bearer key) → { items: PublicPhotoItem[], next }  # PhotoItem zonder shared/owner
Delen: bestaande POST /files/{album_id}/shares (alleen read), /links (alleen read); /shared-with-me geeft kind "album"
```

**Migratie.** Geen.

**Tests**
- pytest:
  - **EXIF**:
    - from in-test JPEGs (Pillow writes EXIF, GPS and `OffsetTimeOriginal`); a bad date; HEIC when pillow-heif can write it;
    - **no GPS value anywhere**: not in `preview_meta` and not in any response (`/preview`, `/photos`, public).
  - **video time**:
    - `creation_time 2024-06-30T22:30:00Z` → `taken_at 2024-07-01 00:30`, `taken_day 20240701` (Europe/Amsterdam);
    - QuickTime `2024-07-01T00:30:00+0200` → `00:30`.
  - **backfill**:
    - an old blob with `preview_meta {width, height}` has `exif` after the backfill, and `GET /files/{id}/preview` returns it;
    - the chain runs over 450 blobs in three runs;
    - starting it twice gives no second chain;
    - `on_dead` stops the loop.
  - **timeline**: nothing inside a trashed folder; cursor paging; month counts; a guest gets 403; `size`/`modified_at` present.
  - **speed** (`test_photos_speed.py`): 50k seeded rows; the median of 5 handler calls for `/photos/months` and `/photos?month=` is under 100 ms.
  - **album access**:
    - a revoked or expired share; a group member removed; a trashed or purged item; a guest viewer;
    - writes stay 403; adding someone else's file is refused;
    - the public album link;
    - archive/link over album ids from a share works, and stops after revocation.
- vitest: layout offsets, scrubber ↔ offset, range selection across unloaded months (via `ids`).
- e2e:
  - 50k seeded: scroll and scrubber (p95 frame ≤ 20 ms at 1×);
  - Shift/Mod selection; "Toevoegen aan album";
  - share with a second user, who sees it under Gedeeld met mij and opens a photo read-only;
  - a public album link;
  - an album zip;
  - the Viewer with ←/→ through the timeline.

**Klaar als**
- `test_photos_speed.py` is green.
- Every access test is green.
- The timeline shows upload dates before the backfill and EXIF dates after it.
- Keyboard-only passes; `check:i18n` 0.

---

### P6 — Beheer, account, delen, prullenbak en versies

**Doel.** Full user management, quotas, system status, storage per node, the jobs view, a split `/beheer`; account storage, theme and language; share-UI additions; a full trash; better version history.

**Dekt.** Admin (users, quota, jobs, system status, storage per node); the share UI; trash; version history; the account side of the auth flows.

**Maakt**
- `web/src/admin/`:
  - `Overview.tsx`, `Users.tsx`, `UserDialog.tsx`, `Storage.tsx`, `Jobs.tsx`, `SecurityTab.tsx`;
  - `usePolling.ts`, `api.ts`, `admin.css`.
- `web/src/shares/MyShares.tsx` (`/gedeeld/door-mij`).
- `web/src/trash/api.ts`; `web/src/versions/upload.ts` (`replaceWithProgress(nodeId, file, onProgress, signal)`).
- `api/app/usage.py`: `local_storage(settings) → NodeStorage`; the scan runs in a thread, cached and refreshed by housekeeping every ~10 minutes.
- `api/app/userdelete.py`: job `user.delete`.
- **Tests and docs:**
  - pytest: `api/tests/test_users_admin.py`, `test_users_queries.py`, `test_user_delete.py`, `test_admin_status.py`, `test_admin_storage.py`, `test_jobs_admin.py`, `test_trash.py`, `test_myshares.py`, `test_sessions_device.py`;
  - vitest: `web/src/admin/*.test.ts`, `web/src/trash/*.test.ts`;
  - e2e: `test_admin.py`, `test_trash_versions.py`, `test_sharing_ui.py`;
  - `docs/fase-3/toetsenbord-p6.md`.

**Wijzigt (exclusief)**
- `Beheer.tsx`:
  - sub-routes `/beheer`, `/gebruikers`, `/opslag`, `/taken`, `/groepen` and `/beveiliging` with `ui/Tabs`;
  - SetPassword as a Dialog;
  - fixes the quota bug (empty → `null`).
- `BeheerGroups.tsx`, `BeheerJobs.tsx`: dialogs and CSS instead of inline styles.
- `Account.tsx`: Opslag (`UsageBar`), Taal (`setLocale`), Thema, sessions confirm, device labels, i18n.
- `Trash.tsx`:
  - `VirtualList` (`keepIndex`) and a SelectionStore;
  - "Terugzetten (n)", "Definitief verwijderen (n)", "Prullenbak legen" behind confirm;
  - "Was in", folder sizes, the total; `useReloadOn(node.*)`;
  - Enter restores; **Shift+Delete or Shift+Mod+Backspace** purges.
- `Versions.tsx` and `versions.css`: upload with progress and cancel; "Bekijken" opens the `<Viewer>` with `version`; `useReloadOn`; confirm dialogs.
- `ShareDialog.tsx`:
  - an expiry date for people and groups;
  - a combobox with `aria-activedescendant`;
  - **`nodes?` multi mode**:
    - at most 100 items (the caller enforces it, the dialog guards again);
    - people and groups only;
    - items you do not own are disabled with the reason;
    - a sequential loop over `POST /files/{id}/shares` with progress;
  - album rules: no "Kan bewerken" and no upload link when `kind === "album"`.
- `SharedWithMe.tsx`: `nodeHref()` (albums → `/album/:id`), `<Viewer>` for files.
- `sharing.ts`:
  - `nodeActions(album)` returns only `open` (plus `share` for the owner);
  - `PERMISSION_LABELS` becomes an object with getters (`get read() { return t("sharing.perm.read") }`);
  - `expiryLabel` uses `fmt` and `t()` with `common.expires.*` keys only (P0 already does this; rule 2).
- `share.css`, `account.test.ts`, `sharing.test.ts`.
- `routers/auth.py`:
  - `PATCH /users/{id}` and `DELETE /users/{id}`;
  - `list_users` with one grouped usage query;
  - sessions get `browser`/`os`.
  - (P0 already did the disabled enforcement and the codes.)
- `useragent.py`: `device_parts()` → `(browser, os)`.
- `routers/admin.py`: `system`, `notices`, `/admin/storage`, trash retention.
- `cluster.py`: notices with codes.
- `certs.py`, `acme.py`, `serve.py`: `error_code` plus params in HttpsStatus.
- `jobs.py`: `counts_by_type`, `running`, `oldest_queued`, `delete_failed`, plus housekeeping hooks (storage cache refresh; retention purge in batches of 500 under `blobs.lock`). P0's `chain`/`run_after` stay.
- `routers/trash.py`: extends P0's version with overview, purge, empty and retention. `POST /trash/restore` keeps P0's contract.
- `routers/jobs_admin.py`, `routers/myshares.py`, `routers/groups.py`, `cli.py` (`make-admin <email>`).

**API**
```
PATCH  /api/v1/users/{id} { display_name?, email?, role?, quota_bytes? (≥0, 0 = onbeperkt), disabled?: bool } → UserOut
       409 email_taken | last_admin | guest_has_files; blokkeren zet disabled_at en trekt alle logins in
       (revoke_other_logins(session, id, None)); deblokkeren wist disabled_at
DELETE /api/v1/users/{id} → 202 { job_id }    # 409 not_disabled tenzij geblokkeerd; 409 last_admin
   job user.delete: share.deleted aankondigen; uploads.release; alle nodes van de eigenaar (boom + kind="album") in
   batches van 500 onder blobs.lock: drop_versions, add_refs(-1), kinderen vóór ouders; collect_garbage; dan de user-rij
GET    /api/v1/users → UserOut[] met disabled, usage: { files_bytes, trash_bytes, versions_bytes, file_count }   # vast aantal queries
GET    /api/v1/admin/status → zoals nu + system: { version, mode, started_at, uptime_s, db_kind, db_bytes,
       jobs: { queued, running, failed, oldest_queued_at, runner_alive }, https: { state, expires_at, days_left } }
       + notices: [{ code, params, text }]
GET    /api/v1/admin/storage → { nodes: NodeStorage[], totals: {…}, users: [{…}] }      # als revisie 1
GET    /api/v1/admin/jobs?before= → { counts, by_type, running, oldest_queued_at, failed, next_before }
DELETE /api/v1/admin/jobs/{id} → 204 | 409 not_failed
GET    /api/v1/admin/trash → { retention_days }   PUT { retention_days: 0..3650 }
GET    /api/v1/trash → zoals nu
GET    /api/v1/trash/overview → { items: [{ node, location: [{id,name}]|null, size, restore_to: "original"|"root" }], count, total_bytes }
POST   /api/v1/trash/restore { ids ≤10000 } → { results: ItemResult[] }   # contract van P0
POST   /api/v1/trash/purge   { ids ≤10000 } → { results }                 # batches van 500 onder blobs.lock; collect_garbage
DELETE /api/v1/trash → { purged }
GET    /api/v1/shares → [{ share: ShareOut, node: NodeOut, path: [{id,name}] }]   # gasten 403
GET    /api/v1/auth/sessions → zoals nu + browser: str|null, os: str|null
```

**Migratie.** Geen.

**Tests**
- pytest:
  - **users**:
    - the last-admin rules (demote, block, delete, race with two admins); email taken; guest with files;
    - blocking revokes sessions (a refresh gets 403 `account_disabled`, the SSE stream ends);
    - **N+1** (`test_users_queries.py`): a SQLAlchemy `before_cursor_execute` counter for `GET /users` gives the same number of statements with 1 and with 50 users.
  - **user delete**: ref counts drop and the blob files disappear; albums and shares are gone; recipients get `share.deleted`.
  - **admin**: status `system`/`notices`; storage scan cached (not per request); jobs by type, running, delete failed.
  - **trash**: overview, bulk purge, empty, retention via housekeeping.
  - my shares; session device parts; the `make-admin` CLI.
- vitest:
  - usage segment math; notice code → text; trash selection;
  - `PERMISSION_LABELS` follows the locale (getter);
  - `nodeActions(album)`;
  - multi-share capped at 100.
- e2e:
  - admin creates, edits, blocks and deletes a user;
  - the quota bar; `/beheer/*` tabs by keyboard;
  - Account → EN, the whole UI in English after the reload;
  - trash bulk and empty (Shift+Mod+Backspace);
  - version upload with progress, Bekijken, terugzetten;
  - ShareDialog expiry; "Gedeeld door mij";
  - a shared album in Gedeeld met mij offers no Kopiëren.

**Klaar als**
- `test_users_queries.py` is green; `/beheer` is lazy.
- No native confirm or prompt left in P6's files.
- All admin texts in EN; `check:i18n` 0; keyboard checklist passes.

---

## 3. Fundament (P0, eerst en sequentieel)

P0 is small in behaviour but wide in surface. It is the only package that touches the hotspots. Three disjoint workers:
- **P0-api** (`api/`; first commit: the mechanical codes pass, then the contracts);
- **P0-web** (`web/`);
- **P0-e2e** (`e2e/`; finishes after the other two are merged into the P0 branch).

It merges as one PR into the fase-3 base, tagged `fase3-basis`. The app looks and works as now, apart from the new shell, theme switch, route table, dialogs and error shape.

### 3.1 P0-web: interfaces (frozen after P0)

**CSS.**
- `web/src/tokens.css` and `web/src/base.css` replace `styles.css`.
- Token pass over `files.css` and `share.css`, with these moves:
  - `.version-*` → new `versions.css` (P6, imported by `Versions.tsx`);
  - `.wide-only`, `.owner-note`, `.muted`, `.link`, `.dialog` and `button.primary` (plus their media query) → `base.css`;
  - `.dropzone`, `.arrived` and `.public-*` → new `public.css` (P4).
- The `<style>` in PreviewPanel moves to CSS.

```
--bg --surface --surface-2 --fg --muted --line --accent --accent-fg --ok --warn --bad
--hover --selected --selected-fg --danger-bg --backdrop --focus --focus-w:2px --tag-1..7
--space-1..6 (0.25 .5 .75 1 1.5 2rem) --radius-s/m/l (4/6/10px) --text-s/m/l/xl (.875/1/1.15/1.4rem) --leading:1.45
--shadow-1/2 --z-sticky:10 --z-menu:100 --z-toast:200 (dialoog = top layer) --motion:150ms (0 bij reduced-motion)
--row-h:44px --row-h-compact:36px --cell-min:160px --font --mono
:root[data-theme=dark]{…}  :root[data-theme=light]{…}  @media (prefers-color-scheme:dark){:root:not([data-theme=light]){…}}
:focus-visible{outline:var(--focus-w) solid var(--focus);outline-offset:2px}
.tag-c1 … .tag-c7 { --tag: var(--tag-1) … }       /* tagkleur via klasse, nooit via style-attribuut */
```
`web/public/boot.js` (synchronous in `<head>`, try/catch): sets `data-theme`, `lang` and every `<meta name=theme-color>` (content set, `media` removed) from `platform-theme`/`platform-locale`. Tokens include `--line-strong` (the border of inputs, ≥ 3:1); `--line` stays for dividers.

**Router** (`router.ts`, `routes.tsx`, `links.ts`): the signatures from revisie 1 are unchanged, with one addition to `RouteDef`:
```ts
export interface RouteDef { pattern: string; load: () => Promise<{ default: ComponentType<RouteProps> }>;
  areas: readonly Area[]; title: MessageKey; section: NavSection | null; roles?: ("admin" | "user" | "guest")[];
  preload?: (p: Record<string, string>) => void }
// navigate, back, useLocation, usePath, useQueryParam, match, isPlainLeftClick, Link, useRestoreScroll,
// NavSection, useNavSection, RouteProps, nodeHref — als revisie 1
```
**Routes** (all in P0, all lazy; `load` waits for `Promise.all([chunk, loadAreas(areas)])`):

| Path | Page | Owner | Areas |
|---|---|---|---|
| `/`, `/map/:id` | Files | P2 | browser, upload, sharing |
| `/zoeken` | Search | P2 | browser, sharing |
| `/gedeeld`, `/gedeeld/door-mij` | SharedWithMe, MyShares | P6 | sharing, browser |
| `/prullenbak` | Trash | P6 | trash, browser |
| `/fotos`, `/albums`, `/album/:id` | photos/* | P5 | photos, browser, sharing |
| `/beheer/:tab?` | Beheer | P6 | admin |
| `/account` | Account | P6 | account |
| `/s/:token` | Public | P4 | public, upload |
| `*` | NotFound, NoAccess | P0 | — |

Guests are sent from `/` to `/gedeeld`. After each navigation:
- `document.title` is set, focus goes to `<PageTitle>`, and the live region announces the page;
- a failed chunk load reloads once.

At boot, the route chunk for `/` or `/map` is prefetched in parallel with `/auth/refresh`.

**i18n** (`i18n/`):
```ts
export type Locale = "nl" | "en";
export type Area = "common"|"shell"|"auth"|"pwa"|"browser"|"upload"|"viewer"|"public"|"photos"|
                   "admin"|"account"|"sharing"|"trash"|"versions";
// areas/<area>.nl.ts: export default { title: "Bestanden", selected: { one: "{n} geselecteerd", other: "{n} geselecteerd" } } as const
// areas/<area>.en.ts: const en: Translation<typeof nl> = {...}   → ontbrekende sleutel = tsc-fout
export type MessageKey;          // i18n/types.ts, uitsluitend met `import type` uit alle nl-bestanden: geen runtime-import
export function t(key: MessageKey, vars?: Record<string, string | number>): string
    // {name}; plural via vars.n + Intl.PluralRules; gebied niet geladen → de sleutel + console.error("i18n: …")
export function locale(): Locale
export function initI18n(): Promise<void>
    // common/shell/auth .nl statisch in de entry; bij en: die drie .en-chunks + alle errors/*.ts parallel
export function loadAreas(areas: readonly Area[]): Promise<void>
    // import.meta.glob(["./areas/*.ts", "!./areas/{common,shell,auth}.nl.ts"]) → één chunk per (gebied, taal); idempotent
export function useAreas(areas: readonly Area[]): boolean
export function lazyWithAreas<P>(load: () => Promise<{ default: ComponentType<P> }>, areas: readonly Area[]):
    LazyExoticComponent<ComponentType<P>>
export function setLocale(l: Locale, o?: { account?: boolean }): Promise<void>   // localStorage (+ PATCH /auth/me), dan reload
export function errorText(e: unknown): string    // nl: servertekst; en: catalogus[code].en met params, anders servertekst
export const fmt: { date; dateTime; time; wallClock; bytes; number; percent; relative; duration; list };   // als revisie 1
export const collator: Intl.Collator;  export function fold(s: string): string;
// errors/<area>.ts: export default { read_only: { en: "You can only view this" }, … } satisfies ErrorCatalog
```
- **Locale priority:** account > `platform-locale` > `navigator.languages` > nl.
- `formatBytes`/`formatDate` in `api.ts` delegate to `fmt`.
- **`scripts/check-i18n.mjs [--strict] [--files …]`** walks the AST with the `typescript` parser already present. It reports:
  - JSX text and literal `aria-label|title|placeholder|alt` outside `t()`;
  - **`t()` calls outside any function** (module level);
  - `setAttribute("style"` and `<style`.
  - `// i18n-ignore` opts a line out.
- **Area files.** P0 creates every area file and error catalog in §2.0, with the route-title keys in them.
- **Error catalogs:**
  - `errors/common.ts` gets every existing generic code (from `get_access`, `clean_name`, `Gone`, `no_room`, `NameTaken`, quota, guest), plus every code a frozen contract names: the ItemResult codes (`not_found`, `read_only`, `share_root`, `cross_owner`, `name_taken`, `into_itself`, `trashed`, `too_many`, `album`, `not_own`, `not_media`, `already`), plus `account_disabled`, `invalid_input` and `not_logged_in`;
  - area-specific existing codes go to their area catalog (auth → `auth.ts`, public/shares → `photos.ts`/`sharing.ts`, uploads → `upload.ts`, previews/jobs → `viewer.ts`/`admin.ts`).

**UI** (`ui/`): additions in P0: `Menu` items with `radio` (role `menuitemradio`; theme and language in the account menu); `Tabs` `idPrefix` plus `tabPanelProps(idPrefix, id)`; `activeShortcuts({ignore})`; `consumesKey` (a shortcut without Ctrl/Cmd never takes arrows/Home/End/PageUp/PageDown from a slider, radio, number field or media with controls, or Enter/Space from a button, link or checkbox); toasts and the live regions render in the top-most open dialog and every `toast()` is announced through the live region; `LanguageSwitch` applies with a button; the shell's icons are in `Icon.tsx`, the rest in its own chunk (`iconPaths.ts`). Otherwise the signatures from revisie 1 are unchanged (Button, IconButton, IconName, Icon, MenuItem, Menu, MenuButton, useContextMenu, useLongPress, confirm, prompt, DialogHost, Dialog, toast, announce, ToastRegion, Tabs, Field, Spinner, Skeleton, EmptyState, PageTitle, UsageBar, ThemeSwitch, LanguageSwitch, ShortcutHelp, OfflineScreen, selection and keys). The virtualization API changes:
```ts
fixedRange(scrollTop, viewport, rowH, count, overscan): { start: number; end: number }
variableRange(offsets: readonly number[], scrollTop, viewport, overscan): { start; end }
useVirtualRows(o: { count: number; rowHeight: number | ((i: number) => number); overscan?: number;
  scroller?: "window" | RefObject<HTMLElement>; listRef: RefObject<HTMLElement>; keepIndex?: number | null }):
  { start: number; end: number; totalHeight: number; offsetOf(i: number): number;
    scrollToIndex(i: number, align?: "auto"|"start"|"center"): void;
    extra: number | null }                 // keepIndex buiten [start,end): die rij wordt óók gerenderd, absoluut gepositioneerd
VirtualList<T>(p: { count; get(i); rowHeight; getKey; renderRow(item, i, style); header?; overscan?; scroller?;
  role?: "grid"|"listbox"|"list"; ariaLabel: string; onRangeChange?(s, e): void; keepIndex?: number | null })
useRovingFocus(o: { count; index; setIndex(i); columns?; scrollToIndex(i); getElement(i): HTMLElement | null }): KeyHandler
  // na scrollToIndex: focus opnieuw op getElement(index) in de volgende frame
```

**App shell** (`App.tsx`):
- the topbar, account menu and skip link;
- `<UploadTray/>` (P3, lazy), `<AppStatus/>` (P1), `<DialogHost/>`, `<ToastRegion/>`, `<ShortcutHelp/>`;
- a network error → `<OfflineScreen>`;
- a 403 `account_disabled` from refresh → back to Login with that message;
- a lazy part outside the page (upload tray, recovery codes) sits in a `ChunkBoundary`: a failed chunk shows its fallback (nothing; the recovery codes fall back to a plain list) without reloading (that would abort uploads or lose the codes), never a white page;
- a route the role may not open renders `NoAccess` through `PageArea` (title, focus and announcement like any page);
- scroll restoration: `useRestoreScroll` keeps a pop restore pending until the page is tall enough (≤ 3 s, or until the user scrolls), and scroll events do not overwrite the saved position meanwhile; the shell calls it for every route, a page may call it again with `ready`.

**Other frozen modules**
- `perf.ts`: `markReady(what)`.
- `api.ts`:
  - `request`/`rawRequest` become public; `tusCreate(…, meta?)`;
  - `FileNode.kind` includes `"album"`; `User.locale`, `User.disabled?`;
  - `ApiError.params?`; `errorFromBody` reads the new validation shape and `params`;
  - `lastRefresh`; `setAccountLocale(l)`.
- `PublicLink.request<T>` becomes public.
- `crossApi.ts`: as revisie 1 (`trash`, `move`, `copy`, `archiveLink` P2; `restore` **P0 → P6**; `addToAlbum` P5), with `archiveLink(ids ≤ 10000)`. `ItemResult.params?`. Until an endpoint exists the server answers 405 (a stub router without that method) or 404 (no route), without a code; `isNotBuilt(e)` recognises that (404/405/501 without code).
- `api.ts`/`publicApi.ts`: a failed fetch becomes `ApiError(0, …, "network")` (`isNetworkError` in i18n; a programming `TypeError` is not a network error); a server error without text gets code `http_error` `{status}`; the HEAD 413 gets `no_room`.
- `viewer/types.ts`, `viewer/index.ts`, `viewer/param.ts`: as revisie 1. `ViewerItem` keeps `size` and `modified_at` required, since PhotoItem now carries both. `Viewer = lazyWithAreas(() => import("./Viewer"), ["viewer"])`.
- **Upload contract** (`upload/index.ts`, signatures frozen, implementation P3):
```ts
export function UploadButtons(p: { folderId: string; canUpload: boolean }): JSX.Element
export function hasFiles(dt: DataTransfer | null): boolean
export function uploadDropped(dt: DataTransfer, folderId: string): void
  // MOET synchroon in de drop-handler worden aangeroepen, vóór elke await; leest dt.items/dt.files meteen uit.
  // Deze module wordt statisch geïmporteerd (nooit lazy op het droppad).
export const UploadTray: ComponentType          // lazyWithAreas(…, ["upload"])
```
  P0's stub: `uploadDropped` snapshots `Array.from(dt.files)` synchronously → `uploadQueue.add`.
- **Stubs with frozen signatures:**
  - `viewer/Viewer.tsx` (P4);
  - `pwa/register.ts`, `pwa/AppStatus.tsx`, `pwa/shell.css` (P1);
  - `browser/preload.ts` (P2);
  - `photos/*` stubs and `AddToAlbumDialog = lazyWithAreas(…, ["photos"])`, `PublicAlbum`, the album dispatch in Public.tsx (P5);
  - `shares/MyShares.tsx`; `ShareDialog` lazy with `["sharing"]` and the `nodes?` prop; `Versions` lazy with `["versions"]` (P6);
  - `scripts/budget.mjs` (P1).
- **Existing pages adapted to the route table**, as revisie 1. `events.ts`: `affectsFolder`/`affectsSharedWithMe` read `data.ids ?? [data.id]`.
- **Build setup:**
  - `tsconfig.json` with project references and `tsconfig.worker.json` (lib WebWorker) for `src/**/*.worker.ts` and `src/pwa/sw.ts`;
  - `package.json` scripts `check:i18n` and `budget`.

### 3.2 P0-api: contracts (frozen after P0)

**Step 1: the codes pass (its own commit).** Every one of the 144 `HTTPException(` raises in `api/app` becomes `errors.fail(status, code, message, **params)`, with the same status and Dutch text. Existing codes and constants (`GUEST`, `READ_ONLY`, `OWNER_ONLY`, `RATE_LIMITED`, `KEY_EXPIRED`, …) keep their codes.
- `totp_locked` gets params `{until: iso, seconds}`.
- The 401 from `/auth/refresh` gets `not_logged_in` plus `{setup_required}`.
- The only test edits: the 34 assertions that compare `["detail"]` to a string become `message_of(r) == "…"`; `message_of` lives in `tests/factories.py`. Status and text stay identical.
- `test_error_catalog.py` (frozen) checks three things:
  - every literal code in `fail(`/`item_fail(` in `api/app` has an `en` entry in exactly **one** `web/src/i18n/errors/*.ts`;
  - no code appears in two catalogs;
  - `HTTPException(` appears nowhere in `api/app` outside `errors.py`.

**Migration `0006_fase3.py`** (down_revision `0005`). Plain `ADD COLUMN`, with a downgrade.
```
user:        locale VARCHAR(5) NULL; disabled_at DATETIME NULL
blob:        taken_at DATETIME NULL; meta_version INT NOT NULL DEFAULT 0; render_version INT NOT NULL DEFAULT 0
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
`models.py` gets the matching columns plus `Album` and `AlbumItem`. Its comments document the floating-time convention, that `kind` can be `album`, and that GPS is not stored (fase 6).

**Python contracts:**
```python
# app/errors.py
def fail(status: int, code: str, message: str, *, headers: dict | None = None, **params) -> HTTPException
    # detail = {"message", "code", "params"?}; main.py: RequestValidationError → 422
    # {"detail": {"message": "Ongeldige invoer", "code": "invalid_input", "params": {"field": "body.name"}, "errors": [...]}}
    # komt de eerste fout uit een eigen validator (ValueError), dan is die Nederlandse tekst de message
class ItemResult(BaseModel): id: uuid.UUID; ok: bool; code: str | None = None; message: str | None = None
    params: dict | None = None; node: Any = None
def item_fail(id: uuid.UUID, code: str, message: str, **params) -> ItemResult
def item_of(id: uuid.UUID, error: HTTPException) -> ItemResult      # code, message en params uit detail
# app/config.py: timezone: str = "Europe/Amsterdam"; page_render_workers: int = 2; page_cache_mb: int = 2048;
#   rate_limits: bool = True
# app/security.py (daarna van P6)
class RateLimiter: enabled: ClassVar[bool] = True     # check/refuse doen niets als False; main.py-lifespan zet het uit settings
                                                      # en logt een waarschuwing bij False
async def active_user(session: AsyncSession, user_id: uuid.UUID) -> User     # 404 not_found als weg of disabled_at gezet
#   logged_in_user: disabled_at gezet → 401 account_disabled, vóór de controle op een ingetrokken login
#   files.download gebruikt active_user (P0 past dat aan); pages (P4) en archive (P2) ook
# app/routers/auth.py: login controleert disabled_at pas NA wachtwoord en 2FA → 403 account_disabled
#   (een geblokkeerd account is zo niet af te tasten); refresh: disabled → 403 account_disabled en de family ingetrokken,
#   ook als het token al ingetrokken, gebruikt of verlopen was (blokkeren in P6 trekt de logins ook in);
# app/routers/public.py: live_node → 404 link_not_found als de eigenaar geblokkeerd is (openen, kijken, downloaden, tus);
# app/routers/events.py: de stroom stopt binnen een PING als de gebruiker weg of geblokkeerd is;
#   UserOut.locale, UserOut.disabled; PATCH /api/v1/auth/me {locale: "nl"|"en"|null} → UserOut
# app/jobs.py (daarna van P6)
async def enqueue(session, type, payload, key=None, *, run_after: datetime | None = None) -> uuid.UUID | None
    # dedupe tegen ACTIVE zoals nu
async def chain(session, type: str, payload: dict, key: str, *, run_after: datetime | None = None) -> uuid.UUID | None
    # voor een lopende taak die zijn opvolger plant: dedupe alleen tegen status "queued" met deze key
# app/routers/trash.py ← list_trash/restore/purge letterlijk verhuisd uit files.py, plus:
#   POST /api/v1/trash/restore { ids ≤10000 } → { results: ItemResult[] }
#   (lus over de bestaande restore-logica, batches van 500 onder blobs.lock, session.commit() vóór het slot; notify_many)
# app/routers/jobs_admin.py ← admin_jobs/retry_failed/retry_job letterlijk verhuisd uit routers/previews.py
# app/__init__.py: __version__; main.py lifespan: app.state.started_at; stub-routers batch, archive (P2), folder_tree (P3),
#   viewer (P4), photos, albums (P5), jobs_admin, myshares (P6); await previews.enqueue_outdated(settings) en
#   await photometa.enqueue_backfill(settings) (stubs → 0), elk in try/except
# app/photodate.py (daarna van P5)
def wall_clock(moment: datetime, tz: str) -> datetime
def day_of(taken_at: datetime) -> int                                         # YYYYMMDD
def is_media(mime: str | None) -> bool
def stamp_values(mime, blob_taken_at, fallback: datetime, tz: str) -> dict
def stamp(node: Node, *, blob_taken_at, fallback, tz) -> None
def from_ms(value) -> datetime | None
    # lastModified van de client (ms sinds 1970, getal of tekst) → echt moment in UTC; onzin of buiten 1900-2100 → None
def split_capture(meta: dict) -> dict
    # haalt "taken_at", "taken_kind" en een eventuele "gps" uit meta (in place, gps wordt weggegooid) en geeft
    # {"taken_at", "taken_kind"} terug; meta houdt "exif"
async def on_blob_meta(session, blob: Blob, capture: dict, exif: dict | None, tz: str) -> None
    # taken_kind "utc" → wall_clock(t, tz); "local" → kloktijd zoals hij is; zet blob.taken_at en
    # blob.meta_version = render_meta.VERSION; UPDATE node SET taken_at, taken_day WHERE blob_id = sha AND taken_day IS NOT NULL;
    # exif gegeven → blob.preview_meta = {**(blob.preview_meta or {}), "exif": exif} (nieuwe dict)
# app/routers/files.py (daarna van P2)
async def place_file(..., client_mtime: datetime | None = None) -> Node     # stamp in beide takken
#   copy(): taken_at/taken_day mee; restore_version(): stamp met de blob van de versie; PUT /files/{id}/upload?mtime=<ms>
async def notify_many(session, nodes, type, *, old_parents=None, seen=None, below=None) -> None   # als revisie 1
# app/render_meta.py (stub, P5): VERSION = 0; image_meta(img) -> {}; video_meta(probe) -> {}; run_meta(request) -> {...}
# app/photometa.py (stub, P5): async def enqueue_backfill(settings) -> int: return 0
# app/previews.py (P4): async def enqueue_outdated(settings) -> int: return 0
```

### 3.3 Hook lines P0 places in P4-owned files (P4 must not remove them)
- **`render.py`:**
  - `image(source, out, max_pixels, meta=None, *, exif=False)`;
  - `render()` passes `exif=True` only for kind `image`;
  - inside the open image: `if exif: meta.update(render_meta.image_meta(img))`;
  - in `video()`: `meta.update(render_meta.video_meta(probe))`;
  - in `main()`: `if request.get("mode") == "meta": result = render_meta.run_meta(request)`.
- **`previews.py` `_store(…, tz)`** (`make()` passes `settings.timezone`): before `blob.preview_meta = meta` comes `capture = photodate.split_capture(meta)`, and before `session.commit()` comes `await photodate.on_blob_meta(session, blob, capture, None, tz)`.

### 3.4 e2e and measurement harness (P0-e2e, frozen)
- **`e2e/conftest.py`:**
  - Starts `python -m app.serve` with a temporary `PLATFORM_DATA_DIR`, `PLATFORM_WEB_DIST=web/dist`, free ports and **`PLATFORM_RATE_LIMITS=false`**.
  - Browser: `chromium.launch(executable_path=os.environ.get("E2E_CHROMIUM") or "/opt/pw-browsers/chromium" if present)`, otherwise Playwright's own (CI: `playwright install chromium`). The tested pair (Playwright 1.64 + Chromium 141) goes in `docs/fase-3/e2e.md`.
  - Fixture **`admin`**:
    - `/auth/setup` → 2FA setup and enable with an `app.totp` code;
    - `PUT` policy `require_2fa=false`;
    - disable the admin's 2FA with a **recovery code**, so later logins need no code.
  - `user_factory`; `context_as(user)` logs in through the context's request (no TOTP).
  - TOTP cases use dedicated users and wait for a new step between two code logins.
  - Fixture **`seeded(fn)`**: stop the server → `fn(data_dir)` → start it.
  - Every context gets an `addInitScript` that listens for `securitypolicyviolation` and `console.error` lines starting with `i18n:`. Any hit makes the test fail.
- **`e2e/seed.py`** (imports `app.models` and `app.previews.directory`, writes SQLite directly):
  - `make_blobs(data_dir, n=2000) → [sha]`: distinct small blob files, plus Pillow-generated WebP of 256 px (all) and 1024 px (the first 50) under `previews/<sha>/`, with `preview='ready'` and `preview_meta.sizes`;
  - `seed_folder(owner, n=10_000, blobs)`: rows round-robin over the blobs, with `search_name`, a mix of folders (5 %), images (50 %) and other files; `ref_count` adjusted;
  - `seed_public(owner)`: a folder of 200 items (50 images), a link without password, returning the `/s/<token>` URL.
- `e2e/perf/first_view.py` and `scroll.py` (§4).
- `e2e/test_smoke.py`: login → Files → upload → Viewer stub → theme switch → logout. It uses only exact accessible names and texts, so the packages can change everything else. **Smoke DOM contract** (P1, P2, P3, P4 keep these; also in `docs/fase-3/e2e.md`):
  - login (P1): heading "Inloggen"; fields labelled exactly "E-mailadres" and "Wachtwoord" (a toggle "Wachtwoord tonen" is fine); button "Inloggen";
  - shell (P0): navigation "Hoofdmenu" with link "Bestanden" (`aria-current=page`); the account button named after the user; menu "Accountmenu van <naam>" with "Thema" → `menuitemradio` "Licht"/"Donker"/"Systeem", and "Uitloggen";
  - Files (P2): the text "Deze map is leeg." in an empty folder; a file shows its name as text exactly once inside `#inhoud` (an icon before it is fine), and a **click on the name opens the viewer** (the name is a link or button inside the row; a click elsewhere on the row may select);
  - upload (P3): a button named exactly "Uploaden" that opens the file chooser;
  - viewer (P4): a dialog named after the file with a button "Sluiten"; `?bestand=` in the address while it is open;
  - public page (P4): an h1 starting with "Openbaar"; every item is a row (`<tr>` or `role=row`), a header row has `columnheader`.
- **`e2e/pytest.ini`** runs with `--strict-markers` and registers `perf`, `slow` and `p1` … `p6`; a package uses only these markers.
- **`e2e/test_theme.py`:** `platform-theme=dark` in localStorage; an `addInitScript` MutationObserver asserts that `<html data-theme="dark">` and the dark `--bg` are set before the first child of `<body>` is parsed.
- Run with: `cd api && uv run --with playwright pytest ../e2e`.

### 3.5 Tests and klaar als P0
- **pytest:**
  - `test_migration_0006.py`: a 0005 DB with data → upgrade → fallback dates set → downgrade;
  - `test_errors.py`: the validation shape and `fail`/`item_fail`;
  - `test_error_catalog.py` (§3.2);
  - `test_locale.py`;
  - `test_photodate.py` (P5 owns it afterwards):
    - stamp on upload, tus, copy and version restore; fan-out;
    - `split_capture` drops gps; UTC vs local conversion;
    - `on_blob_meta` merges exif into `preview_meta`;
  - `test_notify_many.py`;
  - **`test_jobs_chain.py`:**
    - a job that chains itself 3 times runs 3 times under `run_pending(at=…)`;
    - `enqueue` with a key while a chain runs gives `None`;
    - `run_after` is respected;
  - **`test_disabled.py`** (`disabled_at` set directly in the DB):
    - login with the correct password plus code → 403 `account_disabled`;
    - login with a wrong password → the normal 401 (nothing leaks);
    - refresh → 403; an access token → 401; download with an issued token → 404;
  - **`test_rate_limits.py`:** the default is `True`; `PLATFORM_RATE_LIMITS=false` turns `RateLimiter.enabled` off; with it on, the 11th login gives 429;
  - **`test_trash_restore_bulk.py`:** partial failures and `notify_many`.
- **vitest:** `router.test.ts`, `i18n.test.ts` (key and placeholder parity per area, plurals, `fmt`, `loadAreas` idempotent, an unloaded area gives the key), `keys.test.ts`, `virtual.test.ts` (including `keepIndex` → `extra`), `selection.test.ts`, `menu.test.ts`, `toast.test.ts`.
- **Klaar als:**
  - all existing tests are green (with only the `message_of` change), and ruff, tsc and the build are clean;
  - `test_smoke.py` and `test_theme.py` are green;
  - `check:i18n` reports 0 for P0's files;
  - there is no `window.confirm`/`prompt` in P0's files;
  - the decisions are recorded as B19 onwards in `docs/fase-0/beslissingen.md`, and `docs/fase-3/README.md` has a skeleton.

---

## 4. Prestatie

**Without Lighthouse as a dependency.** Everything runs from `e2e/perf/` with Python Playwright and CDP, against the real server (`python -m app.serve` with a production `web/dist`). It needs no npm package and nothing in the image.

1. **Size budget**, a hard CI gate (`npm run budget`, P1). The limits hold with all areas filled; it runs again at integration.

| What | Limit (gzip) |
|---|---|
| Initial JS (entry, static imports, vendor, common/shell/auth NL) | ≤ 100 KB |
| Files route chunk (including the browser, upload and sharing NL dictionaries) | ≤ 45 KB |
| Any other lazy chunk | ≤ 50 KB |
| Highlighter or worker chunk | ≤ 60 KB |
| Initial CSS | ≤ 12 KB |
| `sw.js` | ≤ 8 KB, with no `import`/`export` |

2. **First view < 1.5 s on LAN** (`first_view.py`, P0; a gate for P1, P2 and P4).
   - **Profile:** CDP latency 2 ms, 100 Mbit/s, CPU 1×. Cold runs use a fresh context with no SW and no HTTP cache; warm runs have the SW active.
   - **Measured:** `navigationStart` → `performance.mark("app:ready:<what>")`.
   - **Pages:** `/` (logged out), `/map/<10k>` (seeded with `make_blobs`), `/s/<seed_public>`.
   - **Gate:** median of 5 < 1,500 ms and p95 < 2,000 ms.
   - The SW registers only after `load` plus idle, so in cold runs it does not compete with the measured window. The script checks that no `sw.js` request falls before `app:ready`.
3. **Lighthouse > 90** (`lighthouse_like.py`, P1).
   - **Metrics:** FCP, LCP, CLS, TBT (via TTI) and Speed Index (screencast plus histogram distance), as in revisie 1.
   - **Score:** the same log-normal curves and weights:

     | Metric | Weight | Mobile p10 / median | Desktop p10 / median |
     |---|---|---|---|
     | FCP | 0.10 | 1800 / 3000 ms | 934 / 1600 ms |
     | Speed Index | 0.10 | 3387 / 5800 ms | 1311 / 2300 ms |
     | LCP | 0.25 | 2500 / 4000 ms | 1200 / 2400 ms |
     | TBT | 0.30 | 200 / 600 ms | 150 / 350 ms |
     | CLS | 0.25 | 0.10 / 0.25 | 0.10 / 0.25 |

   - **Throttling:**
     - mobile: 562.5 ms, 1,474.56 kbit/s down, 675 up, CPU 4×, 412×823 at DPR 1.75;
     - desktop: 40 ms, 10 Mbit/s, CPU 1×, 1350×940.
   - **Own gate:** score ≥ **91** (integer, median of 5): mobile on `/` and `/s/<link>`, desktop on `/map/<10k>`.
   - **Hard acceptance:** at integration, Jonas or the integrator runs the Lighthouse panel in Chrome DevTools (navigation, Performance only, the same mobile/desktop profile) once per gated page against the integration server. Each must score **> 90**. Results go in `docs/fase-3/prestaties.md`. If a page differs by more than 3 points from `lighthouse_like.py`, recalibrate the tool before fase 3 closes.
4. **10,000 files scroll smoothly** (`scroll.py`, P0; a gate for P2 and P5).
   - **Data:** 10k rows over 2,000 distinct thumbnails, run in **list and grid**; 50k photos over the same blobs for the timeline.
   - **Method:** inject a rAF frame recorder and a long-task observer, then:
     - scroll with `mouse.wheel(0, 400)` every 50 ms for 6 s;
     - press ArrowDown once;
     - press PageDown × 20, then End.

   | Check | Limit |
   |---|---|
   | p95 frame interval at CPU 1× | ≤ 20 ms |
   | p95 frame interval at CPU 4× | ≤ 34 ms |
   | Long tasks during scrolling | none over 50 ms |
   | Rendered `[role=row]` | ≤ 200 (plus 1 kept focus row) |
   | DOM nodes | ≤ 4,000 |
   | Thumbnails | ≥ 100 distinct URLs fetched and decoded during the run |
   | After the wheel scroll plus ArrowDown | `document.activeElement` is a row |
   | After End | row 10,000 (`aria-rowindex`) visible and focused |

5. **Other measured acceptance checks:**
   - no theme flash (`test_theme.py`, P0);
   - no N+1 in `/users` (`test_users_queries.py`, P6);
   - photos under 100 ms at 50k (`test_photos_speed.py`, P5);
   - no CSP violations and no missing i18n areas in any e2e (conftest, P0).
6. **Where it runs.**
   - Gates 2–4 run hard in the sandbox and in the integration run.
   - In CI the `perf` job is report-only (runner noise); the budget and the functional e2e are hard gates there.
   - Results go to `docs/fase-3/prestaties.json` and `prestaties.md`.
7. **What makes it achievable.**
   - Precompressed assets; immutable `/assets`, and no-cache plus 304 for the rest.
   - A static shell in `index.html`.
   - Lazy routes, viewers and dictionaries; a stable vendor chunk.
   - The route chunk and listing are fetched in parallel with `/auth/refresh`.
   - 200 first rows, light preview payloads, previews only for visible ids.
   - No web fonts.
   - Gzip in a thread for large listings.

---

## 5. Volgorde

1. **P0 Fundament, alone.**
   - P0-api (the codes pass first), P0-web and P0-e2e in parallel. They merge into one PR on the fase-3 branch, tagged `fase3-basis`.
   - Gate: §3.5.
   - From then on, frozen files change only through the **P0-hotfix-lane**.
2. **Parallel wave**, P1 to P6, each in its own git worktree off `fase3-basis`. Each delivers one PR with tests, `docs/fase-3/<pkg>.md` and a keyboard checklist.
3. **Merge order:** P1 (serving, CI, budget) → P3 → P2 → P4 → P6 → P5.
   - P2's undo works on `fasebasis` already, because P0 built the minimal `/trash/restore`.
   - P5 goes last: it changes `access.py` (security-central), and its e2e uses P2's batch and zip, P4's Viewer and P6's album rules.
   - Before each merge: rebase on the integration branch.
   - After each merge, the full set: `pytest` (including `test_error_catalog`), `ruff`, `tsc -b`, `vitest`, `build`, `budget`, `test_smoke.py`, `test_theme.py`, and that package's e2e.
   - A conflict outside the ownership table is a bug in this design: fix it in the PR and note it.
4. **Integration (sequential, after all merges).**
   - **Tighten:**
     - `check:i18n --strict` on in CI;
     - remove the stubs and compatibility layers that are no longer used;
     - run `budget` with all areas filled.
   - **Full e2e** from source with the production dist (Keuze 33) on `localhost`:
     - first-run setup with 2FA → the admin creates a second user and a guest;
     - upload a folder with subfolders, including a file/folder clash and resume after a reload;
     - seed 10k in one folder → scroll (list and grid), select, bulk move, copy and trash plus undo, zip download, context menu, keyboard-only use (including Mod+Backspace);
     - **open every viewer type from a sample set:**
       - images: JPEG with EXIF (the Camera/Belichting panel, no location), an animated GIF, HEIC;
       - documents: a 50-page PDF, kept open longer than the token lifetime (with `download_token_minutes` set to 1 for this run);
       - media: webm and mp3 play, mp4/H.264 shows the honest message in this Chromium;
       - text: cp1252 txt, md, csv, json;
       - office: docx, xlsx, pptx, odt;
     - the photo timeline with backfilled EXIF and video dates (UTC conversion checked) → create an album → share it read-only with the second user, who gets no Kopiëren and cannot download it as a folder → a public link (no GPS in any response) → the guest sees only what is shared → album zip;
     - share a selection (≤ 100) with a group; "Gedeeld door mij";
     - trash: bulk restore and empty → the blobs are gone from disk;
     - versions: upload with progress, view, restore;
     - admin: block (sessions, refresh and an issued download link all stop), delete (job, blobs cleaned), quota bar, storage per node, jobs;
     - switch to EN → every screen is English and API errors are translated;
     - PWA: the SW is active on localhost, offline reload shows the shell, the update toast works;
     - the keyboard checklist from every `toetsenbord-*.md`, keyboard only.
   - **Performance:** `e2e/perf/run_all.py` (the gates of §4) plus the DevTools Lighthouse runs as the hard acceptance; results in `docs/fase-3/prestaties.md`.
   - **Close-out:**
     - `docs/fase-3/README.md` with what is done and the known limits: album viewers get no live `preview.ready`; no PDF text layer; no transcoding; no GPS or location (fase 6); old videos, GIFs and text get their codec, animation and encoding meta only on a later re-render;
     - the fase-3 checkboxes in `docs/stappenplan.md`;
     - B19 onwards made final;
     - version 0.5.0 in `pyproject.toml`, `app/__init__.py` and `package.json`.
   - **Done when:** every item of the fase-3 list is covered by a merged package, and all four "Klaar als" criteria are proven by the measurements and e2e above, including the DevTools run.

---

## 6. Verwerkte en afgewezen kritiek

### Verwerkt

**Test harness**

| Kritiek | Fix |
|---|---|
| Rate limits and TOTP in e2e | Keuze 34; §3.2 `RateLimiter.enabled`; §3.4 `admin` fixture with a recovery code |
| Playwright and Chromium environment | Executable path (checked); webm/mp3 for playback; e2e from source (Keuze 33); CI job with ffmpeg |
| Seed thumbnails and the public fixture | `make_blobs(2000)` and `seed_public`; scroll gates in list and grid |
| Unmeasured acceptance checks | `test_theme.py`, `test_users_queries.py`, `test_photos_speed.py` |
| CSP regressions | conftest listener; `check:i18n` bans `setAttribute("style")`/`<style` |

**Jobs and previews**

| Kritiek | Fix |
|---|---|
| Chained jobs dedupe themselves away | `jobs.chain` and `run_after` (P0) |
| Re-render does nothing / old meta | `make()` skip rule (P4); client fallbacks (Keuze 20) |
| Old photos get no EXIF | `on_blob_meta` merges exif into `preview_meta` (P0 contract, P5 test) |

**Albums and access**

| Kritiek | Fix |
|---|---|
| Albums leak into tree code | Keuze 10; P2 `get_tree_access` plus the search and tag exclusions; P6 `nodeActions(album)`; P5 `/albums/{id}/ids` plus zip ≤ 10,000 |
| Blocked users keep download access | `security.active_user` and the login order (P0); used by download, pages and archive |
| Multi-share without a limit | cap 100 (Keuze 14, P2, P6) |

**Contracts between packages**

| Kritiek | Fix |
|---|---|
| P2 depends on P6's restore | P0 builds the minimal `/trash/restore` |
| P1 depends on P6's auth codes | the P0 codes pass, including `totp_locked` params |
| UploadQueue and missing exports | rule 2 (semantics plus the full export list); P3 compat vitest |
| Error codes collide | P0 codes pass; `common.ts`; the uniqueness check in `test_error_catalog` |
| i18n fills the first bundle | lazy areas, `MessageKey` via `import type`, the module-level `t()` ban, getters for `PERMISSION_LABELS` |
| CSS in someone else's file | the P0 moves (`versions.css`, `base.css`, `public.css`) |
| PDF token expires | `token_expires_in` and renewal (P4) |
| ViewerItem vs PhotoItem | `size` and `modified_at` in PhotoItem |
| Lookup returns trashed nodes | lookup filters, and the store also drops them |
| folders/tree file clash | `errors` and `created` in the response |
| Conflict check through tus is too expensive | `names/check` (P3) |
| Video times are UTC | `taken_kind` and conversion in the parent (Keuze 9) |
| Drop timing | the synchronous contract in §3.1 |
| Focus row unmounted | `keepIndex` |
| Mac Delete and image panning | Keuze 32; P4 ZoomView |

**Other**

| Kritiek | Fix |
|---|---|
| Lighthouse criterion weakened | ≥ 91 own gate, and the DevTools run > 90 as hard acceptance |
| GPS belongs to fase 6 | lat/lon, location, Locatie and Vraag 1 removed; "no GPS anywhere" tests |
| JSON compression on the loop | the thread above 64 KiB, plus a test |
| SW build and precache | IIFE via a nested build, shell-only precache, register after load and idle |

### Afgewezen kritiek

- **"Pin playwright to the version that matches the installed revision."** Not needed. I checked that Playwright 1.64 drives the installed Chromium 141 through `executable_path`, and `E2E_CHROMIUM` covers other machines. A pin would also have to move with every sandbox image.
- **"Keep one e2e test with limits on that expects 429."** Replaced by `test_rate_limits.py`, which checks the default, the env switch and a real 429. A second e2e server with limits on costs a full server start and tests nothing the limiter pytest does not already cover. The risk that matters is shipping with limits off, and the default test catches that.
- **"Add an image mode to conftest."** The alternative the critic offered fits better: e2e runs from source with the same production dist (Keuze 33). The image-specific behaviour is tested by the curl smoke in the images job. Docker is not available in the sandbox, so an image mode would split the gates.
- **"Re-render old videos, GIFs and text" (implicit in the render_version critique).** Not done; the critic's client fallback is used instead. Re-running ffmpeg on every existing video costs a lot for a hint the client has to verify anyway (MediaError decides). This is listed as a known limit.
- **Multi-share: "cap at 200" or "a batch share endpoint in shares.py".** Capped at 100 instead. The grantee's SSE queue (`QUEUE_SIZE 256`) also carries other events, so 200 is too close to the limit. A batch endpoint would make P6 depend on P5 inside the wave.
- **"The budget test runs with all areas filled" as a P1 gate.** During the wave the areas are not filled yet, so P1 enforces the budget on what exists and the full budget gate runs again at integration (§5.4).