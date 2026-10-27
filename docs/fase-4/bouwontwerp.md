> Werkdocument: het bouwontwerp van fase 4 (office), revisie 2. Revisie 2 is leidend. Revisie 1
> staat in [bouwontwerp-r1.md](bouwontwerp-r1.md).
> Nog niet besloten: de editorkeuze ligt bij Jonas (Vraag 1).

# Fase 4 (office): bouwontwerp, revisie 2

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `3ccee0a` (v0.5.0, migraties 0001–0006).

Revisie 2 verwerkt de kritiek op revisie 1: 15 punten over veiligheid (S1–S15) en 15 over
haalbaarheid (F1–F15). Ik heb elk punt nagelopen in de code, in het image van DS en in de
notities. Geen enkel punt bleek onjuist. Wat ik niet overnam, zijn delen van voorgestelde fixes.
§6 zegt per punt wat er veranderde, en waarom een deel niet.

De grootste veranderingen:

- **De app beslist over toegang, bij elk verzoek naar DS** (Keuze 6 en 7). Ook de tunnel kijkt
  naar het schrijfrecht van nu. En het eerste pakket van de socket moet over dezelfde key gaan als
  het pad. Een ingetrokken deling houdt zo echt op (S2).
- **De HTML van de editor alleen met het poort-cookie** (Keuze 7, §3.10). Een link van een andere
  site start nooit code van DS op onze origin (S1). De CSP op die HTML is nu volledig (S3).
- **DS leest alleen wat de app eerst controleerde** (Keuze 23). Een zip-bom komt niet bij DS. Wat
  via een publieke link binnenkwam, gaat alleen op verzoek naar DS (S4).
- **Opslaan kan tegen late, dubbele en verdwaalde callbacks** (Keuze 14–16; S6, S7, F5, S14).
- **Conversie is begrensd** (Keuze 25). Een bestand dat DS laat vastlopen, stopt na twee pogingen
  (S5). Grote bestanden gaan alleen op verzoek (S8).
- **De volgorde van de pakketten klopt** (§2.0, §5). Wat O2 nodig heeft, zit in O0. De echte DS
  draait in CI vanaf O0 (F1, F6, F7).
- **install.sh werkt bij een bestaande installatie** en houdt het image vast (F3, F4, F9, F15).

Bronnen (scratch, niet in het repo):

- `fase4/editors.md`: onderzoek naar OnlyOffice en Collabora, met een bron per bewering;
- `fase4/feasibility.md`: de proef met de echte DS in de sandbox;
- `fase4/code-map.md`: waar fase 4 in de code inhaakt;
- `fase4/judging.md`: het oordeel over ontwerp A en B, met de metingen M1–M11.

Bij een bewering over OnlyOffice staat waar hij vandaan komt:

- **[gemeten]**: gedraaid in de sandbox met `onlyoffice/documentserver:10.0.0.1`;
- **[doc]**: officiële documentatie of de broncode van OnlyOffice;
- **[image]**: gelezen in de bestanden van het image;
- **[niet getest]**: niet bevestigd. Daar ontwerpen we voorzichtig. De spike (§3.7) of een test
  (§4) beslist.

Afkortingen: **DS** = OnlyOffice Docs (Document Server). **App** = ons platform (API en web-app in
één container).

**Nagemeten bij revisie 1** (scripts in `fase4/judge/`):

- **M1.** `HeaderDeadline` (`serve.py`) sluit een geüpgradede WebSocket na `HEADER_TIMEOUT`. Dat
  gebeurt met httptools, en httptools is wat productie gebruikt. Met h11 niet. Fase 4 moet dat
  dus repareren (Keuze 6).
- **M2.** `ws="websockets"` in uvicorn 0.54 is verouderd (`UvicornDeprecationWarning`, het oude
  `websockets.legacy`).
- **M3.** Een eigen klasse voor `ws=` werkt. Uvicorn geeft de upgrade-request als bytes door, met
  h11 en met httptools. Na de fix van M1 blijft de verbinding open.
- **M4.** `api.js` werkt op een pagina met precies de CSP van de app (`WEB_CSP`). Geen enkele
  CSP-melding. De editor was klaar in 4,4 s en de getypte tekst stond in de opgeslagen versie.
  `api.js` zet stijlen alleen via CSSOM, en dat blokkeert CSP niet.
- **M5.** `Content-Security-Policy: sandbox …` op de HTML van DS breekt de editor. Hij wordt nooit
  klaar (origin `null`, CORS weigert, geen opslag, geen service worker).
- **M6.** `credentialless` op de iframe schermt nauwelijks af. De editor werkt, maar
  `parent.document` blijft bereikbaar: dezelfde origin.
- **M7.** Een container op een netwerk met `internal: true` komt niet op internet, niet bij
  `169.254.169.254` en niet bij andere Docker-netwerken. **Maar hij bereikt de host** via het
  gateway-adres van dat netwerk. Diensten van de LXC zelf (SSH, gepubliceerde poorten) zijn dus
  zichtbaar voor DS.
- **M8.** `docker info --format '{{.Driver}}'` geeft `overlayfs` op Docker 29 (containerd-image-store),
  niet `overlay2`.
- **M9.** De compose-bestanden van §2 O6 zijn geldig (`docker compose … config -q`, Compose v5.6.0).
- **M10.** `previews._store` gooit `previews/<sha>/` weg bij elke nieuwe preview en zet
  `blob.preview_meta` in zijn geheel opnieuw. Een PDF hoort daar dus niet.
- **M11.** Langs de proxy kwamen alleen GET-verzoeken (statische mappen, `cache/files/data/…`,
  `doc/<key>/c/`). Afdrukken, downloaden als en afbeelding invoegen zijn niet geprobeerd.

**Nagemeten bij revisie 2:**

- **M12.** `jobs.HOUSEKEEPING_TICK = 60` (`jobs.py:95`). Alle housekeeping-taken draaien na
  elkaar in één lus (`jobs.py:459-463`), ook `usage.refresh` en `trash.retention`. Een ronde van
  30 s via housekeeping kan dus niet (F2).
- **M13.** Het stuk `.env` van revisie 1 nagespeeld (`fase4/review-feas/envtest.sh`), met een
  `.env` waarin `OFFICE=aan` staat. `OFFICE=uit bash envtest.sh` laat `OFFICE=aan` staan, want
  `. "$ENV_FILE"` overschrijft de waarde uit de omgeving. En een mislukte
  `OFFICE_IMAGE=$(office_image)` stopt het hele script (exit 1) onder `set -e` (F3).
- **M14.** `default.json` van DS 10.0.0.1 [image]: `token.session.expires: "30d"`,
  `expire.sessionabsolute: "30d"`, `expire.files: 86400`, `editor.reconnection: {attempts: 50,
  delay: "2s"}`, `FileConverter.converter.maxprocesscount: 1`, `inputLimits` alleen voor de
  OOXML-soorten en vsdx (docx 50 MB, xlsx 300 MB, pptx 50 MB uitgepakte XML),
  `server.healthcheckfilepath` is een docx, `socketio.connection.maxHttpBufferSize: 1e8`,
  `server.limits_tempfile_upload: 104857600`, `callbackBackoffOptions.httpStatus: "429,500-599"`.
- **M15.** De broncode van DS (`DocService/sources/DocsCoServer.js`) [doc]: de socket logt in met
  het token uit `handshake.auth` (`session`, anders `token`). `drop` stuurt de browser een bericht
  en verbreekt, maar onthoudt niemand. Het document van een verbinding komt uit wat die verbinding
  zelf meldt, niet uit het pad van de URL. DS stuurt een levende verbinding wel nieuwe
  sessietokens (`sendDataRefreshToken`).
- **M16.** De spec van Fetch Metadata [doc]: de koppen `Sec-Fetch-*` gaan alleen naar een
  "potentially trustworthy URL". Over `http://<ip>` in het LAN stuurt de browser ze dus niet. Over
  https en via `localhost` wel.
- **M17.** De documentatie van DS [doc]: `onRequestRefreshFile` komt "when the editor is opened
  with a key that was already used to save a file", en ook na `onDocumentReady` als de verbinding
  wegviel, er intussen is opgeslagen en de editor opnieuw verbindt. De integrator roept dan
  `refreshFile` aan met een nieuwe key.
- **M18.** `node scripts/budget.mjs` op de huidige build: eerste lading JS 93,8 KB van 100 KB.
- **M19.** Uit de review, in de sandbox (niet opnieuw gedraaid): een database op revisie 0007
  laat v0.5.0 niet meer starten (`Can't locate revision identified by '0007'`). En compose vindt
  een image op digest alleen onder een repo-naam die lokaal bestaat. Na een pull van
  `mirror.gcr.io` lost `docker tag` naar de naam van Docker Hub dat op.

---

## 0. Uitgangspunten

### 0.1 Wat er sinds het stappenplan veranderd is

- **De limiet van 20 verbindingen bestaat niet meer.** Sinds 9.4 heeft Community geen limiet.
  `command license` geeft `connections: 2147483647` [gemeten]. 23 bewerkers op één docx werkten
  [gemeten]. "Meten en bewaken" wordt dus: zelf tellen, een eigen grens voor de belasting, en
  waarschuwen als een image toch weer een limiet meldt.
- **Geen Postgres, Redis of RabbitMQ.** Community draait sinds 9.4 als één proces, met alle
  bewerkstatus in het geheugen [doc, gemeten]. De regel in het stappenplan over "eigen Postgres,
  Redis en RabbitMQ" vervalt. Gevolg: een harde stop (kill, OOM) gooit niet-opgeslagen
  wijzigingen weg. Een nette stop slaat alles op [gemeten].
- **10.0 is strenger.** JWT staat standaard aan. Een leeg geheim weigert alles. `/command` en
  `/converter` nemen het token van de editorconfig niet meer aan. Uitgaande tokens dragen
  `operation: callback` of `operation: download` [doc, gemeten].

### 0.2 De invalshoek

1. **Zo min mogelijk bewegende delen.** Eén extra container (DS). Geen extra poort, geen proxy
   ernaast, geen nieuwe pakketten.
2. **De app is de enige ingang, en beslist elke keer.** De browser praat alleen met de app. DS
   heeft geen poort naar buiten en geen weg naar internet. Bij elk verzoek naar DS kijkt de app
   wie het is en of die nu nog mag bewerken. Wat DS zelf onthoudt (sessietokens, verbindingen),
   telt niet als toegang.
3. **Elk verzoekpad is nagelopen** (§3.9 en §3.10): wie mag het, met welk token, en waar wordt het
   gecontroleerd.
4. **Werk gaat nooit stil verloren.** Wat niet op het origineel kan, wordt een herstelkopie bij de
   eigenaar. Wat ook dat niet kan, staat op `/beheer`. Maar wat de eigenaar zelf definitief
   weggooit, blijft weg.
5. **De editor is vervangbaar.** Sessies, opslaan, versies, toegang en previews zitten in een kern
   die OnlyOffice niet kent. Alleen een kleine adapter spreekt het protocol van OnlyOffice.
6. **Bijwerken mag het draaiende platform niet breken.** Lukt iets met office niet, dan werkt het
   platform verder zonder editor, of met de vorige editor.
7. **DS leest alleen wat de app eerst controleerde.** Een bestand van buiten gaat niet vanzelf naar
   DS.
8. **Liever schrappen dan risico.** Wat we schrappen, staat onder Keuze 34.

### 0.3 Hoe de browser bij de editor komt (alleen 80 en 443)

```
browser ──https 443 / http 80──▶ app (Edge: tls, koppen, redirect)
                                  ├─ /api/v1/office/…   gewone API (ingelogd, 2FA)
                                  ├─ /office/… http     proxy in de app (ASGI-middleware)
                                  └─ /office/… upgrade  tunnel in de app (eigen ws-klasse)
                                        │ poort-cookie, recht van nu, padbeleid, koppen
                                        ▼
                         intern netwerk "office" (internal: true, geen uitgang)
                                        │ http://office:80
                                        ▼
                                       DS ──http://platform:8081──▶ app, interne luisteraar
                                             (alleen: document ophalen, callback,
                                              bron voor conversie)
```

- De browser laadt `api.js` en de editor-iframe van **dezelfde origin** (`/office/…`). Zo zijn
  CORS, cookies van derden en een tweede poort geen onderwerp [gemeten].
- Dezelfde origin heeft een prijs: code van DS draait met de rechten van onze origin. Keuze 9
  zegt wat dat betekent en wat helpt.
- DS praat terug via een **aparte luisteraar** van de app op poort 8081. Die poort is niet
  gepubliceerd.
- In het LAN zonder domein werkt alles over `http://<ip>/office/…`. Met een domein over https.

---

## 1. Keuzes

Elke keuze geeft de standaard en waarom.

### Product en protocol

1. **OnlyOffice Docs Community 10.0.0.1, vastgepind op digest**
   `sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3`. Die digest is gelijk
   op Docker Hub en `mirror.gcr.io` [gemeten].
   - Een optionele tweede container. Zonder DS werkt alles zoals in fase 3. Alleen bewerken en de
     echte office-previews ontbreken.
   - Waarom 10.0 en niet 9.4: kleiner image (0,94 tegen 1,40 GB) en minder RAM in rust (453 tegen
     962 MiB) [gemeten].
   - Nooit een tag zonder digest. Een image onder 9.4 brengt de limiet van 20 terug. De app weigert
     te bewerken bij een DS onder 10.0 (melding `office_version`).
   - Eén DS per node. Voor het cluster later: één document blijft op één DS (`shardkey`). Niet nu.
2. **De eigen API van OnlyOffice, geen WOPI.**

   | | Eigen API van OnlyOffice | WOPI |
   |---|---|---|
   | Opslaan | status 6 (knop, commando) en status 2 ±6 s na de laatste sluiter [gemeten] | OnlyOffice via WOPI: één PutFile aan het eind [doc] |
   | Wat een versie is | precies: `forcesavetype`, `history`, `users` | alleen PutFile; de host moet raden |
   | Locks | niet nodig | OnlyOffice eist ze [doc] |
   | Beveiliging | JWT in elke richting [gemeten] | access_token plus proof keys |
   | Bewaken | status 1 bij elke verbinding | geen signaal |
   | Nieuw bestand | ja | nee [doc] |

   De eigen API geeft precies wat "Klaar als" vraagt: samen bewerken, opslaan via callbacks met
   duidelijke statussen, forcesave en conversie, alles met JWT. Collabora spreekt alleen WOPI.
   Dat kan later als tweede adapter (Keuze 3).
3. **Een kern zonder OnlyOffice, en een kleine adapter.**
   - `app/office/engine.py` beschrijft de naad als `Protocol Engine` (§3.3). De kern (sessies,
     opslaan, versies, ronde, herstel, poort-cookie, proxy en tunnel, invoercontrole,
     conversietaak, beheer) praat alleen met `Engine`.
   - Alles wat OnlyOffice-specifiek is, staat in `app/office/oo/`: de JWT-vormen, `/command` en
     `/converter`, de editorconfig, het lezen van een callback, het padbeleid en het lezen van het
     eerste socketpakket.
   - Een test (`test_office_imports.py`) bewaakt dat: buiten `app/office/oo/` importeert alleen
     `engine.py` de adapter.
   - De contracttests (§4.4) beschrijven scenario's tegen een `Driver`. Nu is er één driver (de
     nep-DS). Een tweede editor krijgt dezelfde scenario's.
   - Collabora "klaarzetten" is dus: deze naad, de contracttests en één document
     (`docs/fase-4/collabora.md`). Geen WOPI-code in fase 4 (Vraag 5).
   - Waarom: zo is de terugval een structuur die tests bewaken, en geen belofte. En we bouwen geen
     tweede protocol voor een editor die misschien nooit aan gaat.

### De ingang

4. **De app is de enige ingang. DS zit op een intern netwerk zonder uitgang.**
   - DS heeft geen `ports:` in compose. Hij hangt alleen aan het netwerk `office` met
     `internal: true`. De app hangt aan `default` (de poorten 80 en 443) en aan `office`.
   - Zo komt DS niet op internet, niet in andere Docker-netwerken en niet bij `169.254.169.254`
     [gemeten, M7]. Dat is nodig, want het filter van DS op privé-adressen hield in de proef niets
     tegen. DS haalde bestanden op van 172.17.0.1 en 127.0.0.1, en kreeg antwoord van
     169.254.169.254 [gemeten].
   - **Kanttekening (M7):** DS bereikt wel de host, via het gateway-adres van het netwerk. Dat
     zijn de diensten van de LXC zelf: SSH en de gepubliceerde poorten van de app. Die vragen elk
     hun eigen login. Het runbook laat Jonas controleren dat er verder niets op de LXC luistert
     (`ss -ltn`). Een firewallregel op de host stellen we in fase 4 niet voor.
   - Prijs: "afbeelding invoegen via een URL" in de editor werkt niet. Afbeelding invoegen uit een
     bestand gaat via de proxy [niet getest, spike].
5. **Een proxy in de app op `/office/`, voor http.**
   - Een eigen ASGI-middleware (`app/office/proxy.py`) binnen `Edge`, vóór de catch-all van de
     web-app (`main.py:497`). Geen Traefik, geen nginx ervoor.
   - Het prefix gaat eraf. Naar DS gaan `X-Forwarded-Prefix: /office`, `X-Forwarded-Host` (de Host
     van de browser), `X-Forwarded-Proto` (`http` of `https`) en `X-Forwarded-For` (de echte
     peer). Zonder het prefix opent de editor nooit [gemeten].
   - **Verzoekkoppen:** alleen een vaste lijst gaat door (`Accept`, `Accept-Encoding`,
     `Accept-Language`, `Content-Type`, `Content-Length`, `If-None-Match`, `If-Modified-Since`,
     `Range`, `User-Agent`, `Origin`). Nooit `Cookie`, `Authorization`, `Referer`, `Forwarded` of
     `X-Forwarded-*` van de client.
   - **Antwoordkoppen:** weg met `Set-Cookie` (DS zet op `/` een cookie `ds_cache_reset` voor het
     hele domein), `Clear-Site-Data` (op `/` van DS: dat zou de cache van onze hele origin wissen),
     `Strict-Transport-Security` (dat doet Edge), `Server`, `Service-Worker-Allowed`,
     `X-Frame-Options` en `Content-Security-Policy` van DS. Erbij:
     - op HTML: `X-Frame-Options: SAMEORIGIN` en de CSP van Keuze 9;
     - op al het andere: `Cross-Origin-Resource-Policy: same-origin`;
     - op een svg ook `Content-Security-Policy: sandbox`, zoals `send_file` al doet.

     Edge laat die staan en voegt de rest toe. `Cache-Control` van DS blijft (`api.js`
     `no-store`, versiepaden `immutable`).
   - `Location` alleen als die relatief is of naar onze eigen origin wijst. Anders weg.
   - Bodies in stukken, hooguit 100 MB (zoals de nginx van DS). Gzip blijft gzip.
   - Alleen een vaste lijst padklassen gaat door; de rest geeft 404 (§3.10). Wie wat mag per
     klasse, zegt Keuze 7. De beheer- en server-eindpunten van DS komen nooit van buiten, ook niet
     achter een versieprefix: de nginx van DS stuurt `/<versie>/<pad>` gewoon door naar docservice
     [image].
   - **Editoracties met een body** (`POST`, bijvoorbeeld een afbeelding invoegen): per gebruiker
     hooguit 60 per minuut en 200 MB per 10 minuten. Daarboven 429 `rate_limited`.
   - DS niet bereikbaar: 502 `office_unavailable`.
6. **WebSocket via een eigen tunnel, zonder WebSocket-bibliotheek.**
   - Uvicorn krijgt `ws=Tunnel` op de publieke luisteraars. `Tunnel` is een eigen
     `asyncio.Protocol` (`app/office/tunnel.py`). Uvicorn geeft hem de herbouwde upgrade-request
     als bytes [gemeten, M3].
   - **Vóór de upgrade** leest de tunnel alleen de request-regel en de koppen (hooguit 16 KB). Hij
     controleert pad, `Origin` en de toegang van Keuze 7 (cookie, lid van sessie `<key>`, en nu
     schrijfrecht). Dan opent hij TCP naar DS en stuurt de herschreven upgrade.
   - **Na de upgrade: het eerste pakket.** De tunnel leest de frames van de browser tot het
     CONNECT-pakket van socket.io en het eerste `auth`-bericht (hooguit 64 KB en 10 s). Elk JWT
     daarin moet kloppen met het office-geheim (de config is van ons; het sessietoken van DS is
     met hetzelfde geheim getekend) en over de key van het pad gaan (`document.key`). Anders dicht.
     Daarna pompt hij alleen bytes, en leest hij niets meer.
     - Waarom: DS neemt het document uit wat de verbinding meldt, niet uit het pad (M15). Zonder
       deze controle opent wie lid is van sessie A, met een oud token, sessie B.
     - Het lezen zit in de adapter (`oo/stream.py`, achter `Engine.stream_guard`). De vorm van de
       frames legt de spike vast (§3.7 stap 12) [niet getest]. Lukt het lezen niet, dan geldt de
       terugval van Keuze 20: wie zijn recht kwijt is en toch verbonden blijft, stopt de hele
       sessie.
   - Weigeren gebeurt met een kale HTTP-regel en sluiten:
     - geen match of ander pad dan `/office/<versie>/doc/<key>/c/` → 404;
     - `Origin` ontbreekt of hoort niet bij de `Host` → 403;
     - geen geldig poort-cookie, geen lid, of geen schrijfrecht meer → 401;
     - meer dan `office_max_tunnels_user` (standaard 8) tunnels van deze gebruiker, of meer dan
       `max_editors` op deze key → 429;
     - boven 1.000 open tunnels in totaal → 503.
   - Een upgrade naar een ander pad sluit meteen. De app zelf heeft dus nog steeds geen
     WebSockets (B17 blijft).
   - Dezelfde regel als Edge voor poort 80: is er een certificaat en is de Host het domein, dan
     weigert de tunnel een upgrade over http.
   - Tegendruk: lezen pauzeren boven 1 MB in de schrijfbuffer van de andere kant, verder onder
     256 KB. Na 120 s zonder bytes dicht (socket.io pingt elke 25 s). Dicht aan de ene kant is dicht
     aan de andere.
   - **Elke 15 s** controleert de tunnel de toegang van zijn verbinding opnieuw (Keuze 7, met de
     cache van 15 s). Nee → dicht. Zo is uitloggen of een ingetrokken recht binnen 30 s uit de
     tunnel.
   - Een register van open tunnels: per key en per gebruiker. Daaruit komen de tellers van Keuze 27.
     `close_for(key, user)` en `close_all()` bij het stoppen van de app.
   - **De fix van M1:** `HeaderDeadline` overschrijft `handle_websocket_upgrade`. Bij een upgrade
     gaat de deadline weg en zet hij een vlag `_upgraded`. Daarna start `_update_deadline` hem
     nooit meer.
   - Waarom zelf: `ws="websockets"` is verouderd (M2), een expliciete afhankelijkheid botst met B19,
     en een tunnel kost weinig cpu. De proef deed hetzelfde met ±150 regels stdlib [gemeten].
7. **De app beslist over toegang, bij elk verzoek naar DS.**
   - `POST /api/v1/office/files/{id}/session` zet een cookie `office`: HttpOnly, `SameSite=Strict`,
     `Path=/office/`, `Secure` over https.
   - De waarde is een JWT met het **app-geheim**: `typ: office_gate`, gebruiker, login-family,
     2 uur geldig. DS ziet dit cookie nooit, want de proxy stuurt geen `Cookie` door.
   - **Per padklasse** (§3.10):
     - **statisch** (js, css, fonts, afbeeldingen, json, de service worker van DS, `api.js`):
       zonder cookie. Daar zit nooit HTML bij. Die bestanden zijn op elke DS gelijk en bevatten
       geen gebruikersdata. Een js-bestand als pagina openen, voert niets uit.
     - **editorpagina** (elke `*.html` onder `/office/`, en een omleiding die daarheen gaat):
       cookie verplicht. Stuurt de browser `Sec-Fetch-Dest`, dan alleen `iframe`, `frame` of
       `empty` (de service worker van DS); `document` en de rest geven 404. Over http in het LAN
       stuurt de browser die kop niet (M16). Dan beslist het cookie alleen. Omdat dat
       `SameSite=Strict` is, gaat het nooit mee met een link van een andere site.
     - **documentdata** (`cache/files/data/<key>_<n>/…`), **sessie** (`doc/<key>/c/…`, ook de
       tunnel) en **editoracties met een key in het pad**: cookie, lid van de sessie van die key,
       niet uitgesloten (`banned`), de sessie niet dicht, en **nu** schrijfrecht op het bestand
       (eigenaar die geen gast is, of een deling met `write` via `access.resolve`; iets in de
       prullenbak in de keten is geen recht).
     - **editorinstellingen** (`plugins.json`, `themes.json`) en editoracties zonder key: cookie,
       en lid van minstens één open sessie.
   - Per verzoek: handtekening, `exp`, gebruiker actief, login niet ingetrokken, en per key de
     punten hierboven. De uitslag staat **hooguit 15 s** in het geheugen (sleutel: sha-256 van het
     cookie plus de key). Een koude editorstart is 253 verzoeken [gemeten]; met de cache kost dat
     een paar vragen aan de database.
   - `POST /api/v1/office/gate` (cookie vernieuwen) en `POST …/save` eisen hetzelfde: lid, en nu
     schrijfrecht. De editorpagina vernieuwt het cookie elke 30 minuten.
   - **Wat DS onthoudt, is geen toegang.** DS geeft de browser een eigen sessietoken om opnieuw te
     verbinden, 30 dagen geldig (M14). En `drop` onthoudt niemand (M15). Daarom zit de controle in
     de app, op elk pad naar DS. Een browser die na een `drop` opnieuw verbindt, komt niet meer
     door de tunnel.
   - Waarom: zonder het cookie zijn de socket en de cache bereikbaar voor wie een key raadt of ziet.
     Zonder het recht van nu blijft wie zijn deling kwijt is, gewoon bewerken. En zonder het cookie
     op de HTML kan elke site de editor op onze origin laten starten.
8. **Wat DS aanroept, staat op een aparte luisteraar (poort 8081, niet gepubliceerd).**
   - Een kleine eigen ASGI-app (`internal_app`) met alleen drie routes:
     `/internal/office/doc/{key}`, `/internal/office/callback` en `/internal/office/src/{grant}`.
     Hij deelt de database, de blob-opslag en `app.state` met de hoofd-app.
   - De publieke poorten kennen `/internal/…` niet (404). Zo kan niemand van buiten een callback of
     een documentdownload proberen, ook niet met een gestolen token.
   - De luisteraar start alleen als office is ingesteld.
   - Op elke route eist de app ook het JWT van DS (Keuze 11). De poort is een extra slot, niet het
     enige.

### Dezelfde origin

9. **Het restrisico van DS op onze origin is echt. We noemen het, beperken het, en Jonas beslist.**
   - Wat er speelt: de editor draait in een iframe van onze eigen origin. JavaScript van DS heeft
     dus dezelfde rechten als onze web-app. Een fout in DS (XSS, of een kwaadaardig document dat
     code laat draaien) kan `POST /api/v1/auth/refresh` doen. Het refresh-cookie gaat mee
     (HttpOnly, `SameSite=Strict`, pad `/api/v1/auth`). Of het script past via `parent` de pagina
     van de app aan.
   - Wat niet helpt (gemeten): `sandbox` in de CSP van DS breekt de editor (M5). `credentialless`
     op de iframe laat `parent.document` bereikbaar (M6). Een eigen hostpagina verandert niets aan
     de origin.
   - Een andere origin kan alleen met een tweede hostnaam (`office.<domein>`). Dat vraagt een
     domein en een DNS-record, en in het LAN zonder domein kan het niet. Dat hoort bij fase 8.
   - Wat wel helpt, en wat we doen:
     - **Alleen wie nu mag bewerken, laadt de editor.** De HTML van DS komt alleen met het
       poort-cookie (Keuze 7). Dat cookie krijgt alleen wie een sessie opent, en het werkt alleen
       zolang hij schrijfrecht heeft. Lezers, publieke links en een beheerder die nooit bewerkt,
       krijgen het nooit (Keuze 22). Een link van een andere site laadt de editor nooit: het
       cookie gaat niet mee (`SameSite=Strict`), en over https weigert de proxy ook een navigatie
       (`Sec-Fetch-Dest: document`).
     - DS onveranderd en vastgepind op digest; plugins, macro's, AI en docbuilder uit
       (`customization.plugins: false`, `customization.macros: false`, `macrosMode: "disable"`,
       `PLUGINS_ENABLED=false`, `aiSettings.enable: false`, `docbuilderEnable: false`)
       [doc, de namen controleert de spike];
     - DS ziet nooit het app-geheim, nooit `Cookie` en nooit `Authorization`;
     - op elke HTML van DS zet de proxy een volledige CSP:
       ```
       default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval';
       style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:;
       connect-src 'self'; worker-src 'self' blob:; frame-src 'self'; media-src 'self' blob:;
       object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'
       ```
       Inline scripts en stijlen heeft DS nodig [gemeten]. `'unsafe-eval'` gaat eruit als DS
       zonder werkt. De spike meet het met alle acties (§3.7 stap 9). Breekt een richtlijn de
       editor, dan verruimt de spike precies die ene, met een notitie. `default-src 'self'` gaat
       er nooit uit;
     - **DS leest alleen gecontroleerde invoer** (Keuze 23 en 25). Elk zip-formaat gaat eerst door
       de invoercontrole. Wat via een publieke link binnenkwam, en wat groter is dan 20 MB, gaat
       alleen op verzoek van een ingelogde gebruiker naar de converter. Zo komt een anoniem
       bestand nooit vanzelf bij het onderdeel dat ook de editor bedient;
     - de app-pagina zelf houdt haar strikte CSP (M4).
   - Wat de CSP **niet** tegenhoudt: weglekken via navigatie (`top.location`, `window.open`) en
     handelingen binnen onze eigen origin. Dat kan geen CSP. Daarom tellen de andere punten, en de
     eigen hostnaam van fase 8.
   - Wat overblijft:
     - een fout in DS kan binnen onze origin handelen namens een ingelogde bewerker;
     - een bestand dat de invoercontrole doorstaat, kan nog een fout in de converter van DS raken.
       Dat bestand kwam dan van een ingelogde gebruiker, of iemand vroeg er zelf om.

     Dat staat bij de risico's (§5.1) en in Vraag 3.

### Geheimen en tokens

10. **Een eigen geheim voor office, los van het app-geheim.**
    - DS krijgt nooit `data/jwt.secret`. Daarmee zou hij access-tokens kunnen maken.
    - `install.sh` maakt twee geheimen in `/opt/platform/.env`: `OFFICE_JWT_SECRET` en
      `OFFICE_LINK_SECRET`, elk 64 hex-tekens uit `/dev/urandom`. Hex, zodat `.env` veilig als shell
      gelezen kan worden. Het bestand krijgt `chmod 600`.
    - De app krijgt `PLATFORM_OFFICE_SECRET=${OFFICE_JWT_SECRET}`. DS krijgt
      `JWT_SECRET=${OFFICE_JWT_SECRET}` en `SECURE_LINK_SECRET=${OFFICE_LINK_SECRET}`.
    - Vaste waarden zijn nodig. Zonder kiest DS bij elke start een nieuw geheim, ook bij een gewone
      herstart. Dan werken de tokens en de opslaglinks niet meer (403) [doc, gemeten].
    - De app weigert office als het geheim korter is dan 32 tekens of gelijk aan het app-geheim
      (melding `office_misconfigured`).
11. **JWT in elke richting (HS256, 60 s speling op de klok).** De volledige tabel staat in §3.9.
    Kort:
    - app → browser → DS: de hele editorconfig (zonder `events`), getekend in `config.token`,
      met `exp` 2 uur (net als het poort-cookie);
    - DS → browser: een eigen sessietoken om opnieuw te verbinden (30 dagen, M14). Dat laten we
      staan, tenzij de spike laat zien dat DS het bij een levende verbinding vernieuwt (stap 15).
      Dan zetten we `token.session.expires` op `"2h"`. De echte grendel is Keuze 7, niet dit token;
    - DS → app, document ophalen: `Authorization: Bearer` met `{"payload": {"url"}, "operation":
      "download"}`. De app eist dat `payload.url` precies de gevraagde URL is;
    - DS → app, callback: token in de body (platte velden) en in de header (velden onder
      `payload`), beide met `operation: callback`, 5 minuten geldig [gemeten]. De app neemt liever
      de body, want de header kan grote velden zoals `history` missen [doc]. De app gebruikt
      **alleen velden uit een geldig token**, nooit de losse body;
    - app → DS, `/command` en `/converter`: `token` in de body, met de parameters als payload.
      Zonder of met een fout token: `{"error": 6}` of `{"error": -8}` [gemeten]. Dat controleert de
      app ook zelf, steeds opnieuw (Keuze 28);
    - een token met het office-geheim werkt nooit als app-token, en omgekeerd: andere sleutel, en
      `typ` of `operation` wordt gecontroleerd.
12. **Geen geheimen in URL's.** De interne URL's bevatten alleen een willekeurige id: de
    document-key, of een grant-id van 128 bit voor conversies. Toegang komt uit het JWT van DS.
    `SECRET_PARAMS` in `main.py` krijgt er `md5`, `shardkey` en `cmd` bij, zodat de opslaglinks
    van DS en het sessietoken in `downloadas?cmd=` (spike A6) nooit in een log staan.

### SSRF

13. **De app volgt nooit een host uit een URL van DS.**
    - Bij de callback-`url`, de conversie-`fileUrl` en `getForgotten` neemt de app alleen pad en
      query. Het pad moet passen bij `^(/office)?/cache/files/data/<key>(_\d+)?/…` met **onze**
      key (bij conversie `conv_<key>`, bij forgotten `forgotten/<key>`). Daarna gaat het verzoek
      naar `PLATFORM_OFFICE_URL` (`http://office`).
    - Dat mag, want de handtekening van de link hangt alleen af van pad, `expires` en geheim
      [gemeten: herschreven URL 200, kapotte md5 403].
    - httpx zonder omleidingen, met `trust_env=False`, een tijdslimiet en een grens op de grootte
      (Keuze 16).
    - `ALLOW_PRIVATE_IP_ADDRESS` en `ALLOW_META_IP_ADDRESS` blijven uit (de standaard). Een URL in
      een geldig JWT gaat sowieso langs dat filter, dus onze eigen URL's werken [doc, gemeten]. We
      vertrouwen het filter niet: het interne netwerk is de grens (Keuze 4).

### Sessies, keys en opslaan

14. **Document-key = `<node-id hex>-<16 hex willekeurig>`** (49 tekens, binnen
    `[0-9A-Za-z._=-]{1,128}`) [doc].
    - Een tabel `office_session` (migratie 0007) houdt per open bestand één sessie bij: key,
      bestand, de inhoud waar de key voor staat, wie meedoet, wie eruit is gezet, de status.
    - **Wie welke key krijgt.** Onder het slot van het bestand (`node_lock`), in één transactie:

      | De sessie van dit bestand | De opener krijgt |
      |---|---|
      | geen, of `closed` | een nieuwe key |
      | `pending` (config uitgegeven, nog geen status 1), jonger dan 5 minuten | dezelfde key |
      | `pending`, ouder dan 5 minuten | de oude rij dicht (`lost`) en een nieuwe key, in dezelfde transactie |
      | `open`, en de laatste status 1 of `info` noemt minstens één gebruiker | dezelfde key |
      | `open` zonder iemand erin (DS gaat opslaan: wacht op status 2 of 4) | 409 `office_busy` |
      | `saving` | wachten op het slot; na 15 s 409 `office_busy` |

      Bij `office_busy` vraagt de editorpagina het elke 3 s opnieuw, hooguit 30 s lang.
    - Waarom geen lege `open`-key hergeven: DS gaat die key opslaan. Wie daarop opent, krijgt van
      DS `onRequestRefreshFile` (M17), of zijn werk valt tussen twee sessies.
    - Waarom `pending` tot 5 minuten: de eerste opener kan nog laden. Na een start van DS duren de
      fonts tot 280 s [gemeten]. Het is één venster, gelijk aan dat van de ronde (Keuze 20). Zo
      botst een nieuwe rij nooit met de unieke index op open sessies (§3.1).
    - **Een nieuwe key na elke afgesloten sessie** (status 2 of 4), dus na elke opgeslagen
      eindversie [doc: "Every time the document is edited and saved, the key must be generated
      anew"].
    - **Geen nieuwe key na een forcesave** [doc: anders stopt het samen bewerken]. De sessie
      onthoudt de nieuwe inhoud, zodat wie daarna meedoet dezelfde key krijgt.
    - De sha alleen is geen goede key. Dezelfde inhoud staat één keer op schijf en kan in veel
      bestanden zitten (ontdubbeling).
    - De gebruiker heet bij DS `ds_uid`: de eerste 24 hex-tekens van HMAC-SHA256(office-geheim,
      `"user:" + user.id`). Vast per gebruiker (twee tabbladen tellen als één persoon), zonder de
      echte id.
    - Wie een config krijgt, komt in `members`. Wie zijn recht kwijt is, gaat eruit en komt in
      `banned` (Keuze 20). Krijgt hij later weer recht en opent hij opnieuw, dan haalt `POST
      …/session` hem uit `banned`.
15. **Wat elke status van de callback doet:**

    | status | betekenis [doc] | de app |
    |---|---|---|
    | 1 | iemand opent of sluit | `connected` bijwerken, `pending` → `open`; gebeurtenis `office.presence`; `{"error": 0}` |
    | 2 | iedereen weg, klaar om op te slaan | opslaan (Keuze 16), sessie dicht |
    | 3 | fout bij opslaan | staat er een `url`, dan **een herstelkopie** naast het origineel, nooit eroverheen; melding aan de eigenaar en op `/beheer`; sessie dicht |
    | 4 | gesloten zonder wijzigingen | sessie dicht |
    | 6 | forcesave, de sessie loopt door | opslaan als tussenstand (Keuze 17), key blijft |
    | 7 | fout bij forcesave | loggen, niets opslaan; status 2 volgt later |

    - **Volgorde.** DS probeert een callback opnieuw na 429 of 5xx (M14). Een oude status 6 kan
      dus na een nieuwere aankomen. Daarom draagt elke opslag een **merk** (`mark`): bij OnlyOffice
      `lastsave` uit de payload [gemeten bij status 2 en 6, niet gedocumenteerd]. De sessie bewaart
      het hoogste merk dat is toegepast (`applied_mark`). Een callback met een merk dat niet hoger
      is, krijgt `{"error": 0}` en schrijft niets. Is `lastsave` niet steeds hoger (spike stap 13),
      dan telt de adapter het aantal in `history.changes`.
    - **Een callback op een dichte sessie:**

      | Waarom dicht | status 1 | status 2, 3 of 6 | status 4 |
      |---|---|---|---|
      | eigen eindopslag (`saved`, `no_changes`, `copy`, `refused`) | `drop` van die gebruikers: hun editor laadt opnieuw met de key van nu | `{"error": 0}`, niets schrijven: een late herhaling | `{"error": 0}` |
      | `lost` (de app dacht dat DS de key kwijt was) | idem `drop` | **een late opslag** (Keuze 16, met het merk): staat het bestand nog op wat deze sessie het laatst zag, dan op het origineel; anders een herstelkopie | `{"error": 0}` |
      | `gone` (bestand of eigenaar definitief weg) | idem `drop` | `{"error": 0}`, niets schrijven | `{"error": 0}` |

    - Een onbekende key (geen rij): status 1 en 4 → `{"error": 0}`; status 2, 3 en 6 →
      `{"error": 1}`.
    - `notmodified` gebruikt de app niet. Hij haalt altijd op en laat de sha beslissen.
    - Waarom status 3 als kopie: wat DS dan stuurt, kan kapot zijn [niet getest]. Een kopie houdt
      het origineel heel en laat niets weg.
16. **Opslaan volgt het recept van `replace_content`, met twee kleine uitbreidingen van
    `place_file`.** Per callback met status 2, 3 of 6:
    1. Het JWT controleren en de sessie zoeken op key. Het merk en de regels voor een dichte sessie
       van Keuze 15 eerst.
    2. Het slot van dit bestand nemen (`asyncio.Lock` per node-id). De volgorde is altijd: dit
       slot, dan `user_lock(owner)`, dan `blobs.lock`.
    3. Bij status 2 of 3: de sessie op `saving` en committen (`updated_at` = nu). Wie nu de editor
       opent, wacht op het slot; na 15 s geeft dat 409 `office_busy`.
    4. **Meteen downloaden** naar `blobs.root/.tmp`. De link leeft ±900 s en sterft bij een herstart
       van DS zonder vast geheim [gemeten]. **De grens** is de kleinste van
       `office_max_save_bytes` (standaard 512 MB) en wat de eigenaar nog mag opslaan
       (`room_left`). Wat DS echt maakt, is veel kleiner: invoer tot 100 MB, afbeeldingen tot
       25 MB, wijzigingen tot 150 MB [image]. Boven de grens stopt het downloaden en wordt er
       niets geschreven (`too_large` of `no_room`).
    5. Het bestand vers laden. Naam en map kunnen veranderd zijn. **Definitief weg** (ook de
       eigenaar): `{"error": 0}`, niets schrijven, sessie dicht met `gone`. De eigenaar wilde het
       weg (Keuze 19).
    6. Wie slaat op: de eerste uit [de laatste bewerker uit `history.changes[-1].user.id`, de
       `userid` uit `actions`, de rest van `members`] die niet in `banned` staat, **nu** nog
       schrijfrecht heeft (eigenaar die geen gast is, of een deling met `write`) en niet
       geblokkeerd is.
    7. Er is iemand: `place_file(..., "replace", ..., actor=<die gebruiker>, guard=<guard>,
       only=node.id, exact=True, squash=<zie Keuze 17>)`. De guard is `writable(...)` als de actor
       niet de eigenaar is. Het quotum is dat van de eigenaar, zoals altijd bij een gedeelde map
       (B18).
    8. Er is niemand meer met schrijfrecht, maar bestand en eigenaar bestaan: **een herstelkopie**
       `<naam> (hersteld <datum tijd>).<ext>` in de hoofdmap van de eigenaar, met de eigenaar als
       actor. Nooit bij iemand die zijn recht kwijt is. Gebeurtenis `office.recovered` alleen naar
       wie de kopie nu kan zien (§3.4).
    9. Gelukt: `applied_mark`, `checkpoint`, `saved_at` en `versions_made` bijwerken en
       committen, dan pas `{"error": 0}`.
    - **Tijdelijke fout** (database bezet, DS-link gaf 5xx, schijf): **HTTP 503**. DS probeert het
      dan opnieuw [gemeten: 3 keer na 1, 2 en 4 s]. Wij zetten dat op 8 keer (1 + 2 + … + 128 s =
      ±4¼ minuut, Keuze 29), zodat ook een herstart van de app past. Blijft de sessie daarna in
      `saving` hangen, dan haalt de ronde hem eruit (Keuze 20).
    - **Blijvende fout** (ook de kopie lukt niet: quotum vol, te groot): **`{"error": 1}`**. DS
      probeert het dan niet opnieuw en bewaart het document als "forgotten file" [gemeten]. Keuze
      21 pakt het daar op.
    - Een blijvende fout bij status 6 maakt geen kopie (anders komt er elke 5 minuten een). Het
      antwoord is `{"error": 1}`, en wie nog schrijfrecht heeft, krijgt `office.unsaved` met
      `final: false`.
    - **Idempotent:** een herhaalde callback valt af op het merk. Komt hij toch door, dan geeft
      dezelfde inhoud dezelfde sha, en `place_file` maakt bij dezelfde sha geen nieuwe versie.
    - Nieuwe opties van `place_file` (optioneel, de rest blijft gelijk):
      - `exact=True` (alleen met `only`): zoek het bestand op id, niet op naam en map. Hernoemd,
        verplaatst of in de prullenbak maakt dan niet uit. Weg geeft 404. Dat is eenvoudiger en
        juister dan "409 MOVED en één keer opnieuw".
      - `squash=<sha>`: is de huidige inhoud deze sha, dan wordt die geen versie (de verwijzing
        vervalt gewoon). Anders gaat alles zoals nu.
17. **Versies: één per sessie, plus één per 30 minuten in een lange sessie, hooguit 10 per sessie**
    (Vraag 4).
    - De eerste opslag in een sessie (een tussenstand of de laatste) maakt van de inhoud van vóór de
      sessie een versie.
    - Elke volgende opslag in dezelfde sessie vervangt de tussenstand zonder nieuwe versie
      (`squash=<checkpoint>`). Behalve als de laatste versie uit deze sessie ouder is dan
      `version_minutes` (standaard 30): dan wordt de tussenstand wel een versie.
    - **Hooguit 10 versies per sessie** (`versions_made`). Daarna wordt elke tussenstand
      vervangen. Zo blijft de inhoud van vóór de sessie staan: `MAX_VERSIONS` is 20, en deze sessie
      maakt er hooguit 10. Een sessie kan dagen open blijven (`sessionabsolute` 30 dagen, M14).
    - `checkpoint` komt alleen uit een opslag van deze sessie. Na status 2 is de sessie dicht, en
      een late status 6 schrijft niets meer (Keuze 15). Een late opslag van een `lost` sessie
      gebruikt nooit `squash`: wat er nu staat, blijft als versie.
    - Heeft iemand intussen een nieuwe versie geüpload of teruggezet, dan blijft die als versie
      staan. De sha past dan niet bij `squash`.
    - Waarom: met `MAX_VERSIONS = 20` duwen tussenstanden (elke 5 minuten, of bij elke klik op
      Opslaan) anders binnen twee uur de inhoud van vóór het bewerken eruit. En een middag bewerken
      krijgt zo toch een paar ijkpunten.
18. **Tussentijds opslaan doet de app zelf, met `forcesave`.** De key blijft gelijk.
    - **Klok van de app:** bij elke ronde (Keuze 20) kijkt de app naar open sessies met iemand erin.
      Is de laatste opslag ouder dan `checkpoint_minutes` (standaard 5), dan stuurt hij `forcesave` met
      `userdata: "timer"`. `error 4` betekent "geen wijzigingen" en is goed [gemeten]. Een kill of
      OOM verliest zo hooguit 5 minuten.
    - **Knop Opslaan in de editor** (`customization.forcesave: true`): status 6 met
      `forcesavetype 1` [doc, de spike controleert het].
    - **Knop "Nu opslaan"** in de kopbalk van onze editorpagina: `POST
      /api/v1/office/files/{id}/save`. Bereikbaar met het toetsenbord, buiten de iframe.
    - **Vóór een update** (`install.sh` → `app.cli office-checkpoint`), **bij het stoppen van de app**
      (wacht hooguit 5 s) en met een knop op `/beheer`.
    - Waarom zelf en niet `autoAssembly` van DS: dezelfde regel werkt voor elke editor, hij is te
      testen met de nep-DS, en hij hangt niet aan een configsleutel van DS.
19. **Wat er gebeurt als het bestand verandert terwijl het open is.** De editor blijft open. We
    blokkeren niets in andere code en zetten geen haken in andere routers. De toegang kijkt bij
    elk verzoek naar DS (Keuze 7). Opslaan zoekt op id (Keuze 16) en controleert het recht opnieuw.

    | Gebeurtenis | Gevolg |
    |---|---|
    | Hernoemen of verplaatsen | opgeslagen in het hernoemde of verplaatste bestand (`exact`). De titel in de editor blijft de oude tot opnieuw openen. |
    | De eigenaar zet het in de prullenbak | doet de eigenaar mee: opgeslagen in het bestand in de prullenbak (`exact`), dus terugzetten brengt het werk terug. Deelnemers via een deling verliezen hun toegang (zie "deling ingetrokken"). Nieuwe sessies op een bestand in de prullenbak: 422 `trashed`. |
    | Definitief verwijderd (door de eigenaar of de bewaartermijn) | de ronde stuurt `drop` voor iedereen en sluit de sessie (`gone`). Een callback daarna schrijft niets (`{"error": 0}`). **Geen herstelkopie:** de eigenaar wilde het weg. Forgotten files van die key gaan weg. |
    | Nieuwe inhoud via upload of "versie terugzetten" | de editor wint: zijn inhoud wordt de nieuwste, de andere inhoud blijft als versie. Wie de editor intussen opent, doet mee met de lopende sessie en ziet "Dit bestand is buiten de editor veranderd" (`stale: true`). |
    | Deling ingetrokken, gebruiker geblokkeerd of uitgelogd | bij zijn volgende verzoek naar DS weigert de app (hooguit 15 s cache). Binnen 30 s zet de ronde hem uit de editor (`drop`), sluit zijn tunnels op die key en zet hem op `banned`. Opnieuw verbinden lukt niet: elk pad naar DS controleert het recht. Is hij bij de volgende ronde toch nog verbonden, dan stopt de hele sessie (Keuze 20). Wat hij deed, slaat op als iemand anders nog schrijfrecht heeft; anders een herstelkopie bij de eigenaar. Hij krijgt alleen `office.dropped`, zonder naam. Uitloggen: binnen 30 s uit de tunnel. |
    | Quotum van de eigenaar vol | tussenstand: `{"error": 1}` en een melding in de editorpagina. Laatste opslag: ook de kopie lukt niet → forgotten file → Keuze 21. |
    | Eigenaar verwijderd | zijn bestanden gaan mee weg. De ronde stuurt `drop` voor iedereen en sluit de sessie (`gone`). De forgotten files van zijn keys gaan meteen weg. Geen kopie. |

20. **De ronde: een eigen taak, elke `office_sweep_seconds` (standaard 30 s)**
    (`app/office/sweep.py`). Hij start in het office-deel van de lifespan. Niet via housekeeping
    van `jobs`: die tikt maar eens per 60 s, en alles daarin draait na elkaar (M12).
    - `pending` ouder dan 5 minuten zonder verbinding → dicht (`lost`);
    - per gebruiker in `members` of `connected`: actief en nog schrijfrecht? Nee → uit `members`,
      in `banned`, de cache van Keuze 7 voor hem leeg, zijn tunnels op die key dicht, `drop` met die
      gebruiker [gemeten: drop werkt en DS slaat dan op], en `office.dropped` naar hem;
    - staat zo iemand bij de volgende ronde nog in `info` of `connected`, dan **stopt de sessie**:
      `forcesave`, dan `drop` voor iedereen. DS slaat op (status 2), de sessie sluit gewoon via
      Keuze 16, en wie verder wil, opent opnieuw met een nieuwe key. Dit is ook de terugval als het
      lezen van het eerste socketpakket (Keuze 6) niet lukt;
    - bestand of eigenaar definitief weg → `drop` voor iedereen, dicht met `gone`;
    - elke 2 minuten `info` per open key, en per `pending` ouder dan 1 minuut. `error 1` (DS kent
      de key niet meer, bijvoorbeeld na een herstart van DS) → dicht met `lost`, zodat de volgende
      opener een nieuwe key krijgt;
    - `saving` ouder dan 6 minuten (de 8 pogingen van DS plus marge) → `info`. `error 1`: DS gaf het
      op en bewaarde het als forgotten file → dicht met `lost`; het herstel (Keuze 21) slaat het
      dan gewoon op het origineel op. `error 0`: terug naar `open`;
    - tussentijds opslaan (Keuze 18);
    - gesloten rijen ouder dan 30 dagen weg.

    Elke stap met een eigen korte transactie. Nooit een slot vasthouden tijdens een aanroep naar DS.
21. **Forgotten files herstellen we vanzelf.**
    - Wanneer ontstaat er een: de app antwoordde `{"error": 1}`, of de app was onbereikbaar na alle
      pogingen. Het tweede is de normale oorzaak: bij een update of een herstart van de LXC breken
      de sockets, en DS stuurt status 2 terwijl de app weg is.
    - Een eigen taak (`app/office/recovery.py`, O2): bij de start van de app, elke
      `office_recovery_seconds` (standaard 600) en zodra DS weer gezond is. `getForgottenList`, en
      per key:
      1. `getForgotten` en ophalen (Keuze 13, met de grens van Keuze 16). Dezelfde sha als het
         bestand nu → alleen `deleteForgotten`.
      2. Bestand of eigenaar definitief weg → alleen `deleteForgotten`. Niets schrijven.
      3. De sessie van deze key is de laatste van het bestand (ook als hij nog `saving` of `lost`
         is), en de inhoud van het bestand is nog wat deze sessie het laatst zag → gewoon opslaan
         op het origineel, volgens Keuze 16 en 17.
      4. Anders → een herstelkopie bij de eigenaar (Keuze 16, stap 8).
      5. Gelukt → `deleteForgotten`, gebeurtenis `office.recovered`.
      6. Lukt ook de kopie niet (quotum vol, te groot) → hij blijft staan. `/beheer` → Office →
         "Niet opgeslagen" toont hem met de naam en eigenaar uit `office_session`, met de knoppen
         **Terugzetten** (zelfde stap 4, als er nu ruimte is) en **Verwijderen**. Melding
         `office_unsaved`.
      7. Geen rij voor deze key (een andere database) → alleen op `/beheer`, als "onbekend".
    - Ze staan in het volume `office-lib`, niet in `/tank/platform`, dus niet in de ZFS-snapshot.
      Het herstel zet ze binnen enkele minuten in de app; dan zitten ze er wel in.

### Rechten en formaten

22. **Wie mag wat.**

    | Wie | Editor | Voorbeeld |
    |---|---|---|
    | Eigenaar (geen gast) | bewerken | PDF-pagina's |
    | Deling met `write` (gebruiker of gast) | bewerken; slaat op als bestand van de eigenaar, in diens quotum | PDF-pagina's |
    | Deling met `read` | **geen editor** | PDF-pagina's, downloaden, PDF-export |
    | Publieke link (ook met uploadrecht) | **geen editor, ook niet alleen-lezen** | PDF-pagina's via de bestaande publieke paginaroutes, als de PDF er al is; anders B21 |
    | Album | n.v.t. (alleen foto's en video's) | — |
    | Eerdere versie | niet te bewerken (eerst terugzetten) | PDF-pagina's |
    | Zonder 2FA (2FA is verplicht) | nee: alles vraagt `CurrentUser` | — |

    - Er is dus **geen alleen-lezen-modus van DS**. Wie niet mag bewerken, ziet het omgezette
      PDF-voorbeeld. Zo draait alleen wie nu mag bewerken ooit code van DS (Keuze 7 en 9).
    - Een publieke link start nooit een conversie. Alleen een ingelogde gebruiker met leesrecht kan
      dat vragen (Keuze 25).
23. **Bewerken alleen voor docx, xlsx en pptx, tot 100 MB, en alleen na de invoercontrole.**
    - 100 MB is de grens waarop DS een bestand nog downloadt (`maxDownloadBytes` = 104857600)
      [image]. Groter: `office_too_large`.
    - odt, ods, odp, doc, xls, ppt, rtf en de macroformaten (docm, xlsm, pptm) krijgen alleen het
      PDF-voorbeeld. DS kan doc en ppt niet schrijven [gemeten]. ODF of macro's terugschrijven is een
      risico op verlies van opmaak dat we nu niet nemen. Omzetten naar docx hoort bij fase 10.
    - **De invoercontrole** (`app/office/inspect.py`, O0). Vóór elke sessie en vóór elke conversie
      kijkt de app zelf of het bestand veilig genoeg is voor DS. In een thread, met alleen
      `zipfile` en `zlib` uit de standaardbibliotheek:
      - de inhoud beslist, niet de naam: DS kijkt zelf naar de bytes en negeert de opgegeven soort
        [gemeten: een zip met de naam .doc wordt als docx gelezen]. Een zip-formaat moet met
        `PK\x03\x04` beginnen, doc, xls en ppt met de kop van OLE, rtf met `{\rtf`; anders
        `format`;
      - zip-formaten (OOXML en ODF): elk onderdeel echt uitpakken en de bytes tellen
        (`zlib.decompressobj`). De opgegeven grootte geloven we niet. Stoppen bij de grens;
      - de XML samen hooguit 4 MB voor docx, pptx en hun macro- en sjabloonvormen, 2 MB voor elk
        ODF-document (odt, ods, odp) en 10 MB voor een echt OOXML-werkblad. Gemeten bij 2 GB
        (spike A11, "stap 18 verder"): x2t liep vast op een docx met 10 MB XML, een odt met 7 MB en
        een xlsx met 30 MB. DS zelf staat 50 MB toe (M14), en voor ODF niets. XML is elk onderdeel
        op `.xml` of `.rels`, en elk ander onderdeel dat (na een BOM en witruimte) met `<` begint:
        in OOXML mag het hoofddeel elke naam hebben. De ruimere grens krijgt alleen een bestand
        waarvan `[Content_Types].xml` een werkblad als hoofddeel noemt en geen tekst of presentatie;
        verder geldt de kleinste van wat de naam en wat de inhoud zegt;
      - alles samen hooguit 512 MB en hooguit 10.000 onderdelen. Boven 64 MB uitgepakt
        (`CONTENT_BYTES` van B21) mag het hele bestand hooguit 100 keer kleiner zijn dan uitgepakt;
      - alleen de methoden `stored` en `deflate`, niets versleuteld. Een ingebedde zip
        (`embeddings/`, `Object …/`) telt één niveau diep mee;
      - hooguit 20 s;
      - doc, xls, ppt en rtf (geen zip): hooguit 20 MB.
      - De grootte van de XML is maar een maat: een docx met 3,9 MB aan alleen pagina-einden kwam
        binnen de grens, maar op ±97 % van 2 GB. Daarvoor zijn de geheugengrens van de container
        (x2t gaat dood, docservice blijft) en de twee pogingen van een conversie (Keuze 25).
    - Weigert de controle, dan geeft bewerken 422 `office_unsafe` en wordt conversie
      `pdf_state = unsafe`. B21 blijft werken: die lezer heeft zijn eigen grenzen, in een
      afgeschermd proces.
    - De uitslag staat per sha een uur in het geheugen. Het kost bij 64 MB XML ±0,5 s cpu, in een
      thread, dus nooit op de event loop.
    - Ook DS zelf krijgt grenzen voor ODF: `inputLimits` voor odt, ods en odp in
      `local-production-linux.json`. Een lijst vervangt daar de hele lijst, dus alle regels van
      `default.json` staan erin, plus de nieuwe [niet getest, spike stap 18].
24. **Nieuwe documenten uit eigen lege bestanden** (`app/office/blank.py`, alleen `zipfile`).
    - docx: `[Content_Types].xml`, `_rels/.rels`, `word/document.xml` (één lege alinea, `sectPr`
      A4: 11906×16838, marges 1440), `word/styles.xml` (`docDefaults` met `w:lang`).
    - xlsx: `xl/workbook.xml` (blad "Blad1" of "Sheet1"), `xl/_rels/workbook.xml.rels`,
      `xl/worksheets/sheet1.xml` (lege `sheetData`), `xl/styles.xml` (minimaal).
    - pptx: `ppt/presentation.xml` (16:9: 12192000×6858000), één `slideMaster`, één `slideLayout`
      (titel), `ppt/slides/slide1.xml`, `ppt/theme/theme1.xml` (12 kleuren, twee lettertypes, het
      minimum dat de spec vraagt).
    - Deterministisch: `ZipInfo.date_time = (1980, 1, 1, 0, 0, 0)` en een vaste volgorde. Dus vaste
      bytes en één blob voor alle lege documenten van een soort en taal.
    - Niet de sjablonen uit het image: die zijn deel van DS (AGPL) en binden ons aan één editor.
      Een eigen docx van 1 KB werkte in de proef [gemeten]. xlsx en pptx nog niet [niet getest]:
      de spike en de test met de echte DS openen ze alle drie.
    - `POST /api/v1/office/new` maakt het bestand met `place_file(..., conflict="rename")`. Namen
      in de taal van de gebruiker (`user.locale`): "Nieuw document.docx", "Nieuw werkblad.xlsx",
      "Nieuwe presentatie.pptx". Daarna opent de web-app meteen de editor.

### Voorbeelden en PDF

25. **Previews via de conversie-API, met de eigen lezer (B21) als terugval.**
    - Eerst draait de preview zoals nu (B21: `doc.json` en de ingebedde thumbnail). Daarna roept
      `previews._store` de haak `convert.on_preview_done` aan. Die beslist, als office aan staat:
      - een blob die via een publieke link binnenkwam: `pdf_state = ask`. `place_file` zet dat bij
        een nieuwe blob zonder `actor` (O0). Zo'n blob gaat nooit vanzelf naar DS;
      - groter dan `office_auto_convert_bytes` (standaard 20 MB): ook `ask`;
      - anders een taak `office.pdf` (key `office.pdf:<sha>`).
    - **Op verzoek:** in de viewer de knop "Voorbeeld met opmaak maken" bij `ask`, en "Downloaden als
      PDF" (Keuze 26). Alleen ingelogd, met leesrecht, nooit via een publieke link: `POST
      /api/v1/office/files/{id}/convert`. Per gebruiker hooguit 10 per 10 minuten (429
      `rate_limited`). De taak krijgt een `run_after` in het verleden, en staat dus vooraan.
      Grenzen: 100 MB voor zip-formaten, 20 MB voor doc, xls, ppt en rtf.
    - Soorten: docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt, rtf en docm, xlsm, pptm. `mime.py`
      herkent doc, xls en ppt al (`OLE_BY_EXT`) en rtf ook.
    - Conversie-key bij DS: `p<PDF_VERSION>-<sha>`. Die key is een cache bij DS: dezelfde key geeft
      hetzelfde antwoord, ook voor een ander bestand [gemeten]. Met de sha erin kan dat niet
      misgaan. Een nieuwe key (`-a<n>`) alleen als DS zelf -2 of -4 meldde, want dat antwoord
      bewaart hij onder de key. Na onze eigen tijdslimiet vragen we later dezelfde key opnieuw:
      DS werkt gewoon door.
    - **Vóór het versturen**, in deze volgorde:
      1. de invoercontrole (Keuze 23). Weigert hij: `pdf_state = unsafe`, klaar;
      2. DS gezond? Nee: `pdf_state` blijft leeg en de taak stopt. De inhaalronde zet hem terug
         zodra DS gezond is. Dat telt niet als poging, en draait ook niet elke minuut opnieuw;
      3. loopt er een sessie die opent of opslaat (`pending` of `saving`)? Dan de taak over 5 s
         opnieuw in de rij (`jobs.chain`), zonder poging. DS heeft één plek voor conversies
         (`maxprocesscount: 1`, M14), en openen en opslaan gebruiken die waarschijnlijk ook
         [niet getest, spike stap 16];
      4. loopt er al een conversie (één slot in de module)? Dan ook over 5 s opnieuw. Een worker
         wacht nooit op een slot, dus previews van foto's en video's lopen gewoon door.
    - **Na het versturen** telt alles als poging: `async: true` en elke seconde opnieuw vragen, tot
      120 s. Onze tijdslimiet, een DS die ongezond wordt, opnieuw start of niet meer antwoordt: de
      taak faalt. `office.pdf` heeft `max_attempts = 2`. Daarna zet `on_dead` `pdf_state = failed`
      en komt de melding `office_convert_failed` op `/beheer`. Een bestand dat DS laat vastlopen,
      krijgt zo hooguit twee kansen, en komt nooit vanzelf terug.
    - Klein: ±1 s. 401 pagina's: 22 s en 641 MB [gemeten].
    - DS haalt de bron op via `/internal/office/src/<grant>` (een grant van 10 minuten in het
      geheugen, alleen voor deze taak). Het resultaat (`fileUrl`, ±900 s geldig) halen we meteen op.
    - **De PDF staat in `data_dir/office/pdf/<sha[:2]>/<sha>.pdf`**, niet in `previews/<sha>/` (M10).
      Eerst naar `data_dir/office/.tmp`, controle op `%PDF-` en de grens `office_max_pdf_bytes`,
      dan onder `blobs.lock` op zijn plek, alleen als de blob er nog is (zoals `viewer._place`).
    - De staat staat in twee nieuwe kolommen: `blob.pdf_state` (`NULL | pending | ready | failed |
      none | unsafe | ask`) en `blob.pdf_version`. Niet in `preview_meta`, want die wordt bij een
      nieuwe preview opnieuw gezet (M10).
    - Fouten van DS: -4 (download) en -2 (tijd) → later opnieuw met `-a<n>`, hooguit drie keer;
      -3, -7, -9 en -10 → `failed`; -5 (wachtwoord) → `none`; -1 en -6 → een gewone poging;
      -8 → melding `office_misconfigured`. `failed` komt alleen terug met een nieuwe `PDF_VERSION`
      of met de knop op `/beheer`.
    - **Thumbnails.** Daarna maakt het bestaande renderproces de thumbnails (256 en 1024) uit
      pagina 1 van die PDF (de pdf-tak, zonder netwerk). Ze vervangen de webp's in
      `previews/<sha>/`; `doc.json` blijft. **Het adres verandert mee:** `previews.url` krijgt een
      deel `v=<pdf_version>` (0 zonder PDF), mee getekend (O0). Anders toont de browser nog 24 uur
      de oude thumbnail van B21 (`immutable`). Komt er later een nieuwe preview (`RENDER_VERSION`),
      dan zet de haak de taak weer klaar. Die ziet dat de PDF er al is en maakt alleen de
      thumbnails opnieuw.
    - **De viewer** toont echte pagina's: `GET /files/{id}/document` geeft `kind: "pdf"` met `source:
      "office"`, en de paginaroute rendert de omgezette PDF. Dat geldt ook voor de publieke link.
      Is er (nog) geen PDF, dan blijft `OfficeView` met `doc.json` (B21). Komt er een nieuwe PDF
      (onder `blobs.lock`), dan wordt `previews/<sha>/pages/` geleegd. De ETag van een pagina
      krijgt de `pdf_version` erbij (`"<sha>-<n>-<breedte>-p<v>"`), en `info.json` noteert hem.
      Een andere versie telt de pagina's dus opnieuw.
    - `previews.discard` haalt ook de PDF weg (één regel), dus `collect_garbage` ruimt alles op.
    - **De inhaalronde** `office.pdf.backfill` zet ontbrekende conversies klaar: in stukken van 200
      blobs, elk stuk met een `run_after` van 60 s later (zoals `previews.RERENDER_DELAY`). Zo gaan
      nieuwe uploads altijd voor. Hij neemt alleen `NULL`, en bij een hogere `PDF_VERSION` ook
      `ready` en `failed`. Nooit `ask`, `unsafe` of `none`. Hij start bij de start van de app en
      zodra DS weer gezond is.
26. **PDF-export = de omgezette PDF downloaden.** Actie "Downloaden als PDF" (lijst, viewer), met
    leesrecht: `POST /api/v1/office/files/{id}/pdf` geeft een downloadlink. Is de PDF er nog niet,
    dan vraagt de app de conversie op verzoek aan (Keuze 25, met dezelfde grens per gebruiker) en
    wacht hooguit 60 s; anders 409 `pdf_pending`. "Opslaan als PDF" in de map en "omzetten naar
    docx" horen bij fase 10.

### Bewaken en uitval

27. **De verbindingen tellen we zelf, in de tunnel.**
    - Elke editor-tab heeft één WebSocket [gemeten]. Dat is precies wat DS vroeger als "verbinding"
      telde. De tunnel telt ze per key en per gebruiker.
    - **Een eigen grens `max_editors`, standaard 20** (op `/beheer`, 1–500). Vol: de sessie-aanroep
      geeft 429 `office_too_many`; het PDF-voorbeeld werkt gewoon. De tunnel houdt de grens ook per
      key aan, en per gebruiker `office_max_tunnels_user` (Keuze 6). 24 editors op een klein
      document gingen goed [gemeten]; zwaar werk op 4 cores niet. De meting bij de integratie (§4.7)
      stelt de standaard bij.
    - `license` geeft de limiet van DS zelf (elke 10 minuten, met `version`). Is die eindig (een
      ouder image), dan telt de kleinste van de twee, met een melding vanaf 80 %.
    - `/info/info.json` en StatsD gebruiken we niet. Het eerste mag alleen van 127.0.0.1, het tweede
      is een extra dienst [image].
28. **Als DS er niet is of niet werkt.** De gezondheid houdt één taak bij (`app/office/health.py`,
    O0). Die schrijft `OfficeState`; de rest leest alleen.

    | Toestand | Wat er gebeurt |
    |---|---|
    | Niet ingesteld (`PLATFORM_OFFICE_URL` leeg) | geen interne luisteraar, `/office/` geeft 404, geen knoppen Bewerken en Nieuw document, previews alleen via B21. Precies fase 3. |
    | Ingesteld, niet gezond | elke 30 s `GET /healthcheck` (moet `true` zijn). Openen geeft 503 `office_unavailable`; het PDF-voorbeeld en B21 blijven; conversies wachten. Na 2 minuten de melding `office_down`; de eerste 5 minuten na de start van de app (`starting`, DS bouwt fonts) tellen daar niet voor. Nieuw document werkt (het is een bestand). |
    | DS eist geen tokens (verkeerd image, verkeerde instelling) | bij elke overgang naar gezond en daarna elke 10 minuten stuurt de app `{"c": "version"}` en een conversie, allebei **zonder** token. Verwacht: `error 6` en `-8` [gemeten]. Iets anders: `state = misconfigured`, melding `office_misconfigured`, bewerken en conversie uit. Zonder deze controle zou elke opslag stil een forgotten file worden, want de app weigert ongetekende callbacks. |
    | Ander geheim dan DS (`PLATFORM_OFFICE_SECRET` is niet de `JWT_SECRET` van DS, bijvoorbeeld een typfout in `.env` of een DS zonder vast geheim) | `/healthcheck` is `true`, maar elk getekend commando geeft `error 6` [gemeten]. Dat is een fout in de instellingen, geen uitval: `state = misconfigured` met de reden in `problem`, melding `office_misconfigured`, nooit `office_down`. De grote controle draait dan elke ronde opnieuw (één `version`), zodat een goed geheim meteen telt. |
    | Valt weg tijdens het bewerken | de editor toont waarschuwing -18 [gemeten]. Onze pagina zegt "De verbinding met de editor is weg. Wat het laatst is opgeslagen, staat in het bestand." met "Opnieuw laden". Een nette stop slaat alles op (status 2 binnen 1 s) [gemeten]. |
    | Weer gezond | sessies die DS niet meer kent, sluit de ronde (`lost`); conversies gaan verder (de inhaalronde); het herstel van Keuze 21 draait meteen. |
    | Herstart van de app tijdens het bewerken | de sockets breken; DS houdt de sessie in het geheugen. De editor probeert 50 keer met 2 s ertussen opnieuw te verbinden, dus ±100 s (M14). Lukt dat via de nieuwe tunnel en is er intussen opgeslagen, dan komt `onRequestRefreshFile` (M17). Onze pagina vraagt dan een nieuwe sessie en roept `refreshFile` aan. Om niets te verliezen: forcesave bij het stoppen (5 s), 8 pogingen van DS (±4 minuten), en daarna Keuze 21. |

### Uitrol en bijwerken

29. **Compose: een extra bestand, geen profiel.**
    - `infra/single/compose.office.yaml` voegt de dienst `office` en het netwerk `office` toe en
      geeft `platform` de office-variabelen. `install.sh` neemt het bestand alleen mee als office
      aan staat. Zet je office uit, dan is de container een "orphan" en ruimt `--remove-orphans` hem
      op. Met een profiel blijft een draaiende container juist staan [gemeten, M9].
    - `compose.office-limits.yaml` (`mem_limit: 2g`, `memswap_limit: 2g`, `cpus: 2`) alleen als
      `docker info` zegt dat de kernel dat kan. Anders start de container niet in een LXC zonder die
      cgroup-controllers [niet getest in de echte LXC]. Met de grens raakt een geheugentekort DS en
      niet de app. In de proef ging 1 GB nog goed, met een piek van 894 MB [gemeten]. `cpus: 2`
      houdt 2 cores voor de app vrij terwijl DS na elke start ±5 minuten fonts maakt.
    - Hardening die werkte [gemeten]: `cap_drop: ALL` plus `CHOWN, SETUID, SETGID, DAC_OVERRIDE,
      FOWNER, NET_BIND_SERVICE, KILL`, `no-new-privileges`, `pids_limit: 512`. Geen privileged, geen
      AppArmor-aanpassing, geen extra rechten op de host.
    - `depends_on: [platform]` voor DS. Bij `docker compose stop` of `down` stopt DS dan eerst, en
      vindt zijn laatste opslag nog een draaiende app.
    - `stop_grace_period: 60s` voor DS (een stop duurde 30 s [gemeten]) en `30s` voor de app
      (forcesave bij het stoppen).
    - Vier benoemde volumes. Anders maakt Docker bij elke nieuwe container anonieme volumes
      [gemeten].
    - Het image staat in compose onder de naam van Docker Hub, op digest. Komt het van de mirror,
      dan zet `install.sh` die naam er lokaal op (`docker tag`, M19). Zo vindt elke `docker compose
      up` het image, ook zonder `OFFICE_IMAGE` in de shell, en haalt hij niets van Docker Hub.
    - Instellingen zonder omgevingsvariabele via een alleen-lezen `local-production-linux.json`:
      `callbackBackoffOptions.retries: 8`, `docbuilderEnable: false`, `aiSettings.enable: false`,
      en `inputLimits` met ODF erbij (Keuze 23). Dat bestand laat het startscript van DS met rust
      [gemeten met `docbuilderEnable`]. Of de andere sleutels zo werken, controleert de spike.
30. **install.sh blijft één commando, en kan het platform niet breken.**
    - **Eerst** leest het script `OFFICE` uit de omgeving, vóór het `.env` leest (M13). Alleen
      `aan` of `uit`; iets anders stopt met uitleg. Zo werkt `OFFICE=uit bash install.sh`, en ook
      `curl … | OFFICE=uit bash`.
    - Ontbrekende sleutels worden **toegevoegd**, nooit overschreven: `OFFICE=aan`,
      `OFFICE_JWT_SECRET`, `OFFICE_LINK_SECRET`. Een gevraagde `OFFICE` vervangt daarna de waarde.
    - Vóór het ophalen van het image: de opslagdriver is `overlay2` of `overlayfs` (M8; bij `vfs`
      wordt het image vele malen groter [gemeten: 3,1 GB uitgepakt]), en er is ≥ 8 GB vrij voor
      Docker (oud en nieuw image even naast elkaar). Niet goed → office blijft zoals het was, met
      uitleg. Nooit een aanpassing op de host voorstellen die de beveiliging verzwakt.
    - **Het image:**
      - staat er een `OFFICE_IMAGE` in `.env`, dan is dat een **pin**. Het script gebruikt hem
        zoals hij is, haalt geen nieuw image en gooit hem nooit weg. Zo werkt terugrollen;
      - anders op digest: eerst Docker Hub, dan `mirror.gcr.io` (zelfde digest; Docker Hub gaf soms
        429 [gemeten]). Na een pull van de mirror `docker tag` naar de naam van Docker Hub;
      - lukt geen van beide: draait er al een DS, dan blijft die. Zo niet, dan start het platform
        zonder office en zegt het script dat. Dat staat in een `if`, nooit via `set -e` (M13).
    - Vóór het vervangen van de app: `app.cli office-checkpoint` in de draaiende app. Een oudere app
      kent dat commando niet; dan gaat het script gewoon door.
    - `systemctl restart docker` alleen als het script `daemon.json` net heeft geschreven (en
      systemd draait). Nu herstart het script dockerd bij elke run, en dat stopt DS vóór het
      tussentijds opslaan als `live-restore` ontbreekt (F3).
    - Eerst de app vervangen en wachten tot `/health` antwoordt. Pas dan DS bijwerken (alleen als
      zijn image of instellingen veranderden). Op DS wacht het script niet: die is in 5–15 s gezond
      [gemeten], en de app meldt het zelf.
    - Oude images van DS weghalen die geen container meer gebruikt, behalve het vorige (voor
      terugrollen) en de pin.
    - Voor de test (§2 O6): `PLATFORM_REPO` (ook een lokaal pad) en `PLATFORM_SRC_REF` (een commit
      of tag; dan geen `fetch`). Voor Jonas verandert er niets: zonder die variabelen is het
      GitHub en de branch, zoals nu.
    - Het script eindigt met `[ "${INSTALL_LIB:-}" = 1 ] || main "$@"`, zodat een test de functies
      kan laden zonder iets te installeren.
31. **CI.**
    - **office (nieuw, vanaf O0):** de echte DS 10.0.0.1, als harde poort (§4.5). Elk pakket
      voegt zijn eigen `*_real.py` toe; zijn PR moet door deze job.
    - **infra:** `docker compose config -q` voor de combinaties (zonder office, met office, met
      office en grenzen). De images komen uit `docker compose … config --images`; het DS-image
      wordt nagekeken met pogingen en `mirror.gcr.io` als terugval. `test_install_env.sh`.
    - **e2e:** draait ook `-m office` (met de nep-DS), in elke run.
    - **images:** de curl-controles krijgen `/office/` (404 zonder DS) en `/internal/` (404 op de
      publieke poort).

### Web

32. **De editor is een eigen pagina: `/bewerken/<id>`.**
    - Een lazy chunk `office/EditorPage.tsx`. Herladen werkt, Terug sluit de editor, en de link kun
      je aan een mede-bewerker sturen.
    - Openen: "Bewerken" in het contextmenu, de knop Bewerken in de viewer, en de sneltoets **E**
      (vrij in de scopes `browser` en `viewer`).
    - "Nieuw" in de werkbalk wordt een menuknop (Map, Document, Werkblad, Presentatie). **O** opent
      dat menu (vrij). N blijft "nieuwe map". Nooit Ctrl+N. Ook in het contextmenu van de map.
    - Toetsen in de iframe gaan naar DS. Daarom een kopbalk boven de iframe:
      - `<h1>` met de naam;
      - de knop **Sluiten**, eerste in de tabvolgorde;
      - de knop **Nu opslaan** (Keuze 18);
      - een `role="status"`-regio: "Verbinden…", "Bewerken met 2 anderen" (het aantal uit
        `office.presence`), "Opgeslagen om 10:42", "Verbinding weg". De namen van mede-bewerkers
        toont DS zelf in de editor.
    - Uit de iframe kom je met **Alt+Shift+Q** (focus naar de kopbalk; de iframe is van dezelfde
      origin, dus de pagina kan daar luisteren), met de sluitknop van DS (`customization.close` met
      `onRequestClose`) en met Terug in de browser. Of DS Alt+Shift+Q zelf gebruikt en of
      `customization.close` in 10.0 werkt, test de spike [niet getest].
    - De iframe krijgt `title="Editor: <naam>"`. Na `onDocumentReady` krijgt hij de focus. Bij sluiten
      gaat de focus terug naar waar je vandaan kwam.
    - Taal (`lang` nl of en), thema (licht, donker of systeem) en `type: "mobile"` op een smal
      scherm gaan mee naar DS [niet getest: de precieze waarden van `uiTheme`].
    - **`onRequestRefreshFile`** (M17): de pagina vraagt `POST …/session` opnieuw (een dichte sessie
      geeft een nieuwe key) en roept `docEditor.refreshFile(config)` aan. Lukt dat niet, dan laadt
      hij de editor opnieuw. Hetzelfde bij `office.dropped` met reden `stale` (Keuze 15).
    - Bij het verlaten van de pagina: `destroyEditor()`.
33. **Geen nieuwe web-afhankelijkheden, en de CSP van de app blijft.**
    - `api.js` laadt de pagina tijdens het draaien van `/office/`; dat telt niet mee voor het
      budget. De editorpagina is een lazy chunk (< 50 KB gzip; verwacht ±8 KB). Bestanden groeit
      met het menu "Nieuw" (< 1 KB).
    - **De eerste lading groeit hooguit 0,5 KB.** Er is nog 6,2 KB ruimte (M18), en het budget faalt
      hard als een ander woordenboek dan common, shell of auth in de eerste lading komt. Daarom
      zet `App.tsx` alleen een kleine luisteraar. Bij de eerste gebeurtenis `office.*` laadt die
      `office/notices.ts` en `loadAreas(["office"])` dynamisch. De schil importeert nooit
      `office/index.ts`.
    - **De CSP van de app verandert niet.** `api.js` werkt onder `WEB_CSP` zonder één melding (M4).
      Geen terugval met `style-src-attr 'unsafe-inline'` en geen eigen hostpagina.
    - `pwa/routing.ts`: `NEVER` krijgt `office(\/|$)`. De service worker van de app komt nooit aan
      `/office/`; DS heeft zijn eigen worker onder `/office/<versie>/`.
    - **Ontwikkelen:** `vite.config.ts` krijgt `"/office": { target: "http://localhost:8000", ws:
      true }`, naast `/api` op dezelfde poort. Gewone uvicorn heeft onze `Tunnel` niet. Dus draait
      de API in dev zo, naast een lokale DS uit `docker run`:
      ```sh
      PLATFORM_HTTP_PORT=8000 PLATFORM_OFFICE_URL=http://127.0.0.1:18080 \
        PLATFORM_OFFICE_SECRET=… PLATFORM_OFFICE_APP_URL=http://host.docker.internal:8081 \
        PLATFORM_OFFICE_INTERNAL_HOST=0.0.0.0 uv run python -m app.serve
      ```
      `docs/fase-4/e2e.md` geeft ook het `docker run` erbij.

### Schema en wat we schrappen

34. **Eén migratie 0007**, gemaakt door O0: de tabel `office_session` en twee kolommen op `blob`
    (`pdf_state`, `pdf_version`). Gewone `ADD COLUMN`, geen batch (zoals 0006). Met downgrade.
    Geen kolom op `version`: de versielijst toont al wie de vorige inhoud verving (`replaced_by`).
    - Terug naar v0.5.0 kan alleen na `python -m app.cli db-downgrade 0006` (nieuw, O0). v0.5.0
      start niet op een database op 0007 (M19). Het runbook zegt hoe (§2 O6).

**Geschrapt in fase 4** (bewust, met reden):

- DS voor wie niet mag bewerken, een alleen-lezen-modus van DS en DS via een publieke link. Het
  PDF-voorbeeld doet dat zonder DS bloot te stellen (Keuze 9).
- Een conversie die vanzelf start voor een bestand van een publieke link, of groter dan 20 MB.
  Op verzoek kan het wel (Keuze 25).
- Bewerken van ODF, doc/xls/ppt/rtf, macroformaten en pdf.
- WOPI en Collabora-code. Alleen de naad, de contracttests en het document (Keuze 3).
- Een eigen hostpagina, `sandbox` in de CSP van DS en `credentialless`: gemeten, en ze schermen
  niet af (M4–M6).
- "Opslaan als PDF in de map" en "omzetten naar docx" (fase 10).
- De titel in een open editor bijwerken bij hernoemen (`command meta`).
- Directe haken in `shares.py`, `groups.py`, `auth.py` en `trash.py`. De app controleert het
  recht bij elk verzoek naar DS (Keuze 7), en de ronde zet binnen 30 s uit de editor.
- Een herstelkopie na definitief verwijderen. De eigenaar wilde het weg (Keuze 19).
- Namen van mede-bewerkers in onze kopbalk. DS toont ze zelf; wij tonen het aantal.
- Versiegeschiedenis in de editor zelf (`changesurl` en `history` bewaren), vergelijken,
  samenvoegen, een label per versie.
- Plugins, macro's, AI, docbuilder, afbeelding via een URL.
- StatsD, meerdere DS-instanties en `shardkey` (cluster), eigen lettertypen.
- Office in de losse web-image (`web/nginx.conf`): alleen de platform-image heeft `/office/`.

### Vragen voor Jonas

1. **Welke editor?** (de open beslissingskaart) Standaard: **OnlyOffice** (dit ontwerp). Collabora
   CODE noemt zichzelf "not recommended for production", toont bij elke start een welkomstvenster
   dat niet uit kan, en heeft een conversie-eindpunt zonder authenticatie [doc, gemeten]. Bij
   Collabora veranderen de adapter (`app/office/oo/` wordt een WOPI-host), het padbeleid en de
   conversie; de kern blijft. "Zelf bouwen" valt buiten twee weken: samen bewerken in docx, xlsx en
   pptx is een product op zich.
2. **Office meteen aan bij installeren en bijwerken?** Standaard: **ja**. Het kost ±1 GB downloaden,
   ±4 GB schijf (8 GB vrij tijdens een update), ±0,5 GB RAM in rust (grens 2 GB), en na elke start
   van DS ±5 minuten 1–2 cores [gemeten]. Uit: `OFFICE=uit bash install.sh`, of `OFFICE=uit` in
   `/opt/platform/.env` en het script opnieuw draaien. Bij nee start het script DS pas na
   `OFFICE=aan`.
3. **Het restrisico van DS op hetzelfde adres accepteren?** (Keuze 9) Standaard: **ja**. Wat
   overblijft:
   - een fout in DS kan handelen namens een ingelogde bewerker;
   - een kwaadaardig bestand dat onze invoercontrole doorstaat, kan een fout in de converter van
     DS raken. Dat bestand kwam dan van een ingelogde gebruiker, of iemand vroeg er zelf om.

   We beperken het:
   - alleen wie nu mag bewerken, laadt de editor; een link van een andere site nooit;
   - DS onveranderd en vastgepind, plugins en macro's uit;
   - een volledige CSP. Die houdt weglekken via scripts, stijlen, frames, fonts en verzoeken
     tegen, maar niet via navigatie: dat kan geen CSP;
   - wat via een publieke link binnenkwam, en wat groter is dan 20 MB, gaat alleen op verzoek naar
     DS, en altijd eerst door de invoercontrole.

   In fase 8 komt als optie een eigen hostnaam (`office.<domein>`) met een tweede certificaat; dat
   vraagt een DNS-record van jou. Bij nee: bewerken pas met domein en eigen hostnaam, dus niet in
   het LAN zonder domein.
4. **Hoeveel versies maakt de editor?** (Keuze 17) Standaard: **één per bewerksessie, plus één per
   30 minuten in een lange sessie, en hooguit 10 per sessie**. Tussentijds opslaan (de knoppen,
   elke 5 minuten) werkt het bestand bij, maar maakt daarnaast geen eigen versie. Zo blijft de
   inhoud van vóór een sessie terug te halen tot er 20 nieuwere versies zijn. Eén sessie maakt er
   hooguit 10, dus die ene sessie duwt hem er nooit uit. Bij "elke opslag een versie" duwt één
   middag bewerken alle geschiedenis eruit (max. 20).
5. **De Collabora-adapter nu bouwen?** Standaard: **nee, alleen klaarzetten** (Keuze 3): de naad,
   de motor-neutrale contracttests en `docs/fase-4/collabora.md`. Omschakelen kost dan later ±1 week.
   Bij ja: ±3 dagen extra (een WOPI-host zonder locks, een nep-Collabora, en de echte CODE één keer
   in de sandbox), en dat past niet in twee weken naast de rest.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Naam | Grootte | Draait |
|---|---|---|---|
| **O0** | Fundament en spike (§3) | L | **eerst, alleen.** Werkers: O0-api, O0-spike (dag 1, met het gereedschap van de proef), O0-web, daarna O0-e2e |
| O1 | Toegang: proxy, tunnel en poort-cookie | M | parallel |
| O2 | Bewerken: sessies, opslaan, herstel, editorpagina | XL | parallel |
| O3 | Voorbeelden en PDF via conversie | M | parallel |
| O4 | Nieuw document | S | parallel |
| O5 | Beheer en bewaking | S | parallel |
| O6 | Uitrol: compose, install.sh, runbook | M | parallel |

Elk pakket werkt in een eigen git-worktree vanaf de tag `fase4-basis` (na O0).

**Eigendom na O0.** "Frozen" betekent: daarna verandert alleen de *O0-hotfix-lane* het, met een
kleine PR op de basis waar iedereen op rebaset.

**API** (paden onder `api/`)

| Bestand | Eigenaar |
|---|---|
| `app/models.py` (`OfficeSession`, `Blob.pdf_state`, `Blob.pdf_version`), `migrations/versions/0007_office.py`, `app/config.py`, `app/serve.py`, `app/main.py`, `app/cli.py` (`db-downgrade`, en `office-checkpoint` dat `sweep.checkpoint_all` aanroept), `app/routers/files.py` (alleen `place_file`: `exact`, `squash` en `pdf_state = ask`), `app/previews.py` (de twee haakregels en het deel `v` in `url` en `info`, §3.5), `app/routers/admin.py` (alleen de meldingen-haak), `app/office/{__init__,engine,keys,state,grants,runtime,urls,health,inspect}.py`, `app/office/oo/{__init__,tokens,client,admin}.py`, `app/routers/office_contracts.py`, `tests/fake_docserver/` (de kern, §3.6), `tests/office_contract.py`, tests `test_migration_0007.py`, `test_office_tokens.py`, `test_place_file_exact.py`, `test_serve_upgrade.py`, `test_office_imports.py`, `test_office_inspect.py`, `test_office_health.py`, `test_cli_db.py`, `test_fake_docserver.py` (de kern) | O0 (frozen) |
| `app/office/proxy.py`, `app/office/tunnel.py`, `app/office/gate.py`, `app/office/oo/paths.py`, `app/office/oo/stream.py`, tests `test_office_paths.py`, `test_office_proxy.py`, `test_office_tunnel.py`, `test_office_gate.py`, `test_office_stream.py` | O1 |
| `app/office/sessions.py`, `app/office/policy.py`, `app/office/saving.py`, `app/office/sweep.py`, `app/office/recovery.py`, `app/office/oo/editor.py`, `app/routers/office.py`, `app/routers/office_internal.py` (`doc`, `callback`), `tests/fake_docserver/forgotten.py`, tests `test_office_sessions.py`, `test_office_policy.py`, `test_office_callback.py`, `test_office_races.py`, `test_office_sweep.py`, `test_office_recovery.py`, `test_office_contract.py`, `test_cli_office.py`, het deel forgotten van `test_fake_docserver.py` | O2 |
| `app/office/convert.py`, `app/office/oo/convert.py`, `app/routers/office_pdf.py` (export, `convert` en de interne `src`), `app/render.py` (alleen de bron van de pdf-tak), `app/routers/viewer.py`, `tests/fake_docserver/{convert,pdf}.py`, tests `test_office_convert.py`, `test_viewer_office.py`, `test_office_pdf.py`, het deel conversie van `test_fake_docserver.py` | O3 |
| `app/office/blank.py`, `app/routers/office_new.py`, tests `test_office_blank.py`, `test_office_new.py` | O4 |
| `app/office/monitor.py` (alleen lezen en meldingen), `app/routers/admin_office.py`, tests `test_office_monitor.py`, `test_admin_office.py` | O5 |

O0 zet elke module van een ander pakket als stub met de signaturen van §3.3, zodat alles
compileert en de app zonder office precies fase 3 is.

**Eén eigenaar per lus.** De gezondheid (elke 30 s, plus `version`, `license` en de
tokencontrole elke 10 minuten) is van O0 (`health.py`). De ronde (Keuze 20) en het herstel van
forgotten files (Keuze 21) zijn van O2. O5 leest alleen wat in `OfficeState` en de database
staat.

**Web** (paden onder `web/src/`)

| Bestand | Eigenaar |
|---|---|
| `routes.tsx`, `App.tsx` (alleen de kleine luisteraar die `office/notices.ts` laadt, Keuze 33), `pwa/routing.ts`, `../vite.config.ts`, `browser/actions.ts` (haken `edit` en `pdf`), `Files.tsx` (menuknop "Nieuw" met haak, sneltoets O), `viewer/Viewer.tsx` (haak in de werkbalk), `Beheer.tsx` (zevende tabblad), `office/types.ts`, `office/api.ts`, `office/index.ts`, `i18n/errors/office.ts`, `i18n/types.ts` (gebied `office`), en **het blok `office` in `i18n/areas/{browser,viewer,admin}.{nl,en}.ts`**: elke tekst die de haken van O0 tonen (Bewerken, Downloaden als PDF, de items van Nieuw, de knoppen in de viewer, de titel van het tabblad, de redenen) | O0 (frozen) |
| `office/EditorPage.tsx`, `office/editor.ts`, `office/loadApi.ts`, `office/gate.ts`, `office/notices.ts`, `office/actions.ts`, `office/office.css`, `office/{editor,actions,notices}.test.ts`, `i18n/areas/office.*` | O2 |
| `viewer/**` behalve `Viewer.tsx`, `office/pdf.ts`, `office/pdf.test.ts`, `i18n/areas/viewer.*` behalve het blok `office` | O3 |
| `office/newItems.ts`, `office/newItems.test.ts` | O4 |
| `admin/OfficeTab.tsx`, `admin/format.ts`, `admin/office.test.ts`, `i18n/areas/admin.*` behalve het blok `office` | O5 |

Waarom het blok `office` bij O0: de route Bestanden laadt alleen `browser`, `upload` en `sharing`
(`routes.tsx:47`). Een tekst van een haak moet dus in een gebied staan dat die route al laadt, en
geen pakket is eigenaar van dat hele gebied.

**Repo**

| Bestand | Eigenaar |
|---|---|
| `e2e/conftest.py`, `e2e/pytest.ini`, `e2e/office_harness.py`, `e2e/fixtures/office/**` (docx, xlsx, pptx, doc, xls, ppt en rtf, eenmalig gemaakt met LibreOffice in de sandbox, met een `README.md` over waar ze vandaan komen; ook de API-tests gebruiken ze), `docs/fase-4/e2e.md`, `docs/fase-4/spike.md`, `.github/workflows/ci.yml` (de job `office` en de marker in `e2e`) | O0 (frozen) |
| `infra/single/**`, `.github/workflows/ci.yml` (alleen de job `infra` en de curl-controles in `images`), `docs/runbooks/office.md`, `docs/fase-4/collabora.md` | O6 |
| `e2e/test_office_<pkg>*.py` (ook die met marker `office_real`), `docs/fase-4/<pkg>.md`, `docs/fase-4/toetsenbord-<pkg>.md` | elk pakket zijn eigen |

**Regels** (dezelfde als in fase 3, plus):

1. Alleen eigen bestanden wijzigen; de rest alleen importeren.
2. Geen migraties en geen `models.py` buiten O0. Een schemawens gaat naar de hotfix-lane.
3. **Geen nieuwe pakketten** in `pyproject.toml` of `package.json`, ook geen dev-deps.
4. **Kern en adapter:** buiten `app/office/oo/` importeert alleen `app/office/engine.py` de adapter.
   Al het andere gebruikt `engine.current()`. `test_office_imports.py` controleert het.
5. Elke API-fout via `errors.fail` met een code uit `i18n/errors/office.ts`. O0 zet ze er allemaal in
   (§3.4). `test_error_catalog.py` controleert het.
6. **Sloten:** eerst het slot per bestand van office (`state.node_lock(node_id)`), dan `user_lock`,
   dan `blobs.lock`. Commit vóór elk wachten op een slot. Nooit een slot vasthouden tijdens een
   aanroep naar DS, behalve het downloaden in de callback onder het bestandsslot.
7. **Uitgaand verkeer naar DS alleen via `app/office/oo/client.py`** (één httpx-client,
   `trust_env=False`, geen omleidingen, tijdslimieten). Nooit een host uit een antwoord van DS volgen
   (`urls.rewrite`).
8. **Niets van DS komt rauw bij de browser,** behalve via de proxy en de tunnel (O1).
9. **Geen pad naar DS zonder `gate.check`** (Keuze 7). Ook niet voor een nieuwe padklasse.
10. **Geen bestand naar DS zonder `inspect`** (Keuze 23): niet bij openen en niet bij conversie.
11. UI-tekst via `t()` in de eigen gebieden, met Engels. Alles bereikbaar met het toetsenbord, met een
    checklist in `docs/fase-4/toetsenbord-<pkg>.md`.
12. Commentaar en commitberichten in het Nederlands.

**Afhankelijkheden tussen pakketten** (alleen via de contracten en stubs van O0):

| Wie | Gebruikt | Gemaakt door |
|---|---|---|
| O1, O2, O3, O5 | `engine.current()`, `oo.client.DocServer`, `oo.admin`, `state.OfficeState`, `oo.tokens.*`, `urls.*`, `grants`, `inspect`, `health` | O0 |
| O2 (e2e) | proxy en tunnel | O0 (de kern, alleen met `PLATFORM_OFFICE_DEV_UNGATED=1`); O1 maakt ze af |
| O1 (e2e) | de editorpagina en `POST …/session` | O2 (gaat vóór O1, §5) |
| O2 | `gate.issue(response, user, family)` | O0 (signatuur, zet het cookie al); O1 (de controle) |
| O1 | `members`, `banned` en `state` van `OfficeSession` | O0 (model); O2 vult ze |
| O2 | `gate.forget_user(user_id)` (de cache leeg na een intrekking) | O0 (signatuur); O1 |
| O3, O5 | `sessions.find_by_key`, `sessions.busy()` (een sessie die opent of opslaat) | O2 (O0-stub) |
| O5 | `recovery.restore(key)`, `recovery.forget(key)` voor de knoppen op `/beheer` | O2 (O0-stub) |
| O5 | de tellers van de tunnel in `state` | O1 |
| O5 | gezondheid, versie, licentie en de tokencontrole in `state` | O0 |
| O0 (`health.py`) | `convert.on_healthy()`, `recovery.kick()` bij "weer gezond" | O3 en O2 (O0-stubs) |
| O4 (web) | het openen van de editor (`/bewerken/<id>`) | O2 (O0 zet een stubpagina) |
| O6 | `app.cli office-checkpoint` | O0 (het commando); O2 (`sweep.checkpoint_all`, O0-stub geeft 0) |

---

### O1 — Toegang: proxy, tunnel en poort-cookie

**Doel.** Alles onder `/office/` veilig doorgeven aan DS: alleen toegestane paden, alleen voor wie
nu mag, met de juiste koppen, over http én WebSocket, en tellen.

**Maakt**
- `app/office/oo/paths.py`: het padbeleid van §3.10 als pure functie
  `classify(method, path, upgrade) -> PathClass | None`, plus `key_of(path)` voor sessie, data en
  editoracties.
  - Eerst normaliseren: prefix `/office` eraf, een versiesegment `^\d+\.\d+\.\d+-[0-9a-f]{8,64}$`
    eraf, hoofdletters van de map niet uitmaken.
  - Weigeren: `..`, `.`, lege segmenten, `\`, NUL, en procent-codes die een `/`, `\` of `.` worden.
  - Elke `*.html` (en een omleiding daarheen) is klasse `PAGE`, nooit `STATIC`.
  - De lijst komt uit de spike (§3.7) en ligt daarna vast.
- `app/office/oo/stream.py`: `StreamGuard` (§3.3). Leest de frames van de browser (gemaskeerd,
  eventueel in stukken) tot het CONNECT-pakket van socket.io en het eerste `auth`-bericht. Elk JWT
  daarin: HS256 met het office-geheim, en `document.key` gelijk aan de key van het pad. Hooguit
  64 KB en 10 s. De vorm komt uit de spike (stap 12).
- `app/office/gate.py`: `issue()` (O0-signatuur), `check(headers, key=None) -> GateUser | None`
  en `forget_user(user_id)`. De controle van Keuze 7: cookie, gebruiker, family, en met een key ook
  lid, niet `banned`, sessie niet dicht en nu schrijfrecht (`access.resolve`). Cache van 15 s
  (sleutel: sha-256 van het cookie plus de key).
- `app/office/proxy.py` maakt O0's kern af: de klassen en de toegang van Keuze 7, de koppen van
  Keuze 5 (ook `Cross-Origin-Resource-Policy` en de svg-CSP), `Sec-Fetch-Dest` op `PAGE`,
  `Location`, de 100 MB, de grens per gebruiker op editoracties, de CSP van Keuze 9 op HTML, en een
  logregel bij een geweigerd pad (`office: pad geweigerd <klasse> <pad>`), zodat een nieuw pad van
  een nieuwe DS opvalt. De code die `office_dev_ungated` leest, gaat weg; de vlag zelf doet daarna
  niets meer (`test_office_gate.py` bewijst het).
- `app/office/tunnel.py` maakt O0's kern af: `Origin` en de toegang vóór de upgrade naar DS; de
  `StreamGuard` na de upgrade; de grenzen per gebruiker en per key; de regel voor poort 80;
  tegendruk; idle; de controle elke 15 s; het register met `counts()`, `close_for(key, user)` en
  `close_all()`.

**Wijzigt.** Niets buiten de eigen bestanden. O0 hing de middleware al in `main.py` en zette `ws=Tunnel`
en de fix van M1 in `serve.py`.

**Gedrag** (geen nieuwe eindpunten):
```
/office/<statisch pad, geen html>   GET|HEAD, zonder cookie                              → DS
/office/<…>.html (editorpagina)     GET|HEAD, cookie; Sec-Fetch-Dest document → 404      → DS
/office/cache/files/data/<key>_…    GET, cookie + lid + recht van nu                     → DS
/office/<V>/doc/<key>/c/…           GET|POST (polling), cookie + lid + recht van nu      → DS
/office/<V>/doc/<key>/c/ upgrade    tunnel: Origin, cookie, lid, recht; daarna het eerste pakket
/office/<V>/<editoractie>           zie §3.10; met key: lid + recht; POST: grens per gebruiker
al het andere onder /office         404 not_found (en een logregel)
geen of ongeldig cookie, geen recht 401 office_gate
upgrade: Origin 403, cookie of recht 401, te veel 429, ander pad 404 — vóór er iets naar DS gaat
```

**Tests**
- `test_office_paths.py`: elke klasse; het versieprefix; hoofdletters; `%2e%2e`, `%2f`, dubbele
  slashes; `command` achter een versieprefix; `/office/` zelf (welkomstpagina met
  `Clear-Site-Data`); elke `*.html` is `PAGE`; alle paden uit de proxylogs van de proef (vaste lijst
  in `tests/fixtures/ds_paths.txt`) mogen.
- `test_office_proxy.py` (nep-DS als ASGI-app): koppen in en uit als vaste verwachting; `Cookie`
  gaat nooit naar DS; `Set-Cookie`, `Clear-Site-Data` en `Service-Worker-Allowed` komen nooit terug;
  `Cross-Origin-Resource-Policy` op statische bestanden; svg met `sandbox`; de volledige CSP op HTML;
  `Location` naar een vreemde host weg; body boven 100 MB 413; editoracties boven de grens per
  gebruiker 429; een antwoord van 50 MB in stukken met begrensd geheugen; gzip-bytes ongewijzigd;
  DS weg → 502; een `index.html` zonder cookie 404, met cookie en `Sec-Fetch-Dest: document` 404,
  met cookie en `iframe` 200.
- `test_office_tunnel.py` (echte sockets, zoals `test_serve.py`): een upgrade via de h11- en de
  httptools-luisteraar naar een kleine WebSocket-echo; de verbinding leeft langer dan
  `HEADER_TIMEOUT` (in de test 0,2 s); ook over https met een zelfgetekend certificaat; vreemde
  Origin 403; geen cookie 401; geen schrijfrecht meer 401; ander pad 404; meer dan 8 van één
  gebruiker 429; meer dan `max_editors` op één key 429; boven de globale grens 503; stil na de
  idle-tijd dicht; tegendruk met een trage lezer; een ingetrokken deling sluit binnen 15 s (klok
  in de test); uitloggen sluit binnen de controle; `counts()` en `close_all()`.
- `test_office_stream.py`: een CONNECT-pakket met het token van key A op het pad van key A gaat
  door; met een token van key B op het pad van A dicht; een token met een ander geheim dicht; een
  sessietoken van DS (zelfde geheim) gaat door; te groot of te traag dicht; frames in stukken.
- `test_office_gate.py`: geen cookie, verlopen, andere `typ`, office-geheim in plaats van
  app-geheim, uitgelogd (family ingetrokken), geblokkeerd, geen lid, `banned`, deling ingetrokken,
  bestand in de prullenbak, cache van 15 s, `forget_user` leegt hem; `office_dev_ungated` opent
  niets meer.
- e2e `test_office_o1.py` (nep-DS, met de editor van O2): twee contexts openen de editor; tekst van
  de één verschijnt bij de ander via de tunnel; §4.4 punt 3 en punt 10.
- e2e `test_office_o1_real.py` (`office_real`): §4.5 punt 5.

**Klaar als**
- Elk pad uit de inventaris van de spike gaat door; elk ander pad geeft 404.
- Zonder login, of zonder schrijfrecht van nu, kom je bij geen enkel dynamisch pad van DS, ook niet
  via de tunnel. Een context zonder cookie die `index.html` van de editor als pagina opent, krijgt
  404.
- Na het intrekken van een deling lukt opnieuw verbinden niet (nep-DS en echte DS).
- De editor werkt via `/office/` op http én https, met de echte DS in de job `office` (§4.5).

---

### O2 — Bewerken: sessies, opslaan, herstel en de editorpagina

**Doel.** Openen, samen bewerken, opslaan als nieuwe versie, en alle randgevallen van Keuze 14–21.

**Maakt (API)**
- `app/office/sessions.py`: `open_session(session, user, node) -> (OfficeSession, stale)` met de
  tabel van Keuze 14 en de rechten van Keuze 22 (na `inspect`, Keuze 23); `find_by_key`; `busy()`;
  `close`.
- `app/office/policy.py`: pure functies zonder database, met tabeltests:
  - `make_version(row, current_sha, now, cfg) -> bool` (Keuze 17, ook de 10 per sessie);
  - `choose_actor(history, actions, members, banned, can_write) -> UUID | None` (Keuze 16, stap 6);
  - `target(node_alive, owner_alive, actor) -> "original" | "copy" | "nothing"` (Keuze 16, stap 5–8);
  - `late(row, event) -> "ignore" | "save" | "drop"` (de tabel van dichte sessies, Keuze 15);
  - `recovery_name(name, when, locale) -> str`.
- `app/office/saving.py`: `store(row, tmp, sha, size, event) -> Outcome`
  (`saved | same | copy | ignored | retry | refused`). Het recept van Keuze 16, zonder OnlyOffice.
- `app/office/sweep.py`: de ronde van Keuze 20 als eigen taak (`start(app)`, elke
  `office_sweep_seconds`), met het tussentijds opslaan van Keuze 18 en `checkpoint_all(wait)`. De
  klok is in te spuiten, zodat de API-tests minuten kunnen overslaan.
- `app/office/recovery.py`: het herstel van Keuze 21 als eigen taak (bij de start, elke
  `office_recovery_seconds`, en `kick()` bij "weer gezond"), plus `restore(key)` en `forget(key)`
  voor de knoppen van O5.
- `app/office/oo/editor.py`: de editorconfig bouwen en tekenen; een callback lezen tot een
  motor-neutrale `CallbackEvent` met `mark` (§3.3); `info`, `forcesave` en `drop`.
- `app/routers/office.py`: `status`, `files/{id}`, `files/{id}/session`, `files/{id}/save`, `gate`.
- `app/routers/office_internal.py`: `doc/{key}` en `callback` (op de interne app).
- `tests/fake_docserver/forgotten.py`: de forgotten files van de nep-DS (`getForgottenList`,
  `getForgotten`, `deleteForgotten`, en wat een mislukte callback daar zet).
- Bij het stoppen van de app (lifespan): `checkpoint_all(wait=5)`.
- Gebeurtenissen `office.unsaved`, `office.recovered`, `office.dropped` en `office.presence` (§3.4),
  elk alleen naar wie het mag zien.

**Maakt (web)**
- `office/EditorPage.tsx` (route `/bewerken/:id`, lazy):
  - vraagt `POST …/session`, laadt `api.js` via `loadApi.ts` (een `<script src>` van `/office/`,
    één keer per pagina), maakt `new DocsAPI.DocEditor("office-editor", config)` met de events
    `onAppReady`, `onDocumentReady`, `onDocumentStateChange`, `onError`, `onWarning`,
    `onRequestClose` en **`onRequestRefreshFile`** (Keuze 32);
  - de kopbalk van Keuze 32: `<h1>`, Sluiten, Nu opslaan, de statusregio;
  - meldingen bij `stale`, `office_too_many`, `office_unavailable`, `office_unsafe`, `office_busy`
    (elke 3 s opnieuw, hooguit 30 s), `office.dropped` ("Je hebt geen toegang meer; de editor is
    gesloten", of bij reden `stale` stil opnieuw laden), `office.unsaved` met `final: false`, en
    waarschuwing -18;
  - Alt+Shift+Q in de iframe zet de focus op Sluiten. Sluiten gaat terug naar waar je vandaan kwam
    (anders naar de map van het bestand);
  - vernieuwt het cookie elke 30 minuten (`gate.ts`);
  - luistert naar `node.changed` voor dit bestand ("Opgeslagen om …") en naar `office.presence`;
  - `destroyEditor()` bij het verlaten.
- `office/actions.ts`: wanneer "Bewerken" er is (formaat, `canWrite`, office aan), voor de haak in
  `browser/actions.ts` en de knop in de viewer.
- `office/notices.ts`: de meldingen bij `office.unsaved` en `office.recovered`, met een link naar de
  herstelkopie. Geladen door de luisteraar in `App.tsx` (Keuze 33).
- `i18n/areas/office.nl.ts` en `.en.ts`.

**API**
```
GET  /api/v1/office/status
     → { enabled: bool, editing: bool, state: "off"|"starting"|"ok"|"down"|"misconfigured",
         edit_formats: ["docx","xlsx","pptx"], new_kinds: ["docx","xlsx","pptx"], max_bytes: 104857600 }
GET  /api/v1/office/files/{id}
     → { can_edit: bool,
         reason: null|"read_only"|"format"|"too_large"|"unsafe"|"trashed"|"album"|"office_off"|"office_down"|"too_many",
         open: { editors: int } | null,
         pdf_state: null|"pending"|"ready"|"failed"|"none"|"unsafe"|"ask" }
POST /api/v1/office/files/{id}/session   body { type?: "desktop"|"mobile", theme?: "light"|"dark" }
     → 200 { api_url: "/office/web-apps/apps/api/documents/api.js", config: {…, token}, key: str,
             stale: bool, gate_seconds: 7200 }   + Set-Cookie: office=…
     fouten: 404 not_found; 403 read_only | guest (gast-eigenaar); 422 not_a_file | album | trashed
             | office_format | office_too_large | office_unsafe; 409 office_busy;
             429 office_too_many {max} | rate_limited; 503 office_off | office_unavailable | office_misconfigured
POST /api/v1/office/files/{id}/save      → 202 {}   (lid met schrijfrecht van nu; 404 als er geen open sessie is)
POST /api/v1/office/gate                 → 204 + Set-Cookie (alleen als lid van een open sessie, met schrijfrecht van nu; anders 404)

Intern (poort 8081):
GET  /internal/office/doc/{key}   Authorization: Bearer <DS-download-JWT>
     → 200 de blob van de sessie (application/octet-stream, Repr-Digest: sha-256=:<base64>:)
       401 office_token; 404 not_found (ook als de sessie dicht is)
POST /internal/office/callback    body met token, en/of Authorization: Bearer
     → 200 {"error": 0} | 200 {"error": 1} | 503 (DS probeert opnieuw)
```

De editorconfig (getekend als geheel, zonder `events`):
```json
{ "documentType": "word|cell|slide", "type": "desktop|mobile", "width": "100%", "height": "100%",
  "document": { "fileType": "docx", "key": "<key>", "title": "<naam, ≤128>",
                "url": "http://platform:8081/internal/office/doc/<key>",
                "permissions": { "edit": true, "download": true, "print": true, "comment": true,
                                 "review": true, "copy": true, "fillForms": true } },
  "editorConfig": { "mode": "edit", "lang": "nl|en", "region": "nl-NL|en-GB",
                    "callbackUrl": "http://platform:8081/internal/office/callback",
                    "user": { "id": "<ds_uid>", "name": "<display_name, ≤128>" },
                    "coEditing": { "mode": "fast", "change": true },
                    "customization": { "autosave": true, "forcesave": true, "feedback": false,
                                       "help": false, "plugins": false, "macros": false,
                                       "macrosMode": "disable", "uiTheme": "<thema>",
                                       "close": { "visible": true, "text": "<Sluiten of Close, uit user.locale>" } } },
  "token": "<HS256, exp 2 uur>" }
```
- De tekst van `close` komt uit `user.locale`, net als `recovery_name`. Kent DS zelf een tekst per
  `lang`, dan laten we `text` weg (spike stap 6).
- De spike controleert `close`, `uiTheme`, `macros`, `macrosMode` en `plugins` tegen 10.0. Een
  sleutel die DS niet kent, gaat eruit.

**Tests**
- `test_office_sessions.py`: de rechtentabel van Keuze 22 rij voor rij (eigenaar, `write`-deling als
  gebruiker en als gast, `read` 403, album 422, prullenbak 422, zonder 2FA 403
  `totp_setup_required`, geblokkeerd 401, gast-eigenaar 403); twee gelijktijdige openingen
  (`asyncio.gather`) geven één key; elke rij van de tabel van Keuze 14 (`pending` jong en oud,
  `open` met en zonder iemand, `saving`, dicht); een oude `pending` en een nieuwe rij in één
  transactie, nooit een `IntegrityError`; `inspect` weigert → 422 `office_unsafe`; de config is
  getekend, met `exp`, en de handtekening dekt `document.url`, `callbackUrl`, `permissions` en
  `mode`; `office_too_many` bij de grens; `stale` na een upload; wie `banned` was en weer recht
  heeft, kan weer meedoen.
- `test_office_policy.py`: tabellen voor `make_version` (ook 25 tussenstanden met 31 minuten
  ertussen: hooguit 10 versies, en de versie van vóór de sessie blijft), `choose_actor`, `target`,
  `late`, `recovery_name`.
- `test_office_callback.py` (de test speelt DS met het office-geheim):
  - status 1 werkt `connected` bij en stuurt `office.presence`; een onbekende `ds_uid` geeft alleen
    een logregel;
  - status 2 → één nieuwe versie, `replaced_by` = de laatste bewerker, inhoud = wat de nep-link gaf;
  - status 6, 6, 2 binnen 30 minuten → één nieuwe versie (squash), en de key blijft na elke 6;
  - status 6 na 31 minuten (klok verzet) → een tweede versie;
  - 30 keer status 6 binnen 30 minuten laten de oudste versies staan;
  - dezelfde callback twee keer → geen tweede versie;
  - **een oud merk na een nieuwer** → niets geschreven;
  - **elke cel van de tabel van dichte sessies** (Keuze 15), ook een late opslag op een `lost`
    sessie: op het origineel als niets veranderde, anders een herstelkopie;
  - status 3 met `url` → herstelkopie naast het origineel, het origineel ongewijzigd;
  - bestand definitief weg → `{"error": 0}`, niets geschreven, geen kopie;
  - een download boven `office_max_save_bytes` of boven het quotum → niets geschreven,
    `office.unsaved` met `too_large` of `no_room`;
  - token alleen in de header, alleen in de body, verkeerd geheim, verlopen, `operation: download`
    op de callback, `alg: none`, ontbrekende `operation` → 401 `office_token`;
  - losse body-velden die afwijken van het token worden genegeerd;
  - een `url` met een vreemde host, een andere key in het pad, of `..` → nooit opgehaald (de
    `MockTransport` faalt op elke andere host);
  - database bezet (geïnjecteerd) → 503; een download die halverwege stopt → 503 en geen wijziging.
- `test_office_races.py`: elke rij van Keuze 19 als test, plus: 503 op de eerste status 6, dan
  status 2, dan de herhaling van die status 6 → de inhoud van status 2 blijft de huidige (S7); een
  herstart van de app tussen `saving` en het antwoord; DS geeft het op na 8 keer 503 → de ronde
  sluit (`lost`), het herstel zet het op het origineel, en de volgende opener krijgt een nieuwe key.
- `test_office_sweep.py`: pending verloopt; `info` 1 sluit; `saving` te oud → `info`; ingetrokken
  en geblokkeerde gebruikers krijgen `drop`, gaan uit `members` en in `banned`; nog verbonden bij de
  volgende ronde → forcesave en `drop` voor iedereen; bestand weg → `drop` voor iedereen;
  tussentijds opslaan na 5 minuten, en `error 4` is goed; `office.recovered` en `office.unsaved`
  komen nooit bij wie zijn recht kwijt is.
- `test_office_recovery.py`: forgotten met dezelfde sha → alleen weg; ongewijzigd bestand → gewoon
  opgeslagen, ook als de sessie nog `saving` is; veranderd of zonder schrijver → herstelkopie;
  bestand of eigenaar weg → alleen weg; quotum vol → blijft staan en meldt; bij de start van de app.
- `test_office_contract.py`: de scenario's van §4.4 met de nep-DS.
- `test_cli_office.py`: `office-checkpoint` stuurt forcesave voor open sessies en wacht.
- vitest: `editor.test.ts` (statusovergangen, `onRequestRefreshFile`, het opnieuw proberen bij
  `office_busy`, het vernieuwen van het cookie, de foutteksten), `actions.test.ts` (wanneer
  Bewerken zichtbaar is), `notices.test.ts`.
- e2e `test_office_o2.py` (nep-DS): §4.4, punten 1, 2 en 6.
- e2e `test_office_o2_real.py` (echte DS, marker `office_real`): §4.5, punten 1 en 4.

**Klaar als**
- Twee gebruikers bewerken via de nep-DS tegelijk één docx. Na sluiten is er precies één nieuwe
  versie met de tekst van allebei. Hetzelfde met de echte DS in de job `office`.
- Elke rij van de tabellen in Keuze 14, 15 en 19 heeft een test.
- Een forgotten file na een herstart van de app staat binnen 10 minuten weer in de app.
- Alleen met het toetsenbord: openen (E, of Enter op Bewerken), Nu opslaan, Sluiten, Alt+Shift+Q.

---

### O3 — Voorbeelden en PDF via conversie

**Doel.** Een echt voorbeeld van office-bestanden zonder de editor te openen, ook voor doc, xls,
ppt en rtf, en PDF-export. Begrensd, zodat conversie het bewerken niet in de weg zit.

**Maakt**
- `app/office/convert.py`: de taak `office.pdf` (payload `{sha, n}`, `max_attempts = 2`,
  `on_dead` → `failed`) en de inhaalronde `office.pdf.backfill` (payload `{after}`, in stukken van
  200, `jobs.chain` met `run_after` +60 s). De stappen van Keuze 25:
  - `on_preview_done(session, blob)`: `ask`, of een taak;
  - vóór het versturen: `inspect`; gezond (anders stoppen, de inhaalronde neemt het terug);
    `sessions.busy()` of het eigen slot bezet → over 5 s opnieuw in de rij, zonder poging;
  - de PDF er al, met de juiste `pdf_version` → alleen de thumbnails opnieuw;
  - anders: grant maken, `engine.convert(...)`, `fileUrl` herschrijven en ophalen, opslaan;
  - de thumbnails 256 en 1024 uit pagina 1 in het renderproces; `preview_meta.sizes` bijwerken;
    `previews.announce` (dan ziet de lijst de nieuwe thumbnail, met een nieuw adres);
  - `blob.pdf_state` en `blob.pdf_version` bijwerken; bij een nieuwe PDF `previews/<sha>/pages/`
    leeg (onder `blobs.lock`);
  - `on_discard(blobs, sha)` en `on_healthy()` voor de haken van O0;
  - `request(session, user, node)` voor de conversie op verzoek (grens per gebruiker).
- `app/office/oo/convert.py`: `POST /converter` (`async: true`, `Accept: application/json`,
  `filetype`, `outputtype: "pdf"`, `key`, `title`, `url`), elke seconde opnieuw tot `endConvert`,
  hooguit `office_convert_timeout`; de foutcodes naar `ConvertError(code, kind)` met `kind` uit
  `new_key | failed | none | retry | misconfigured` (Keuze 25).
- `app/routers/office_pdf.py`:
  - `POST /api/v1/office/files/{id}/convert` (lezen genoeg, ingelogd) → `{pdf_state}`;
  - `POST /api/v1/office/files/{id}/pdf` (lezen genoeg) → `{url, expires_in}`;
  - `GET /api/v1/office/pdf/{id}?token=` → `application/pdf`, `attachment; filename="<stam>.pdf"`
    (download-token, `active_user`, toegang per verzoek, net als `/download`);
  - intern: `GET /internal/office/src/{grant}` (download-JWT van DS; de grant moet bestaan).
- `tests/fake_docserver/convert.py` en `pdf.py`: `/converter` van de nep-DS (§3.6) en een kleine
  geldige PDF met N pagina's (stdlib).
- web: in de viewer bij een omgezet document het label "Voorbeeld" en de knop "Downloaden als PDF";
  bij `ask` de knop "Voorbeeld met opmaak maken"; zolang `pdf_state` `pending` is, de eenvoudige
  weergave met "Voorbeeld met opmaak wordt gemaakt…"; `office/pdf.ts` voor de actie in de lijst
  (haak van O0 in `actions.ts`, groep `convert`).

**Wijzigt (exclusief)**
- `app/render.py`: de pdf-tak kan een ander bronpad krijgen (de omgezette PDF) dan de blob zelf.
- `app/routers/viewer.py`: `_document` geeft bij een PDF met `pdf_state = ready` `kind: "pdf"`,
  `source: "office"`; `docinfo` en `_page` lezen dan de omgezette PDF. De paginacache blijft
  `previews/<sha>/pages/`, maar de ETag en `info.json` dragen de `pdf_version` (Keuze 25). De
  publieke varianten volgen vanzelf. Een eerdere versie zonder PDF: geen automatische conversie;
  het antwoord zegt `pdf_state: "ask"`.

**API**
```
GET  /api/v1/files/{id}/document    → DocumentOut + source: "pdf"|"office"|null, pdf_state   (optionele velden erbij)
POST /api/v1/office/files/{id}/convert → 202 { pdf_state: "pending" | "ready" }
     fouten: 404; 422 office_format | office_unsafe; 413 office_too_large; 429 rate_limited; 503 office_unavailable
POST /api/v1/office/files/{id}/pdf → { url: "/api/v1/office/pdf/{id}?token=…", expires_in: 600 }
     fouten: 404, 422 office_format | office_unsafe, 409 pdf_pending, 429 rate_limited,
             503 office_unavailable, 502 office_convert_failed {code}
GET  /api/v1/office/pdf/{id}?token= → application/pdf
```

**Tests**
- `test_office_convert.py` (nep-DS):
  - docx, xlsx, pptx, odt, doc, xls, ppt, rtf → PDF en thumbnails; `doc.json` blijft; dezelfde
    sha in twee bestanden → één conversie;
  - de nep-DS geeft bij hergebruik van een key het oude resultaat (zoals de echte), en onze key per
    sha voorkomt dat; na onze eigen tijdslimiet dezelfde key opnieuw, pas na -2 of -4 een nieuwe;
  - **invoer:** een odt-bom (2 MB, gigabytes uitgepakt) en een zip die over zijn grootte liegt,
    komen nooit bij de nep-DS (`pdf_state = unsafe`, de log van de nep-DS is leeg); een pptx met
    80 MB foto's gaat wel;
  - **publieke link:** een upload via een link krijgt `ask` en wordt niet omgezet; een ingelogde
    gebruiker met leesrecht kan het vragen; de 11e vraag in 10 minuten → 429;
  - **vastlopen:** `__fake/kill` tijdens een conversie → na hooguit 2 runs `failed` en de melding;
    nooit meer vanzelf;
  - DS niet gezond vóór het versturen → geen poging, `pdf_state` leeg, de inhaalronde neemt het
    terug na "weer gezond";
  - een sessie `pending` of `saving` → de conversie wacht, zonder poging; twee taken tegelijk → de
    tweede komt over 5 s terug, en een previewtaak voor een foto loopt intussen door;
  - -3 blijvend; -5 → `none`; -8 geeft de melding; een `fileUrl` met een vreemde host → geweigerd;
    een grant verloopt;
  - **een nieuwe preview gooit de PDF niet weg** (M10); de blob verdwijnt tijdens het omzetten →
    niets blijft achter; `collect_garbage` haalt de PDF weg;
  - **caches:** een pptx met een ingebedde thumbnail krijgt na de conversie een ander adres voor
    256 en 1024; een hogere `PDF_VERSION` geeft een nieuwe ETag, nieuwe pagina's en het juiste
    aantal;
  - de inhaalronde loopt over 450 blobs in drie stukken, met 60 s ertussen; zonder office wordt
    niets klaargezet.
- `test_viewer_office.py`: `/document` met en zonder PDF; pagina's van de omgezette PDF; een
  publieke link ziet de pagina's maar kan geen conversie starten; een ingetrokken deling stopt de
  pagina's meteen.
- `test_office_pdf.py`: downloaden als PDF met leesrecht; 409 `pdf_pending` en daarna 200; een
  verlopen token.
- e2e `test_office_o3.py` (nep-DS): §4.4 punt 4 en 8.
- e2e `test_office_o3_real.py` (`office_real`): §4.5 punt 2.

**Klaar als**
- "Office-preview werkt zonder de editor te openen": bewezen met de nep-DS en met de echte DS in
  de job `office`.
- Geen bestand gaat naar DS zonder de invoercontrole, en geen upload via een link gaat vanzelf.
- Zonder DS precies het gedrag van fase 3 (de bestaande `test_office.py` en `test_viewer.py` slagen).

---

### O4 — Nieuw document

**Doel.** Een leeg docx, xlsx of pptx maken in de huidige map en meteen openen.

**Maakt**
- `app/office/blank.py`: `blank(kind, locale) -> bytes` (Keuze 24).
- `app/routers/office_new.py`: `POST /api/v1/office/new`.
- web: `office/newItems.ts` (de items voor het menu "Nieuw" en het contextmenu van de map, via de
  haak van O0; de teksten staan al in het blok `office` van `browser.*`); na het maken naar
  `/bewerken/<id>`.

**API**
```
POST /api/v1/office/new  body { parent_id: uuid, kind: "docx"|"xlsx"|"pptx", name?: str }
     → 201 NodeOut      (een naam zonder extensie krijgt die erbij; bestaat hij al: "Nieuw document (2).docx")
     fouten: 403 read_only; 404 folder_not_found; 413 no_room; 422 album | invalid_input
```
Dit werkt ook als DS uit staat (het is gewoon een bestand). De web-app toont de items alleen als
`office/status.editing` waar is.

**Tests**
- `test_office_blank.py`: geldige zip (`testzip()` schoon); `[Content_Types].xml` noemt elk
  onderdeel; de B21-lezer leest alle drie; `inspect` laat alle drie door; twee keer maken geeft
  dezelfde bytes.
- `test_office_new.py`: in een eigen map, in een gedeelde `write`-map (quotum van de eigenaar),
  `read`-map 403, naamconflict, quotum vol, Engelse namen bij `locale=en`.
- e2e `test_office_o4.py`: Nieuw → Document met alleen het toetsenbord (O, pijltjes, Enter) → de
  editorpagina opent.
- e2e `test_office_o4_real.py` (`office_real`): §4.5 punt 3.

**Klaar als:** de drie soorten openen in de echte DS en zetten om naar PDF; het menu werkt met het
toetsenbord.

---

### O5 — Beheer en bewaking

**Doel.** Op `/beheer` zien hoe office ervoor staat, de grens instellen, en niet-opgeslagen werk
terugzetten. O5 meet zelf niets: hij leest `OfficeState` (O0 en O1) en de database (O2, O3).

**Maakt**
- `app/office/monitor.py`: de piek van de verbindingen; `notices(state, session) -> list[Notice]`
  voor de haak van O0 in `admin.system_info`.
- `app/routers/admin_office.py` (alleen `AdminUser`).
- `OfficeConfig` in `Setting` met sleutel `office`, in het geheugen zoals `SecurityPolicy`.
- web: `admin/OfficeTab.tsx` (tabblad 7, Alt+7), `admin/format.ts` (`NOTICES`, `JOB_TYPES`
  `office.pdf` en `office.pdf.backfill`), teksten in `i18n/areas/admin.*`. Het tabblad zegt ook
  hoe je office aan- of uitzet (het commando in de console).

**API**
```
GET  /api/v1/admin/office → OfficeAdmin {
       configured: bool, enabled: bool,
       state: "off"|"starting"|"ok"|"down"|"misconfigured", down_since: str|null,
       version: str|null,                         # command version (O0)
       license_connections: int|null,             # command license; ≥ 2147483647 = "geen limiet"
       tokens_enforced: bool|null,                # de controle zonder token (Keuze 28)
       max_editors: int, connections: int, people: int, documents: int,
       peak: { connections: int, at: str } | null,  # sinds de start van de app
       open: [ { name, owner, editors: [display_name], since } ],
       unsaved: [ { key, name, owner: str|null, at: str|null, reason: str|null } ],
       conversions: { queued: int, failed: int, unsafe: int, ask: int } }
PUT  /api/v1/admin/office  body { enabled: bool, max_editors: int (1–500),
                                  checkpoint_minutes: int (1–60), version_minutes: int (5–1440) } → OfficeAdmin
POST /api/v1/admin/office/checkpoint → { sent: int, saved: int }
POST /api/v1/admin/office/conversions/retry → { queued: int }     (zet failed terug, voor de huidige PDF_VERSION)
POST /api/v1/admin/office/unsaved/{key}/restore → NodeOut      (404 not_found; 409 office_owner_gone; 413 no_room)
DELETE /api/v1/admin/office/unsaved/{key} → 204
```
- Meldingen: `office_down` (> 2 min onbereikbaar), `office_misconfigured` (ook: DS eist geen
  tokens), `office_version` (DS < 10.0, of een andere versie dan vastgepind), `office_limit`
  {used, max} (≥ 80 %), `office_unsaved` {count}, `office_convert_failed` {count}.

**Tests**
- `test_office_monitor.py`: licentie 2147483647 → geen limiet; licentie 20 (nep-DS) → melding vanaf
  16; een andere versie → `office_version`; `tokens_enforced` onwaar → `office_misconfigured`;
  forgotten files → melding; mislukte conversies → melding.
- `test_admin_office.py`: alle velden met de nep-DS; niet-beheerder 403; grens opslaan en meteen
  gebruikt; terugzetten en verwijderen; eigenaar weg → 409; mislukte conversies opnieuw.
- e2e `test_office_o5.py`: §4.4 punt 7, en het tabblad toont 2 verbindingen tijdens punt 1.

**Klaar als:** het tabblad toont de echte verbindingen (2 tijdens de test met twee gebruikers);
terugzetten werkt; alle meldingen zijn vertaald.

---

### O6 — Uitrol: compose, install.sh, runbook

**Doel.** Office aan met hetzelfde ene commando, en bijwerken en terugrollen zonder dat het
platform breekt.

**Maakt**

`infra/single/compose.office.yaml` (gecontroleerd met `docker compose config`, M9):
```yaml
# Office (fase 4): OnlyOffice Docs als tweede container. Alleen de app praat ermee, over het
# interne netwerk "office" zonder uitgang naar buiten. Geen eigen poort.
services:
  platform:
    environment:
      PLATFORM_OFFICE_URL: http://office
      PLATFORM_OFFICE_APP_URL: http://platform:8081
      PLATFORM_OFFICE_SECRET: ${OFFICE_JWT_SECRET:?}
    networks: [default, office]
    stop_grace_period: 30s

  office:
    # De naam van Docker Hub, op digest. Na een pull van de mirror zet install.sh deze naam er
    # lokaal op, zodat compose het image altijd zelf vindt. Een pin staat in .env (OFFICE_IMAGE).
    image: ${OFFICE_IMAGE:-onlyoffice/documentserver:10.0.0.1@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3}
    pull_policy: missing
    restart: unless-stopped
    depends_on: [platform]           # stopt eerst, zodat zijn laatste opslag de app nog vindt
    networks: [office]
    stop_grace_period: 60s
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER, NET_BIND_SERVICE, KILL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 512
    environment:
      JWT_ENABLED: "true"
      JWT_SECRET: ${OFFICE_JWT_SECRET:?}
      JWT_HEADER: Authorization
      SECURE_LINK_SECRET: ${OFFICE_LINK_SECRET:?}
      WELCOME_PAGE_ENABLED: "false"
      PLUGINS_ENABLED: "false"
    volumes:
      - ./office/local-production-linux.json:/etc/onlyoffice/documentserver/local-production-linux.json:ro
      - office-lib:/var/lib/onlyoffice
      - office-data:/var/www/onlyoffice/Data
      - office-logs:/var/log/onlyoffice
      - office-fonts:/usr/share/fonts/truetype/custom

networks:
  office:
    internal: true

volumes:
  office-lib:
  office-data:
  office-logs:
  office-fonts:
```

`infra/single/compose.office-limits.yaml`:
```yaml
services:
  office:
    mem_limit: 2g
    memswap_limit: 2g
    cpus: 2
```

`infra/single/office/local-production-linux.json`:
```json
{
  "services": {
    "CoAuthoring": {
      "callbackBackoffOptions": { "retries": 8 },
      "server": { "docbuilderEnable": false }
    }
  },
  "FileConverter": {
    "converter": {
      "inputLimits": [
        { "type": "docx;dotx;docm;dotm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
        { "type": "xlsx;xltx;xlsm;xltm", "zip": { "uncompressed": "300MB", "template": "*.xml" } },
        { "type": "pptx;ppsx;potx;pptm;ppsm;potm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
        { "type": "vsdx;vstx;vssx;vsdm;vstm;vssm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
        { "type": "odt;ott;odp;otp", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
        { "type": "ods;ots", "zip": { "uncompressed": "150MB", "template": "*.xml" } }
      ]
    }
  },
  "aiSettings": { "enable": false }
}
```
- De sleutels staan zo in `default.json` van het image [image]. `docbuilderEnable` werkte via dit
  bestand [gemeten]. `callbackBackoffOptions`, `inputLimits` en `aiSettings` controleert de spike
  (8 pogingen tellen in de log van de nep-app; een odt boven de grens geeft een fout).
- `token.session.expires` komt er alleen bij als de spike het zegt (Keuze 11).
- Geen `ALLOW_PRIVATE_IP_ADDRESS`: de standaard (uit) blijft. Geen `autoAssembly`: de app slaat zelf
  tussentijds op (Keuze 18).

**Wijzigt `install.sh`.** Het script wordt een rij functies met een `main`, zodat een test ze kan
laden. De kern voor office:

```bash
OFFICE_REPO=onlyoffice/documentserver
OFFICE_TAG=10.0.0.1
OFFICE_DIGEST=sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3

hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
add_key() { grep -q "^$1=" "$ENV_FILE" || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }
set_key() { sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; }

# De eerste regel van main, vóór er een .env gelezen wordt (M13)
want_office() {
  case "${OFFICE-}" in
    "" | aan | uit) printf '%s' "${OFFICE-}" ;;
    *) echo "OFFICE moet aan of uit zijn (nu: $OFFICE)." >&2; return 1 ;;
  esac
}

# Na het maken of lezen van .env; test_install_env.sh roept precies deze functie aan
merge_env() {
  add_key OFFICE aan
  add_key OFFICE_JWT_SECRET "$(hex)"
  add_key OFFICE_LINK_SECRET "$(hex)"
  [ -z "$1" ] || set_key OFFICE "$1"
  chmod 600 "$ENV_FILE"
}

# Het image: een pin in .env gaat voor. Anders op digest, eerst Docker Hub, dan de mirror,
# en daarna altijd de naam van Docker Hub erop (M19)
office_image() {
  local hub="$OFFICE_REPO@$OFFICE_DIGEST" mirror="mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST" img
  if [ -n "${OFFICE_IMAGE:-}" ]; then
    docker image inspect "$OFFICE_IMAGE" >/dev/null 2>&1 || docker pull -q "$OFFICE_IMAGE" >/dev/null 2>&1 || return 1
    printf '%s' "$OFFICE_IMAGE"; return 0
  fi
  for _ in 1 2 3; do
    for img in "$hub" "$mirror"; do
      if docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null 2>&1; then
        docker tag "$img" "$OFFICE_REPO:$OFFICE_TAG"
        printf '%s' "$OFFICE_REPO:$OFFICE_TAG@$OFFICE_DIGEST"; return 0
      fi
    done
    sleep 5
  done
  return 1
}
```
Stappen, in deze volgorde:
1. **Eerst:** `want=$(want_office) || exit 1`, als eerste regel van `main`.
2. **Docker:** zoals nu, maar `systemctl enable --now docker` alleen als `docker info` faalt, en
   `systemctl restart docker` alleen als het script `daemon.json` net schreef (en systemd draait).
   De controle met hello-world haalt eerst `mirror.gcr.io/library/hello-world`, dan `hello-world`.
   Lukt geen pull, dan zegt het script dat het netwerk of Docker Hub het probleem is. Alleen als
   het starten zelf faalt, komt de uitleg over nesting en keyctl.
3. **Broncode:** zoals nu, met `PLATFORM_REPO` en `PLATFORM_SRC_REF` voor de test (Keuze 30).
4. **Instellingen:** `.env` maken of lezen zoals nu, dan `merge_env "$want"` en `. "$ENV_FILE"`.
5. **Controle:** `docker info --format '{{.Driver}}'` is `overlay2` of `overlayfs` (M8), en
   `df --output=avail -B1G /var/lib/docker` geeft ≥ 8. Niet goed: melding met uitleg, en office
   blijft zoals het was.
6. **Image:** `if img=$(office_image); then …; elif` de container `platform-office-1` draait: zijn
   huidige image ("office blijft op de vorige versie"); `else` office voor deze keer uit ("het
   platform werkt zonder editor; draai het script later opnieuw"). Nooit afbreken.
7. **Bestanden:** `-f compose.yaml`, plus `-f compose.office.yaml` als office meedoet, plus
   `-f compose.office-limits.yaml` als `docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}'`
   `true true` geeft.
8. **Tussentijds opslaan:** `"${compose[@]}" exec -T platform python -m app.cli office-checkpoint
   --wait 30 || true`.
9. **App:** `OFFICE_IMAGE=$img "${compose[@]}" up -d --build --remove-orphans platform`, dan de
   bestaande wachtlus op `/health`.
10. **DS:** `OFFICE_IMAGE=$img "${compose[@]}" up -d office` (alleen als office meedoet; compose
    vervangt hem alleen als er iets veranderde). Daarna oude images van DS weg die geen container
    gebruikt, behalve het vorige en de pin.
11. **Slot:** de eindtekst noemt office ("start op de achtergrond, over ±1 minuut klaar") of waarom
    het uit staat.

**Ook**
- `infra/single/.env.example`: `OFFICE=aan`, `OFFICE_JWT_SECRET=`, `OFFICE_LINK_SECRET=` (leeg; het
  script vult ze), en `# OFFICE_IMAGE=` (de pin, met uitleg).
- `infra/single/tests/test_install_env.sh` (bash, met `INSTALL_LIB=1`, in CI): `merge_env` op een
  `.env` uit fase 3 geeft de drie sleutels; `merge_env uit` op een `.env` met `OFFICE=aan` geeft
  `OFFICE=uit`; `want_office` met `OFFICE=uit` vóór het lezen van zo'n `.env` (de volgorde van
  `main`); een bestaand geheim blijft staan; een pin in `.env` blijft na een tweede run; de waarden
  zijn hex; `chmod 600`; `office_image` die faalt, stopt het script niet; de driver-controle
  accepteert `overlay2` en `overlayfs` en weigert `vfs`.
- `infra/single/tests/test_install_update.sh` (alleen in de sandbox, bij de integratie): een
  container `debian:12` (van `mirror.gcr.io`, getagd) met `/var/run/docker.sock`, de docker-cli van
  de host, en `/opt/platform` en de datamap op **dezelfde absolute paden** gekoppeld. Zo vinden de
  bind mounts van compose hun pad op de host. De basisimages van de `Dockerfile` (`node:22-alpine`,
  `python:3.12-slim`) komen vooraf van `mirror.gcr.io`, getagd naar hun naam; `ghcr.io/astral-sh/uv`
  van ghcr zelf [niet getest: of ghcr hier werkt]. Eerst installeren op `3ccee0a`
  (`PLATFORM_REPO=<repo> PLATFORM_SRC_REF=3ccee0a`), dan bijwerken naar de lokale commit van de
  worktree. Er wordt niets gepusht.
- `infra/single/README.md`: wat office is, wat het kost, aan en uit, waar de data staat, het
  herstel van forgotten files, "Office start op" en "Office onbereikbaar".
- `docs/runbooks/office.md`:
  - **DS bijwerken en terugrollen:** een oudere digest als pin in `.env` (`OFFICE_IMAGE=…`) en het
    script opnieuw; het vorige image staat er nog. De digest vervangen bij een nieuwe DS (met de
    job `office`);
  - **App terugrollen:** liefst `OFFICE=uit bash install.sh` (de app blijft nieuw). Moet de code
    echt terug: eerst `office-checkpoint`, wachten tot "Niet opgeslagen" op `/beheer` leeg is, dan
    `docker compose … stop platform`, `docker compose … run --rm --no-deps platform python -m
    app.cli db-downgrade 0006`, en dan `PLATFORM_SRC_REF=<oude commit> OFFICE=uit bash
    install.sh`. Let op: de sessies en de PDF-staat zijn dan weg; de PDF's in
    `data_dir/office/` mogen weg;
  - niet-opgeslagen werk; de belasting meten op de LXC; de controle dat er op de LXC niets anders
    luistert dan 80, 443 en SSH (M7).
- `docs/fase-4/collabora.md` (Keuze 3): de gemeten feiten en wat een WOPI-host vraagt. CheckFileInfo,
  GetFile, PutFile (met `X-COOL-WOPI-Timestamp` en 409 met `COOLStatusCode 1010`), locks als we die
  aanbieden, `aliasgroup1`, `ssl.termination=true`, `net.service_root=/office` (de proxy **houdt**
  het prefix), `net.proto=IPv4`, nooit `/cool/convert-to` of `/cool/adminws` van buiten, het
  welkomstvenster in elke e2e, en dat CODE "not recommended for production" is [doc, gemeten].
  Welke modules van `app/office/oo/` een Collabora-adapter vervangt. Schatting: ±1 week.
- `.github/workflows/ci.yml`: alleen de job `infra` (Keuze 31) en de curl-controles in `images`.
  De job `office` is van O0 (§4.5).

**Klaar als**
- In de sandbox (`test_install_update.sh`): een installatie uit fase 3 bijwerken geeft office
  aan, de geheimen in `.env`, DS gezond, de app op zijn poort. `OFFICE=uit` haalt DS weg; opnieuw
  `aan` brengt hem terug; een geblokkeerde pull laat het platform werken; een pin blijft staan; na
  een pull van alleen de mirror maakt een losse `docker compose … up -d` office niet opnieuw.
- `test_install_env.sh` en de job `infra` zijn groen.

---

## 3. Fundament (O0, eerst en alleen)

O0 raakt alle hotspots en legt de contracten vast. Werkers:

- **O0-spike** (dag 1): de spike van §3.7 met het gereedschap dat er al is en dat al gemeten heeft
  (`fase4/proef/proxy.py`, `stub.py`, `tools/coedit.py`). Hij wacht niet op de kern van de app;
- **O0-api** tegelijk: contracten, migratie, hotspots, de kern van proxy en tunnel, de invoercontrole,
  de gezondheid en de kern van de nep-DS;
- **O0-web** tegelijk;
- daarna **O0-e2e**: harnas, nep-DS als proces, de fixtures en de job `office` in CI. Aan het eind
  van O0 doet hij stap 2, 5 en 11 van de spike opnieuw, nu via de kern in de app.

Eén PR op de fase-4-branch, getagd `fase4-basis`. Daarna werkt de app zonder office-instellingen
precies als fase 3. Met office ingesteld zegt de stubpagina "nog niet klaar".

### 3.1 Migratie 0007 (`0007_office.py`, down_revision `0006`)

Gewone `ADD COLUMN` en een nieuwe tabel, geen batch (zie 0002 en 0006). Met een downgrade.

```
office_session
  key           VARCHAR(128) PRIMARY KEY          -- document.key
  node_id       UUID NULL  REFERENCES node(id) ON DELETE SET NULL
  owner_id      UUID NULL  REFERENCES user(id) ON DELETE SET NULL
  name          VARCHAR(255) NOT NULL             -- de naam bij het openen (voor kopie en "Niet opgeslagen")
  file_type     VARCHAR(8)  NOT NULL              -- docx | xlsx | pptx
  blob_id       VARCHAR(64) NOT NULL              -- de inhoud waar de key voor staat (na forcesave bijgewerkt)
  checkpoint    VARCHAR(64) NULL                  -- wat deze sessie het laatst zelf opsloeg (squash)
  versioned_at  DATETIME NULL                     -- laatste opslag van deze sessie die een versie maakte
  versions_made INTEGER NOT NULL DEFAULT 0        -- versies uit deze sessie (hooguit 10, Keuze 17)
  saved_at      DATETIME NULL                     -- laatste opslag (voor het tussentijds opslaan)
  applied_mark  VARCHAR(40) NULL                  -- hoogste merk dat is toegepast (Keuze 15)
  state         VARCHAR(8)  NOT NULL DEFAULT 'pending'   -- pending | open | saving | closed
  members       JSON NOT NULL DEFAULT '{}'        -- {ds_uid: user_id}: wie een config kreeg en nog recht heeft
  banned        JSON NOT NULL DEFAULT '[]'        -- [ds_uid]: eruit gezet (Keuze 20)
  connected     JSON NOT NULL DEFAULT '[]'        -- [ds_uid] volgens de laatste status 1 of info
  last_error    VARCHAR(32) NULL                  -- no_writer | gone | no_room | too_large | owner_gone | error_status
  closed_reason VARCHAR(16) NULL                  -- saved | no_changes | lost | copy | refused | gone
  node_name     VARCHAR(64) NOT NULL              -- settings.node_name (voor het cluster later)
  created_at    DATETIME NOT NULL
  updated_at    DATETIME NOT NULL                 -- ook: sinds wanneer 'saving' (Keuze 20)
  closed_at     DATETIME NULL
INDEX ix_office_session_node (node_id)
INDEX ix_office_session_state (state)
UNIQUE INDEX ux_office_session_open (node_id) WHERE state <> 'closed' AND node_id IS NOT NULL
                                              -- sqlite_where en postgresql_where

blob
  pdf_state     VARCHAR(8) NULL                   -- NULL | pending | ready | failed | none | unsafe | ask
  pdf_version   INTEGER NOT NULL DEFAULT 0
INDEX ix_blob_pdf (pdf_version)
```
- `node_id` met `SET NULL`: na definitief verwijderen blijft de rij. Een late callback en het herstel
  weten dan nog wat het was, en schrijven niets (Keuze 19).
- Geen foreign key op `blob_id` en `checkpoint`: de sessie houdt geen verwijzing vast. `ref_count`
  blijft van de bestanden en versies.
- De downgrade haalt eerst `ix_blob_pdf` weg, dan de kolommen, dan de tabel. Daarna is de database
  gelijk aan 0006, en start v0.5.0 weer (M19).
- `models.py` krijgt `OfficeSession` en de twee kolommen, met commentaar over de statussen en Keuze 17.

### 3.2 Instellingen en geheimen

`config.py`:
```python
office_url: str = ""               # PLATFORM_OFFICE_URL, bijv. http://office; leeg = geen office
office_secret: str = ""            # PLATFORM_OFFICE_SECRET = JWT_SECRET van DS (≥ 32, ≠ app-geheim)
office_app_url: str = ""           # PLATFORM_OFFICE_APP_URL: hoe DS de app bereikt, bijv. http://platform:8081
office_internal_host: str = ""     # leeg = host
office_internal_port: int = 8081
office_convert_timeout: float = 120
office_max_pdf_bytes: int = 512 * 1024**2
office_max_save_bytes: int = 512 * 1024**2   # grens op een download in de callback (Keuze 16)
office_auto_convert_bytes: int = 20 * 1024**2  # groter: alleen op verzoek (Keuze 25)
office_ole_max_bytes: int = 20 * 1024**2     # doc, xls, ppt en rtf (Keuze 23)
office_max_tunnels: int = 1000
office_max_tunnels_user: int = 8
office_gate_cache_seconds: float = 15
office_health_seconds: float = 30
office_sweep_seconds: float = 30   # de e2e zet 1
office_recovery_seconds: float = 600  # de e2e zet 2
office_dev_ungated: bool = False   # alleen O0, O2 en tests: proxy en tunnel zonder toegangscontrole;
                                   # na O1 doet hij niets meer, bij de integratie gaat hij weg

@property
def office_configured(self) -> bool  # url, app_url en een geldig geheim
```

In `Setting` met sleutel `office`, aan te passen op `/beheer`: `OfficeConfig {enabled: true,
max_editors: 20, checkpoint_minutes: 5, version_minutes: 30}`. In het geheugen, zoals
`SecurityPolicy`.

### 3.3 Python-contracten (frozen)

```python
# app/office/keys.py
KEY_RE = re.compile(r"^[0-9A-Za-z._=-]{1,128}$")
def new_key(node_id: uuid.UUID) -> str               # f"{node_id.hex}-{secrets.token_hex(8)}"
def convert_key(sha: str, n: int = 1) -> str         # f"p{PDF_VERSION}-{sha}", vanaf n = 2 met "-a<n>"

# app/office/engine.py (de naad; zie Keuze 3)
class PathClass(StrEnum): STATIC; PAGE; DATA; SESSION; SETTINGS; ACTION
@dataclass(frozen=True)
class Health: ok: bool; version: str | None; limit: int | None
@dataclass(frozen=True)
class OpenContext: key: str; ds_uid: str; user_name: str; title: str; file_type: str
    lang: str; region: str; theme: Literal["light", "dark"]; mobile: bool; doc_url: str
    callback_url: str; close_text: str | None
@dataclass(frozen=True)
class CallbackEvent:
    kind: Literal["presence", "final", "failed", "closed", "checkpoint", "checkpoint_failed", "other"]
    key: str
    url: str | None            # pad en query, nog niet herschreven
    connected: list[str]       # ds_uid's
    editors: list[str]         # ds_uid's, de laatste bewerker eerst (history, dan actions)
    explicit: bool             # opslaan op verzoek van een gebruiker (OnlyOffice: forcesavetype 1)
    filetype: str | None
    mark: str | None           # volgorde van de opslag, sorteerbaar (OnlyOffice: lastsave)
class StreamGuard(Protocol):   # één per tunnel; leest alleen het begin van de stroom van de browser
    def feed(self, data: bytes) -> Literal["more", "ok", "deny"]
class TokenError(Exception): ...
class ConvertError(Exception): code: int; kind: Literal["new_key", "failed", "none", "retry", "misconfigured"]
class Engine(Protocol):
    name: str
    strip_prefix: bool                      # OnlyOffice: ja; Collabora: nee
    edit_formats: frozenset[str]
    convert_formats: frozenset[str]
    def classify(self, method: str, path: str, upgrade: bool) -> PathClass | None
    def session_key(self, path: str) -> str | None             # sessie, data en editoracties
    def stream_guard(self, key: str) -> StreamGuard
    def editor_config(self, ctx: OpenContext) -> dict          # getekend, met exp
    def read_callback(self, body: bytes, authorization: str | None) -> CallbackEvent   # TokenError
    def callback_reply(self, outcome: str) -> tuple[int, dict]   # saved|same|copy|ignored → (200, {"error":0}) …
    def check_download(self, authorization: str | None, url: str) -> None              # TokenError
    async def health(self) -> Health
    async def enforces_tokens(self) -> bool                  # zonder token: version → 6, converter → -8
    async def present(self, key: str) -> list[str] | None     # None: de editor kent de key niet
    async def checkpoint(self, key: str, userdata: str) -> Literal["sent", "no_changes", "unknown"]
    async def drop(self, key: str, ds_uids: Sequence[str] | None) -> None   # None: iedereen
    async def convert(self, src_url: str, ext: str, key: str, title: str, out: Path, limit: int) -> None
    async def forgotten(self) -> list[str]
    async def fetch_forgotten(self, key: str, out: Path, limit: int) -> tuple[str, int]   # (sha, grootte)
    async def delete_forgotten(self, key: str) -> None
def current() -> Engine | None                             # None als office niet ingesteld is

# app/office/oo/__init__.py: class OnlyOffice(Engine), gebundeld uit oo/paths.py en oo/stream.py
#   (O1), oo/editor.py (O2), oo/convert.py (O3) en oo/admin.py (O0, af); O0 zet de rest als stubs.

# app/office/oo/admin.py (O0, af; ±30 regels op DocServer.command)
async def healthcheck() -> bool ; async def version() -> str ; async def license() -> int | None
async def forgotten_list() -> list[str] ; async def forgotten_url(key: str) -> str
async def delete_forgotten(key: str) -> None ; async def tokens_enforced() -> bool

# app/office/oo/tokens.py (alles HS256 met settings.office_secret; speling 60 s)
def sign(settings, payload: dict, minutes: int = 5) -> str
def browser_token(settings, config: dict, minutes: int = 120) -> str   # hele config zonder "events"
def command_body(settings, params: dict) -> dict              # {**params, "token": sign(params)}
def callback_data(settings, body: dict, authorization: str | None) -> dict
    # body["token"] eerst (platte velden), anders de header (velden onder "payload");
    # eist operation == "callback"; TokenError bij elke fout
def check_download(settings, authorization: str | None, url: str) -> None
    # eist operation == "download" en payload.url == url (precies)
def document_key(settings, token: str) -> str | None         # config- of sessietoken: document.key
def ds_user_id(settings, user_id: uuid.UUID) -> str

# app/office/oo/client.py (de enige weg naar DS)
class DocServer:
    async def healthy(self) -> bool                           # GET /healthcheck == "true"
    async def command(self, c: str, *, signed: bool = True, **params) -> dict   # POST /command?shardkey=<key>
    async def convert(self, *, signed: bool = True, **params) -> dict           # POST /converter?shardkey=<key>, Accept: application/json (A18)
    async def fetch(self, url: str, dest: Path, limit: int) -> tuple[str, int]   # al herschreven URL

# app/office/urls.py
def internal_url(settings, path: str) -> str                  # settings.office_app_url + path
def rewrite(settings, url: str, *, key: str, kind: Literal["data", "conv", "forgotten"]) -> str
    # alleen pad en query; het pad moet passen (Keuze 13); anders ValueError

# app/office/inspect.py (Keuze 23; in een thread, alleen zipfile en zlib)
@dataclass(frozen=True)
class Verdict: ok: bool; reason: str | None     # too_big | ratio | entries | method | encrypted | time
async def check(settings, path: Path, ext: str, sha: str) -> Verdict   # een uur in het geheugen per sha

# app/office/health.py (de enige gezondheidslus; schrijft OfficeState)
async def start(app) -> None
    # elke office_health_seconds: healthcheck; bij een overgang naar gezond en elke 10 min:
    # version, license, tokens_enforced; bij "weer gezond": convert.on_healthy(), recovery.kick()

# app/office/state.py (op app.state.office; één proces)
class OfficeState:
    health: Literal["off", "starting", "ok", "down", "misconfigured"]; down_since: datetime | None
    version: str | None; license_connections: int | None; tokens_enforced: bool | None
    tunnels: dict[str, dict[uuid.UUID, int]]   # key → gebruiker → open tunnels (O1)
    peak: tuple[int, datetime] | None
    def node_lock(self, node_id: uuid.UUID) -> asyncio.Lock
def office_state(app) -> OfficeState

# app/office/runtime.py: bind(app) in de lifespan; app() voor de tunnel (die is geen ASGI)

# app/office/grants.py (conversiebronnen, in het geheugen, 10 minuten)
def grant(sha: str) -> str ; def resolve(grant_id: str) -> str | None

# app/office/gate.py (signatuur; O1 vult check en forget_user in)
GATE_COOKIE = "office"
def issue(response: Response, settings, user: User, family: uuid.UUID | None, https: bool) -> None
    # app-geheim, typ "office_gate", 2 uur, HttpOnly, SameSite=Strict, Path=/office/, Secure bij https
async def check(headers: Headers, key: str | None = None) -> GateUser | None
    # cookie, gebruiker, family; met key ook lid, niet banned, sessie niet dicht, schrijfrecht van nu
def forget_user(user_id: uuid.UUID) -> None

# app/office/sessions.py (O0-stub; O2)
async def find_by_key(session, key: str) -> OfficeSession | None
async def busy(session) -> bool                              # een sessie die opent of opslaat

# app/office/saving.py (O0-stub; O2)
async def store(row: OfficeSession, tmp: Path, sha: str, size: int, event: CallbackEvent) -> str
    # "saved" | "same" | "copy" | "ignored" | "retry" | "refused"

# app/office/sweep.py (O0-stub; O2)
async def start(app) -> None ; async def checkpoint_all(wait: float) -> int

# app/office/recovery.py (O0-stub; O2)
async def start(app) -> None ; def kick() -> None
async def run_once() -> int ; async def restore(key: str) -> Node ; async def forget(key: str) -> None

# app/office/convert.py (O0-stub; O3)
async def on_preview_done(session, blob: Blob) -> None ; async def on_discard(blobs, sha: str) -> None
def on_healthy() -> None

# app/routers/files.py: place_file krijgt twee optionele parameters (Keuze 16), en zet
#   pdf_state = "ask" op een nieuwe blob als actor None is (een upload via een link, Keuze 25)
async def place_file(..., only=None, exact: bool = False, squash: str | None = None, ...)

# app/previews.py: het adres draagt de PDF-generatie (Keuze 25)
def url(secret: bytes, sha: str, size: int, exp: int, v: int = 0) -> str   # "&v=<v>", mee getekend
```

### 3.4 API-contracten, gebeurtenissen en codes (frozen)

- De eindpunten van O2 tot en met O5 zoals in §2. Pydantic-modellen in
  `routers/office_contracts.py`. O0 zet stubs die 503 `office_off` geven.
- **Gebeurtenissen** (SSE, na de commit). Ontvangers worden bepaald op het moment van versturen:
  - `office.unsaved {node_id|null, name, reason: "no_room"|"too_large"|"owner_gone"|"error_status", final: bool}`
    naar de eigenaar en de deelnemers die **nu** nog schrijfrecht hebben;
  - `office.recovered {node_id|null, copy_id, name, reason: "status3"|"no_writer"|"forgotten"}`
    naar wie de kopie nu kan zien: de eigenaar, plus wie via een deling bij die map kan
    (`access.watchers`). Nooit naar wie eruit gezet is;
  - `office.dropped {node_id, key, reason: "access"|"stale"}` alleen naar wie eruit gezet is, of
    (bij `stale`) wiens editor een oude key had (Keuze 15). Zonder naam;
  - `office.presence {node_id, count}` na elke status 1, naar de deelnemers die nu schrijfrecht
    hebben;
  - `affectsFolder` negeert ze. `node.changed` komt al uit `place_file`.
- **Nieuwe foutcodes**, allemaal in `web/src/i18n/errors/office.ts` (nl en en): `office_off`,
  `office_unavailable`, `office_format`, `office_too_large`, `office_unsafe`, `office_busy`,
  `office_too_many` {max}, `office_gate`, `office_token`, `office_convert_failed` {code},
  `pdf_pending`, `office_owner_gone`, `office_misconfigured`. Bestaande codes blijven:
  `not_found`, `read_only`, `trashed`, `not_a_file`, `album`, `no_room`, `folder_not_found`,
  `guest`, `rate_limited`.
- **Meldingscodes** voor `NOTICES`: `office_down`, `office_misconfigured`, `office_version`,
  `office_limit`, `office_unsaved`, `office_convert_failed`.

### 3.5 Hotspots die O0 zelf aanpast

- **`serve.py`**:
  - `ws=Tunnel` op de publieke luisteraars. De kern van O0 geeft door zonder toegangscontrole, maar
    alleen met `office_dev_ungated`; anders 404 op elke upgrade. Het commentaar "de app heeft geen
    websockets" wordt "alleen de tunnel naar office (Keuze 6)";
  - **de fix van M1**: `HeaderDeadline.handle_websocket_upgrade` zet de deadline uit en de vlag
    `_upgraded`; `_update_deadline` doet daarna niets meer;
  - een derde `Server` voor `internal_app` op `office_internal_port` (alleen http, zonder lifespan,
    `ws="none"`), alleen als `office_configured`; hij stopt als laatste;
  - bij het stoppen: eerst `checkpoint_all(wait=5)` (stub), dan `tunnel.close_all()` vóór
    `events.hub.close()`, dan zoals nu.
- **`main.py`**:
  - `app.add_middleware(OfficeProxy)` binnen `JsonCompression` en `Edge`, vóór de catch-all;
  - `SECRET_PARAMS` wordt `([?&](?:token|sig|key|md5|shardkey|cmd)=)`; `cmd` loopt door tot `&`
    of witruimte, ook over aanhalingstekens heen (spike A6);
  - de routers van office; `internal_app` met de interne routers en `internal_app.state = app.state`;
  - in `lifespan`, alleen als office ingesteld is: `OfficeState`, `runtime.bind(app)`, en drie
    taken: `health.start` (O0, af), `sweep.start` en `recovery.start` (stubs, O2 vult ze);
  - de catch-all geeft 404 voor `office/…` en `internal/…`, ook als de proxy uit staat.
- **`cli.py`**: `db-downgrade <revisie>` (Alembic `downgrade`, met een vraag om bevestiging, of
  `--ja`), en `office-checkpoint [--wait 30]`, dat `sweep.checkpoint_all` aanroept (de stub geeft 0).
- **De kern van proxy en tunnel** (`app/office/proxy.py` en `tunnel.py`, daarna van O1): http
  doorgeven met httpx (streamen in beide richtingen), bytes pompen in de tunnel, het prefix eraf en de
  `X-Forwarded-*`-koppen. Nog zonder padbeleid en toegangscontrole, en daarom alleen met
  `office_dev_ungated`. Zo kunnen de e2e van O2 en de spike aan het eind werken, en is de basis toch
  nooit open.
- **`routers/files.py`**: alleen `exact`, `squash` en `pdf_state = ask` in `place_file`, met tests.
- **`previews.py`**: twee haakregels, en het deel `v`. In `_store` vóór de commit `await
  convert.on_preview_done(session, blob)`; in `discard` `await convert.on_discard(blobs, sha)`. O3
  vult ze. `url()` en `info()` krijgen `v` (de `pdf_version` als `pdf_state` `ready` is, anders 0).
- **`routers/admin.py`**: in `system_info` de meldingen van `office.monitor.notices(...)` (stub: `[]`).
- **`pyproject.toml`** en **`package.json`**: ongewijzigd.
- **Web**:
  - `routes.tsx`: `route("/bewerken/:id", () => import("./office/EditorPage"), ["office"],
    "office.title", null)`;
  - `pwa/routing.ts`: `NEVER` krijgt `office(\/|$)`;
  - `vite.config.ts`: `"/office": { target: "http://localhost:8000", ws: true }`, en de
    dev-opdracht van Keuze 33 in `docs/fase-4/e2e.md`;
  - de haken: `browser/actions.ts` (`edit` met sneltoets `e`, `pdf` in groep `convert`, de
    beschikbaarheid uit `office/actions.ts` en `office/pdf.ts`), `Files.tsx` (menuknop "Nieuw" met
    Map plus `newItems()`, sneltoets `o`), `viewer/Viewer.tsx` (Bewerken en PDF in de werkbalk, `e`),
    `Beheer.tsx` (tabblad "Office", Alt+7), `App.tsx` (alleen de kleine luisteraar van Keuze 33);
  - het blok `office` in `i18n/areas/{browser,viewer,admin}.{nl,en}.ts`: elke tekst van die haken
    (§2.0);
  - `office/types.ts`, `office/api.ts` en `office/index.ts`: de typen en aanroepen van §2;
  - `i18n/types.ts`: gebied `office`; `i18n/errors/office.ts`.
- **CSP:** O0 verandert de CSP van de app niet (M4).

### 3.6 De nep-DS en het harnas

**`api/tests/fake_docserver/`**. Een kleine ASGI-app (FastAPI, al aanwezig) die het protocol van
DS 10.0 spreekt zoals gemeten. In pytest via httpx `ASGITransport`. In de e2e als proces: `python
-m tests.fake_docserver --port P --secret-file F --link-secret-file G --time-scale S`, met uvicorn
en `ws="websockets-sansio"` (testcode; alles staat al in de lock).

Hij is in drie delen gesplitst, zodat een gat erin niet de hele golf ophoudt:

- **de kern** (O0, frozen): `app.py`, `protocol.py`, `docx.py`, `static/api.js`,
  `static/editor.html`, `static/editor.js`, `recorded/*.json`, `__main__.py`. Tokens, callbacks 1,
  2, 4 en 6, `command` (`info`, `forcesave`, `drop`, `version`, `license`), de editorpagina met de
  socket, secure links, en `kill`, `stop` en `restart`;
- **forgotten files** (`forgotten.py`, O2);
- **conversie** (`convert.py` en `pdf.py`, O3).

| Onderdeel | Gedrag zoals de echte DS | Deel |
|---|---|---|
| `GET /healthcheck` | `true` (of `false` in de stand "start op") | kern |
| `/web-apps/apps/api/documents/api.js` | een kleine `DocsAPI.DocEditor(id, config)`. Controleert `config.token` (ook `exp`) en vergelijkt de getekende velden met de config. Maakt een iframe naar `<prefix>/<V>/web-apps/apps/documenteditor/main/index.html` met het prefix uit `X-Forwarded-Prefix`; zonder die kop klopt het adres niet en komt `onError` -4, net als bij de echte. Events `onAppReady`, `onDocumentReady`, `onDocumentStateChange`, `onError`, `onWarning` (-18 als de socket wegvalt), `onRequestClose`, `onRequestRefreshFile`; `destroyEditor()` en `refreshFile(config)`. `<V>` heeft de vorm `10.0.0-<32 hex>`. | kern |
| editorpagina | een `<textarea aria-label="Nep-editor">`, een knop Opslaan (forcesave type 1), een WebSocket naar `<prefix>/<V>/doc/<key>/c/?shardkey=<key>&EIO=4&transport=websocket`. Het eerste pakket is een CONNECT van socket.io met het token (`token`, of `session` na een eerste verbinding), dan een `auth`-bericht, in de vorm die de spike vastlegt. Tekst gaat live naar de andere sockets van dezelfde key. Verbindt opnieuw na een breuk (zoals de echte, met een kortere pauze). Na herverbinden op een key waarmee intussen is opgeslagen: `onRequestRefreshFile`. Geen inline script, geen console-fouten (de guard van de e2e kijkt ook in iframes). | kern |
| eerste opening van een key | haalt `document.url` op met `Authorization: Bearer` (`{payload: {url}, operation: "download"}`) en `User-Agent: DocumentServer`. Controleert `Repr-Digest` als die er is. Stuurt status 1 met `actions` type 1. | kern |
| callbacks | token in de body (platte velden, `operation`, `iat`, `exp` = +300 s) en in de header (`payload`-omhulsel, zonder `history`). Status 1 bij elke socket die opent of sluit; status 6 bij forcesave (knop: type 1, commando: type 0, met `userdata`) met `url`, `changesurl`, `history`, `filetype`, `users`, `lastsave`; status 2 na de laatste sluiter plus `--save-delay` (standaard 5 s, in tests 0,2 s), ook met `lastsave`; status 4 als er nooit iets veranderde. | kern |
| fouten van de app | 429 of 5xx: opnieuw na 1, 2 en 4 s (maal `--time-scale`, of `retries` uit de instellingen); `{"error": 1}`: niet opnieuw; beide daarna naar de forgotten files | kern (de forgotten files: O2) |
| `/cache/files/…` | links op de host van de aanvrager, met een echte `md5` over `expires + pad + linkgeheim`; fout 403, verlopen 403. De app moet de host dus herschrijven, net als bij de echte. | kern |
| `POST /command` | token in body of header, anders `error 6`; een config-token ook `error 6`. `info` (gebruikers, en een status-1-callback), `forcesave` (`error 4` zonder wijzigingen), `drop` (met of zonder `users`; zonder: iedereen eruit en status 2), `version` (`10.0.0.169`), `license` (instelbaar, standaard 2147483647); onbekende key → `error 1`. `drop` onthoudt niemand, net als de echte (M15): een gedropte browser kan opnieuw verbinden. | kern |
| `getForgottenList`, `getForgotten`, `deleteForgotten` | zoals gemeten (§4.3 van `editors.md`) | O2 |
| `POST /converter` | zonder `Accept: application/json` geen JSON; zonder token -8; bron ophalen met het download-JWT (anders -4); docx → doc -3, pptx → ppt -7; uitvoer een kleine geldige PDF met N pagina's (`pdf.py`, stdlib); `async`: eerst `{endConvert: false}`, daarna klaar; **dezelfde key geeft hetzelfde resultaat, ook voor een ander bestand**; een vlag om -5 te geven; elke conversie in de log, zodat een test kan zien dat een bestand nooit aankwam | O3 |
| opgeslagen bestand | docx: een nieuwe docx met één alinea per gebruiker (stdlib `zipfile`), zodat de test de inhoud kan lezen; xlsx en pptx: het origineel plus `customXml/fake.xml` met de tekst | kern |
| `/__fake/*` (alleen in de nep) | `log` (elk verzoek, zonder geheimen), `kill` (alles weg zonder callbacks, zoals `docker kill`), `stop` (status 2 voor alles binnen 1 s, zoals SIGTERM), `restart` (nieuw linkgeheim tenzij vast: oude links 403), `health`, `license`, `jwt-off` (neemt alles aan zonder token, voor de tokencontrole van Keuze 28) | kern |

`tests/test_fake_docserver.py` houdt de nep eerlijk. De veldnamen en de tokenvorm van zijn callbacks,
downloads en commando's moeten gelijk zijn aan die in `recorded/`: opgenomen uit de proeven
(`coedit-result.json`, `stub.log`) en uit de spike (de eerste socketpakketten), met tokens opnieuw
getekend en zonder geheimen. Elk deel heeft zijn eigen stuk van die test. De nep-DS is **geen**
vervanging van de echte; die draait in de job `office` (§4.5).

**`tests/office_contract.py`**: het testprotocol `Driver` en de scenario's van §4.4 (O0 schrijft ze,
O2 laat ze slagen).

**`e2e/office_harness.py`** en **`e2e/conftest.py`** (O0-e2e):
- fixture `office` (sessie): start de nep-DS als proces (vrije poort, geheimen in een tijdelijke map,
  stoppen op PID) en een eigen `Server` met `PLATFORM_OFFICE_URL`, `PLATFORM_OFFICE_SECRET`,
  `PLATFORM_OFFICE_APP_URL=http://127.0.0.1:<interne poort>`, `PLATFORM_OFFICE_INTERNAL_PORT`,
  `PLATFORM_OFFICE_SWEEP_SECONDS=1`, `PLATFORM_OFFICE_RECOVERY_SECONDS=2` en
  `PLATFORM_OFFICE_DEV_UNGATED=1` (na O1 doet die niets meer);
- fixture `real_office`: dezelfde server, maar met `E2E_REAL_DS` (de URL van een echte DS) en
  `PLATFORM_OFFICE_INTERNAL_HOST=0.0.0.0`. De fixture kiest zelf de interne poort en zet
  `PLATFORM_OFFICE_APP_URL=http://host.docker.internal:<die poort>`. Zonder `E2E_REAL_DS` wordt de
  test overgeslagen;
- de guard krijgt de optie `office_frames="record"`: meldingen uit frames onder `/office/` worden
  bewaard en als bijlage geschreven, maar laten de test niet falen. Alleen voor tests met marker
  `office_real`. Met de nep-DS faalt alles zoals nu;
- hulpjes: `frame_editor(page)`, `type_in_editor(page, text)` (echte DS: klik in `#id_main_view` in
  de iframe `frameEditor` en `keyboard.type` [gemeten]), `wait_callback(server, key, status)`,
  `fake_info(key)` (de gebruikers op een key, uit de nep-DS), `docx_text(bytes)`;
- `e2e/fixtures/office/`: `proef.docx`, `proef.xlsx`, `proef.pptx`, `proef.doc`, `proef.xls`,
  `proef.ppt` en `proef.rtf`, eenmalig gemaakt met LibreOffice in de sandbox (`/usr/bin/soffice`),
  met een `README.md` over hoe. CI maakt ze niet. Ook de API-tests gebruiken ze;
- `e2e/pytest.ini`: markers `office` en `office_real` (`--strict-markers` blijft).

**De job `office` in CI** (`.github/workflows/ci.yml`, O0-e2e): zie §4.5. In O0 draait hij één
rooktest (§4.5 punt 0: `api.js` laadt via `/office/`, `/healthcheck` is `true`, een conversie van
een docx lukt, en `version` zonder token geeft `error 6`). Elk pakket voegt zijn eigen `*_real.py`
toe.

### 3.7 De spike met de echte DS (O0-spike, dag 1)

Met de echte DS 10.0.0.1 in de sandbox, via de proxy, de stub en de scripts van de proef
(`fase4/proef/proxy.py`, `stub.py`, `tools/coedit.py`). Die werkten al (feasibility §3d). De spike
wacht dus niet op de kern in de app. Aan het eind van O0 doet O0-e2e stap 2, 5 en 11 opnieuw via de
kern in de app. M1, M3, M4, M5 en M6 zijn al gemeten; die horen er niet meer bij. Uitslag in
`docs/fase-4/spike.md`. Daarna ligt het vast.

1. **Padinventaris:** koud en warm openen, typen, Opslaan, downloaden als PDF en docx, afdrukken,
   afbeelding uit een bestand invoegen, een opmerking, sluiten. Elke methode en padklasse → §3.10
   wordt de vaste lijst. Welke paden zijn HTML?
2. **Cookie bij elk verzoek:** stuurt elk dynamisch verzoek (ook uit workers en de service worker
   van DS) het poort-cookie mee?
3. **Knop Opslaan:** geeft hij status 6 met `forcesavetype 1`?
4. **Opnieuw proberen:** werkt `callbackBackoffOptions.retries: 8` via `local-production-linux.json`?
   Geldt het ook bij "verbinding geweigerd" (de app gestopt vlak voor status 2), niet alleen bij 5xx?
5. **Herstart van de app terwijl iemand bewerkt:** verbindt de editor vanzelf opnieuw door de tunnel
   (50 pogingen, M14)? Komt `onRequestRefreshFile`? Gaat er iets verloren? Komt status 2 later toch
   binnen?
6. **Configsleutels in 10.0:** `customization.close` (en of `text` nodig is) met `onRequestClose`,
   `uiTheme`, `macros`, `macrosMode`, `plugins`, `help`; werkt `aiSettings.enable: false`? Gebruikt
   DS Alt+Shift+Q zelf?
7. **`Repr-Digest`** op `/internal/office/doc/{key}`: neemt DS hem aan?
8. **Eigen lege bestanden:** de xlsx en pptx van Keuze 24 openen in edit-modus en zetten om naar PDF.
9. **De volledige CSP van Keuze 9** op de HTML van DS: werkt de editor nog, met alle acties van
   stap 1? Ook zonder `'unsafe-eval'`?
10. **Status 3** proberen op te wekken (bijvoorbeeld een kapotte `changesurl`). Wat staat er in `url`?
11. **Tijden via de app:** koud en warm openen via proxy en tunnel, tegen 4,1 s via nginx in de proef.
12. **Het eerste socketpakket:** de frames van de browser na de upgrade opnemen. Staat het token in
    het CONNECT-pakket (`token` of `session`), in het `auth`-bericht, of in beide? Welk veld heeft
    de key? Dat wordt `recorded/socket.json` en de vorm voor `oo/stream.py`.
13. **Het merk:** is `lastsave` bij status 6, 6, 2 steeds hoger? Zo niet: is `history.changes` dat
    wel?
14. **Na een `drop`:** verbindt een browser (met een script) opnieuw met zijn sessietoken, en neemt
    DS hem aan? En een socket op het pad van key A met een token van key B? (Verwacht: ja en ja.
    Dat is waarom Keuze 6 en 7 de toegang in de app leggen.)
15. **Het sessietoken:** met `token.session.expires: "2m"` een editor 5 minuten open laten. Vernieuwt
    DS het token (`sendDataRefreshToken`, M15), en overleeft een herverbinding na 3 minuten? Zo ja:
    `"2h"` in `local-production-linux.json`. Zo nee: de standaard blijft.
16. **Conversie en editor tegelijk:** de docx van 401 pagina's omzetten, en tegelijk een editor
    openen en `/healthcheck` vragen. Hoe lang duren die? Wachten ze op de conversie?
17. **`Sec-Fetch-Dest`** via `localhost` (dat is een veilige context, M16): welke waarde hebben de
    iframe van de editor, de frames binnen DS en de service worker?
18. **`inputLimits` voor ODF** via `local-production-linux.json`: geeft een odt boven de grens een
    fout?

Elke uitkomst heeft al een terugval in dit ontwerp. Geen enkele uitkomst vraagt een andere
architectuur, behalve als stap 1 een pad laat zien dat de editor nodig heeft en dat we niet willen
doorgeven. Dan beslist de integratie, met een notitie. Laat stap 12 geen leesbaar token zien, dan
geldt de terugval van Keuze 20 (de sessie stoppen).

### 3.8 Klaar als (O0)

- Alle bestaande tests groen; ruff, tsc, build en budget schoon (de eerste lading groeit hooguit
  0,5 KB).
- `test_migration_0007.py`: een database van 0006 met data → upgrade → tabel, kolommen en indexen er
  → twee open sessies voor één bestand geven een `IntegrityError` → downgrade → de database is
  gelijk aan een verse 0006 (tabellen, kolommen, indexen, `alembic_version`).
- `test_cli_db.py`: `db-downgrade 0006` vraagt om bevestiging en doet daarna precies dat.
- `test_office_tokens.py`: elke vorm uit §3.9 heen en terug, en elke weigering. Een office-token werkt
  niet als app-token en omgekeerd. `document_key` leest een config- en een sessietoken.
- `test_place_file_exact.py`: `exact` na hernoemen, verplaatsen en prullenbak; 404 als het weg is;
  `squash` alleen bij de juiste sha; de oude blob is daarna vrij voor `collect_garbage`; een nieuwe
  blob zonder actor krijgt `pdf_state = ask`.
- `test_office_inspect.py`: een gewone docx, xlsx, pptx, odt en de lege bestanden van O4 gaan door;
  een bom (klein, gigabytes uitgepakt), een zip die over zijn grootte liegt, te veel onderdelen,
  een vreemde methode, iets versleuteld en een bom in een ingebedde zip niet; een pptx met 80 MB
  foto's wel; doc boven 20 MB niet; de tijdgrens; de cache per sha. De inhoud beslist: een zip,
  een leeg bestand of XML met de naam doc, xls, ppt of rtf krijgt `format`; XML onder een andere
  naam (`word/main.dat`, met BOM, UTF-16) telt mee; de grens van een werkblad alleen voor een
  echt werkblad; ODF de strengste grens.
- `test_office_health.py`: gezond ↔ weg na 2 minuten; `version`, `license` en de tokencontrole bij
  een overgang naar gezond; de nep-DS met `jwt-off` → `misconfigured`; een ander geheim dan de
  nep-DS → `misconfigured`, nooit `down`; `starting` telt niet mee voor de 2 minuten van
  `office_down`; `on_healthy` en `kick` worden aangeroepen.
- `test_serve_upgrade.py`: een geüpgradede verbinding blijft open na `HEADER_TIMEOUT` (in de test
  0,5 s), met h11 **en** httptools (M1); een stille gewone verbinding sluit nog steeds; zonder
  `office_dev_ungated` geeft elke upgrade 404; de interne poort kent alleen `/internal/office/…`; de
  publieke poort geeft 404 op `/internal/…`.
- `test_office_imports.py`: buiten `app/office/oo/` importeert alleen `engine.py` de adapter.
- `test_fake_docserver.py` (de kern) is groen; de nep-DS draait in pytest en als proces.
- De job `office` in CI draait zijn rooktest tegen de echte DS en is groen.
- De spike is gedaan; `docs/fase-4/spike.md` legt de lijst vast.
- Zonder office-instellingen is de app precies fase 3 (de bestaande e2e bewijst het).
- `docs/fase-4/README.md` en B31 en verder in `docs/fase-0/beslissingen.md` (voorstel) kloppen nog
  met wat er gebouwd is.

### 3.9 Elk verzoekpad, met token en controle

| # | Van → naar | Pad | Token | Wie controleert wat |
|---|---|---|---|---|
| 1 | browser → app | `/api/v1/office/…` (`status`, `files/{id}`, `session`, `save`, `gate`, `new`, `convert`, `pdf`) | access-token van de app (Bearer) | `CurrentUser` (2FA), toegang via `access.py`, rate limit 30/min per gebruiker op `session`, 10 per 10 min op `convert` en `pdf` als er omgezet moet worden |
| 2 | app → browser | antwoord van `session` | `config.token`: HS256, office-geheim, hele config, `exp` 2 uur; plus cookie `office` | DS controleert de config bij openen [doc] |
| 3 | browser → app → DS | `/office/<statisch>` (geen HTML) | geen | padbeleid (alleen GET en HEAD, vaste klassen); `Cross-Origin-Resource-Policy: same-origin` |
| 4 | browser → app → DS | `/office/<…>.html` (editorpagina) | cookie `office` | app: cookie (SameSite=Strict); `Sec-Fetch-Dest` alleen `iframe`, `frame` of `empty` als de kop er is; de volledige CSP op het antwoord |
| 5 | browser → app → DS | `/office/cache/files/data/<key>_…` | cookie `office` (app-geheim) + `md5`/`expires` van DS | app: cookie, lid, niet `banned`, sessie open, schrijfrecht van nu (15 s cache); DS: secure link [gemeten] |
| 6 | browser → app → DS | upgrade `/office/<V>/doc/<key>/c/` (tunnel) | cookie `office`; daarna het config- of sessietoken in het eerste socketpakket | tunnel: `Origin`, toegang als #5, grenzen per gebruiker en key; dan het eerste pakket: elk JWT met het office-geheim en `document.key` = de key van het pad; elke 15 s opnieuw; DS: het token |
| 7 | browser → app → DS | `/office/<V>/<editoractie>` | cookie `office` | app: cookie; met een key toegang als #5; zonder key lid van een open sessie; POST: grens per gebruiker; DS: zijn eigen sessie |
| 8 | DS → app (poort 8081) | `GET /internal/office/doc/{key}` | `Authorization: Bearer`, `{"payload": {"url"}, "operation": "download"}` | app: handtekening, `exp`, `operation`, `payload.url` == de URL; sessie niet dicht |
| 9 | DS → app (poort 8081) | `POST /internal/office/callback` | body-`token` (plat) en/of header (onder `payload`), `operation: callback`, 5 min | app: handtekening, `exp`, `operation`; alleen velden uit het token; key bekend; het merk; de regels voor een dichte sessie |
| 10 | app → DS | `GET` de `url` uit de callback | `md5`/`expires` van DS | app: alleen pad en query, onze key, host = `office_url`; grens van Keuze 16 |
| 11 | app → DS | `POST /command?shardkey=<key>` | body-`token`, payload = parameters | DS [gemeten: `error 6` zonder]; de app controleert dat steeds opnieuw (Keuze 28) |
| 12 | app → DS | `POST /converter` | body-`token`, payload = parameters | DS [gemeten: -8 zonder]; idem; alleen na `inspect` |
| 13 | DS → app (poort 8081) | `GET /internal/office/src/{grant}` | als #8 | app: als #8, en de grant bestaat nog (10 min) |
| 14 | app → DS | `GET` de `fileUrl` of de `getForgotten`-URL | `md5`/`expires` | app: als #10 (`conv_<key>` of `forgotten/<key>`) |
| 15 | app → DS | `GET /healthcheck` | geen | intern netwerk |

Vanaf internet zijn alleen #1 en #3–#7 bereikbaar. #8, #9 en #13 staan op een poort die niet
gepubliceerd is. #10–#12, #14 en #15 gaan van de app naar DS over het interne netwerk.

### 3.10 Padbeleid van `/office/` (definitief na de spike)

De spike heeft de lijst vastgelegd (stap 1, 2 en 17 van `docs/fase-4/spike.md`; opname in
`api/tests/fake_docserver/recorded/paths.json`). Na normaliseren (prefix `/office` eraf,
versiesegment `^\d+\.\d+\.\d+-[0-9a-f]{8,64}$` eraf). "Gemeten" = gezien in de runs van de spike;
"niet gemeten" = afgeleid, nog niet gezien.

| Klasse | Paden | Methoden | Toegang | Bron |
|---|---|---|---|---|
| statisch | `web-apps/…`, `sdkjs/…`, `sdkjs-plugins/…`, `fonts/…`, `dictionaries/…`: alles behalve HTML (gezien: .js, .css, .json, .template, .svg, .png, .wasm, .bin, .aff, .dic, fonts zonder extensie); `document_editor_service_worker.js`; `web-apps/apps/api/documents/api.js` (ook zonder `<V>`) | GET, HEAD | geen | [gemeten, spike stap 1] |
| editorpagina | precies `web-apps/apps/{documenteditor,spreadsheeteditor,presentationeditor}/main/index.html`; daarbij `…/mobile/index.html` voor `type: "mobile"` | GET, HEAD | cookie; `Sec-Fetch-Dest` `iframe`, `frame` of `empty` als de kop er is; volledige CSP | [`main` gemeten; `mobile` niet gemeten (api.js maakt dat pad)] |
| documentdata | `cache/files/data/<key>_<n>/…` (uitvoer van downloaden en afdrukken) **en** `cache/files/data/<key>/…` (`Editor.bin/Editor.bin`, `media/<bestand>/<bestand>`) | GET | cookie + lid + recht van nu | [gemeten] |
| sessie | `doc/<key>/c/…` | upgrade (tunnel); GET en POST alleen voor polling | cookie + lid + recht van nu; tunnel: ook de pakketten (spike stap 12) | [upgrade gemeten; polling niet gezien] |
| editorinstellingen | `themes.json`; `plugins.json` | GET | cookie + lid van een open sessie | [`themes.json` gemeten; `plugins.json` niet gevraagd met `plugins: false`] |
| editoracties | `downloadas/<key>` (downloaden en afdrukken; query `cmd` en `shardkey`; body tot ±250 KB); `upload/<key>` (afbeelding; `Authorization: Bearer <sessietoken>` door naar DS) | POST | cookie + lid + recht van nu; grens per gebruiker | [gemeten] |
| geweigerd (404) | al het andere, met name: `/`, `index.html`, `<V>/index.html`, `welcome/…`, `web-apps/apps/api/documents/preload.html` en `cache-scripts.html`, `printfile/…`, `savefile/…`, `downloadfile/…`, `command`, `coauthoring/…`, `converter`, `converter/from-file`, `ConvertService.ashx`, `docbuilder`, `baseurl`, `dummyCallback`, `info/…`, `internal/…`, `hosting/…`, `example/…`, `meta/…`, `healthcheck`, `version`, `ai-proxy`, `robots.txt`, `cache/files/forgotten/…`, `cache/files/data/conv_…`, `favicon.ico` | — | — | [gemeten: DS en de spike-proxy] |

Toelichting:
- `printfile`, `savefile` en `downloadfile` bestaan in DS, maar de editor gebruikte ze in geen
  enkele actie (afdrukken gaat via `downloadas/<key>` en `output.pdf`). Ze blijven dicht; vraagt de
  integratie er toch om, dan beslist zij met een notitie.
- `Authorization` gaat alleen op `upload/<key>` door naar DS, en alleen als het een geldig
  sessietoken is (afgeleide sleutel, Keuze 11) met `document.key` = de key van het pad. Op alle
  andere paden blijft het eruit.
- De upgrade heeft nooit `Sec-Fetch-Dest`; de tunnel werkt zonder die kop (`Origin` en cookie).
- `cmd` en `shardkey` gaan nooit in een log (`SECRET_PARAMS`).
- `<V>` is per container anders en wisselt bij de font-herstart van een nieuwe container: alleen de
  vorm controleren, nooit vastpinnen.

---

## 4. Tests en meten

### 4.1 Vier lagen

| Laag | Waartegen | Waar | Poort |
|---|---|---|---|
| Unit | pure functies: tokens, keys, herschrijven, padbeleid, het eerste socketpakket, de invoercontrole, regels, lege bestanden | pytest, vitest | hard, in elke CI-run |
| API | de app met de nep-DS in hetzelfde proces | pytest | hard |
| e2e | de echte server, Chromium, de nep-DS als proces | job `e2e` (`-m "not perf"` neemt `office` mee) | hard |
| Echte editor | de echte server, Chromium, DS 10.0.0.1 in Docker | job `office`, `-m office_real`, **vanaf O0** | hard |

Daarnaast:
- `test_install_env.sh` in de job `infra` (hard);
- `test_install_update.sh` en de metingen (§4.7) in de sandbox, bij de integratie (rapport).

### 4.2 Unit en API

**Unit** (zonder netwerk):
- tokens in elke richting en elke weigering (§3.9), ook `alg: none`, HS512, een token van de app zelf,
  een uitgaand token van DS dat als inkomend terugkomt, en een `config.token` zonder of met een
  verlopen `exp`; `document_key` uit een config- en een sessietoken;
- keys: vorm, uniek over 10.000 sessies, hergebruik, een nieuwe key na sluiten;
- URL herschrijven: alleen ons pad en onze key, geen host, geen userinfo of poort van buiten, geen
  `..`, de query blijft;
- padbeleid (alle gemeten paden, de weigerlijst, padtrucs): elke `*.html` is `PAGE`; `PAGE` zonder
  cookie of met `Sec-Fetch-Dest: document` geeft 404; de koppen van de proxy, met
  `Cross-Origin-Resource-Policy` en de volledige CSP;
- het eerste socketpakket (`oo/stream.py`): token van de eigen key door, van een andere key dicht,
  ander geheim dicht, in stukken, te groot, te traag;
- de invoercontrole (`inspect.py`): bommen, een zip die over zijn grootte liegt, te veel
  onderdelen, vreemde methoden, versleuteld, een bom in een ingebedde zip, de tijdgrens; een pptx
  met 80 MB foto's en de lege bestanden van O4 gaan door; doc boven 20 MB niet;
- het herschrijven van de upgrade-request in de tunnel (bytes in, bytes uit);
- de regels van `policy.py` als tabellen: `make_version` (ook de grens van 10 per sessie),
  `choose_actor` (nooit iemand uit `banned`), `target`, `late`, `recovery_name`;
- `place_file` met `exact`, `squash` en `pdf_state = ask`;
- `previews.url` met `v`, en de ETag van een pagina met `p<v>`;
- lege bestanden: vaste bytes, geldige zip, leesbaar voor B21;
- web: de statusovergangen van de editorpagina, `onRequestRefreshFile`, het opnieuw proberen bij
  `office_busy`, wanneer Bewerken er is, de items van "Nieuw", de opmaak van de beheerwaarden, en
  dat de luisteraar in `App.tsx` niets laadt vóór de eerste `office.*`-gebeurtenis.

**API** (de nep-DS als ASGI-app): de lijsten per pakket in §2. Samen dekken ze:
- de rechtentabel (Keuze 22), en dat elk dynamisch pad naar DS het recht van nu controleert
  (Keuze 7), met een cache van hooguit 15 s;
- elke rij van de tabel van Keuze 14 (wie welke key krijgt), ook een oude `pending` en een nieuwe
  rij in één transactie;
- elke status van de callback (Keuze 15) en elke vorm van het token; het merk (een oude opslag na
  een nieuwere schrijft niets); elke cel van de tabel van dichte sessies; een onbekende key;
- de grens op een download (Keuze 16), ook bij forgotten files;
- de grens van 10 versies per sessie (Keuze 17): 25 tussenstanden met 31 minuten ertussen laten de
  versie van vóór de sessie staan;
- elke rij van Keuze 19, de ronde (Keuze 20: `banned`, de sessie stoppen, `saving` te oud) en het
  herstel (Keuze 21);
- de ontvangers van `office.recovered`, `office.unsaved` en `office.dropped` (§3.4): nooit iemand
  die zijn recht kwijt is;
- SSRF: een `httpx.MockTransport` die faalt op elke host behalve `PLATFORM_OFFICE_URL`;
- de interne routes met en zonder het JWT van DS;
- conversie: de invoercontrole vóór elke aanroep, `ask` voor uploads via een link en boven 20 MB,
  de grens per gebruiker, de valkuil van de cache-key, `max_attempts = 2` met `on_dead`, wachten
  op sessies en op het slot zonder poging, de foutcodes, de caches van thumbnails en pagina's, en
  de terugval op B21;
- proxy en tunnel met echte sockets, met de grenzen per gebruiker en per key;
- de gezondheid, de tokencontrole (de nep-DS met `jwt-off`) en de bewaking met een eindige
  licentie;
- DS weg tijdens openen, tijdens opslaan en tijdens een conversie;
- de migratie heen en terug, en `db-downgrade`;
- `test_error_catalog.py` voor alle nieuwe codes.

### 4.3 De volgorde van bewijzen

Elke bewering in "Klaar als" heeft eerst een unit- of API-test, dan een e2e met de nep-DS, en
waar het om de editor zelf gaat, een test met de echte DS. Die laatste draait vanaf O0 in CI. Elk
pakket bewijst zijn deel dus met de echte DS **vóór** het samenvoegen, niet pas bij de integratie.
Een test met de echte DS vervangt nooit een test met de nep: de nep draait bij elke PR, ook als
Docker Hub en de mirror een dag niet willen.

### 4.4 Contracttests en e2e met de nep-DS

**Contracttests** (`tests/office_contract.py`, O0; slagen na O2). Scenario's tegen een klein
testprotocol, los van OnlyOffice:

```python
class Driver(Protocol):            # nu één: de nep-DS van OnlyOffice
    async def open(self, user, node) -> Handle
    async def type(self, h: Handle, text: str) -> None
    async def save(self, h: Handle, explicit: bool) -> None
    async def close(self, h: Handle) -> None
    async def reconnect(self, h: Handle) -> bool     # zoals een browser na een breuk; False = geweigerd
    async def fail_callbacks(self, n: int) -> None   # de app geeft de volgende n callbacks 503 (testhaak)
    async def crash(self) -> None                    # kill zonder opslaan
    async def restart_app(self) -> None
```

Scenario's:
- twee gebruikers → één key → beide teksten in de opgeslagen versie;
- tussentijds opslaan binnen 30 minuten → geen extra versie; na 30 minuten → wel; na 10 versies in
  één sessie → geen meer;
- hernoemen, verplaatsen, prullenbak, upload ertussen, quotum vol;
- definitief verwijderd → niets geschreven, geen kopie;
- deling ingetrokken → eruit, en `reconnect` geeft `False`;
- een oude opslag die na de laatste aankomt (`fail_callbacks(1)` op de eerste tussenstand) → de
  laatste blijft;
- de app weg tijdens status 2, en een sessie die in `saving` blijft hangen → het herstel zet het op
  het origineel, en de volgende opener krijgt een nieuwe key;
- crash.

`pytest -k contract` draait ze voor elke `Driver`. Een tweede editor krijgt later dezelfde
scenario's. Zo is "de terugval is echt" een test, geen belofte.

**e2e met de nep-DS** (marker `office`, in elke CI-run). Via de echte server, de echte proxy en
tunnel, het echte poort-cookie en een echte Chromium. De harnas zet de ronde op 1 s
(`PLATFORM_OFFICE_SWEEP_SECONDS=1`) en het herstel op 2 s.

1. **Samen bewerken** (O2): Anna deelt een map met schrijfrecht met Bram. Ze openen allebei
   `/bewerken/<id>` in een eigen context. Beide editors klaar. Anna typt "Anna was hier"; Bram ziet
   het meteen (dat bewijst tunnel en meedoen op dezelfde key). `fake_info(key)` noemt twee
   gebruikers op die key: dat vraagt de test zelf aan de nep-DS, dus zonder O5. Bram typt. Bram
   drukt "Nu opslaan" → de versielijst heeft er één bij. Allebei sluiten → status 2 → nog steeds
   precies één nieuwe versie (binnen 30 minuten), en de docx bevat beide teksten.
2. **Randgevallen** (O2):
   - hernoemen tijdens het bewerken → opgeslagen in het hernoemde bestand;
   - prullenbak terwijl de eigenaar bewerkt → het bestand in de prullenbak heeft de nieuwe inhoud;
   - deling ingetrokken terwijl alleen Bram bewerkt → binnen enkele seconden uit de editor, met de
     melding. Anna krijgt `office.recovered`, en de herstelkopie staat in haar hoofdmap. Bram krijgt
     alleen `office.dropped`, zonder naam;
   - definitief verwijderd terwijl Bram bewerkt → hij is eruit, en er komt geen kopie.
3. **Rechten** (O1, met de editor van O2):
   - een `read`-gebruiker ziet geen Bewerken en krijgt 403;
   - uitgelogd → 401 op `cache/files` en een geweigerde upgrade;
   - uitloggen in een andere context sluit de open editor van die login binnen 30 s.
4. **Voorbeeld zonder editor** (O3): een docx en een doc uploaden → de thumbnail krijgt een nieuw
   adres en de viewer toont pagina's. De log van de nep-DS heeft geen enkele editorverbinding. Een
   odt-bom komt nooit bij de nep-DS.
5. **Nieuw document** (O4) met alleen het toetsenbord → de editor opent.
6. **DS weg en app weg** (O2): de nep-DS stoppen (op PID) → Bewerken geeft "niet bereikbaar", het
   voorbeeld blijft; weer starten → het werkt weer zonder de app te herstarten. Dan de app stoppen
   (op PID) terwijl Anna bewerkt: de nep-DS kan status 2 niet kwijt en zet het bij de forgotten
   files. De app weer starten → het herstel zet de inhoud in het bestand.
7. **Beheer** (O5): tabblad Office met Alt+7. Met twee open editors (zoals punt 1) toont het 2
   verbindingen. Grens op 1 → de tweede bewerker krijgt de melding. Een niet-opgeslagen document
   (quotum vol) terugzetten na het vrijmaken van ruimte.
8. **Publieke link** (O3): een docx toont pagina's (als de PDF er is), geen Bewerken, en nooit een
   upgrade naar `/office/`. Een upload via een uploadlink wordt niet omgezet, tot een ingelogde
   gebruiker met leesrecht erom vraagt.
9. **Toetsenbord en taal** (elk pakket zijn deel): E, O, Alt+Shift+Q, Nu opslaan, Sluiten; alles nog
   een keer in het Engels.
10. **Afscherming** (O1):
    - een context zonder cookie opent `/office/<V>/web-apps/apps/documenteditor/main/index.html` als
      pagina → 404;
    - een ingelogde context opent hetzelfde adres als pagina (`Sec-Fetch-Dest: document`; via
      `127.0.0.1` stuurt Chromium die kop wel, M16) → 404;
    - na het intrekken van de deling opent een script in de pagina van Bram een nieuwe socket met
      zijn oude sessietoken → geweigerd bij de upgrade;
    - een socket op het pad van key A met het token van key B → dicht na het eerste pakket.

### 4.5 De echte editor in CI (job `office`, marker `office_real`, vanaf O0)

Haalbaar volgens de proef: ±1,5–2 minuten extra per run. O0 maakt de job met één rooktest. Elk
pakket voegt zijn eigen `*_real.py` toe, en zijn PR moet door deze job.

```
1. docker pull mirror.gcr.io/onlyoffice/documentserver@sha256:ca17… (3 pogingen; anders docker.io)
2. docker run -d --name ds -p 127.0.0.1:18080:80 --add-host=host.docker.internal:host-gateway \
     -e JWT_SECRET=<per run> -e SECURE_LINK_SECRET=<per run> -e WELCOME_PAGE_ENABLED=false \
     -e PLUGINS_ENABLED=false <image>
   (als eerste stap, zodat de fonts van DS bouwen terwijl de web-app bouwt; na O6 ook
    -v infra/single/office/local-production-linux.json:…:ro)
3. web bouwen, Chromium zoals in de job e2e, wachten op /healthcheck == true (hooguit 6 minuten)
4. E2E_REAL_DS=http://127.0.0.1:18080 uv run --with playwright pytest ../e2e -m office_real
   (de fixture real_office kiest zelf de interne poort en zet PLATFORM_OFFICE_APP_URL op
    http://host.docker.internal:<die poort>, met PLATFORM_OFFICE_INTERNAL_HOST=0.0.0.0)
5. bij een fout: docker logs ds en de schermafdrukken als artefact
```

Tests:
0. **Rooktest** (O0): `api.js` laadt via `/office/`, `/healthcheck` is `true`, een conversie van
   een docx lukt, en `version` zonder token geeft `error 6`.
1. **Twee gebruikers** (O2): zie §4.6.
2. **Voorbeeld zonder editor** (O3): `proef.docx`, `.xlsx`, `.pptx`, `.doc` en `.rtf` uit
   `e2e/fixtures/office/` uploaden → binnen 60 s `pdf_state: "ready"` en pagina's in de viewer,
   zonder upgrade naar `/office/`.
3. **Nieuw docx, xlsx en pptx** (O4): openen zonder `onError`; sluiten zonder wijziging → status 4,
   geen versie; omzetten naar PDF lukt.
4. **Herstart van de app** (O2) terwijl Anna bewerkt: de server op PID stoppen en starten → opnieuw
   laden → wat ze typte, staat er. Via de pogingen van DS of via het herstel (Keuze 21).
5. **Afscherming** (O1): punt 10 van §4.4 tegen de echte DS, vooral de nieuwe socket na het
   intrekken en de socket met een token van een andere key.

Verschil met productie: in CI hangt DS aan de standaardbridge (met uitgang), niet aan het interne
netwerk. Proxy, tunnel, cookie en tokens zijn gelijk. Het interne netwerk bewijst §4.6 punt 3.

Deze job is een **harde poort**, net als de e2e (B29). Valt hij door een pull van het image, dan
opnieuw draaien; het image is op digest vastgepind.

### 4.6 Hoe "twee gebruikers bewerken tegelijk één docx" bewezen wordt

Drie bewijzen die elkaar dekken.

1. **Met de echte editor, in CI, als harde poort**
   (`test_office_o2_real.py::test_twee_gebruikers_een_docx`):
   1. Anna (eigenaar) en Bram (`write`-deling) in twee browsercontexts, elk ingelogd met 2FA.
   2. Beide openen `/bewerken/<id>` van dezelfde docx via de knop Bewerken. Bewijs van "tegelijk":
      allebei `onDocumentReady`, dezelfde key in beide configs, en `info` op die key noemt beide
      gebruikers. Die `info` stuurt de test zelf naar DS, getekend met het geheim van de run
      [gemeten in de proef]. Er is dus geen beheerpagina voor nodig.
   3. Anna typt `Anna-<willekeurig>`, Bram typt `Bram-<willekeurig>`, terwijl beide open zijn (klik in
      `#id_main_view` in de iframe `frameEditor`, dan `keyboard.type` [gemeten]).
   4. Bram drukt "Nu opslaan" (status 6). De docx van het bestand bevat **beide** woorden. De opslag
      van Bram heeft dus ook de tekst van Anna: echt samen bewerken, geen twee kopieën [gemeten in de
      proef].
   5. Beide sluiten. Binnen 30 s komt status 2 [gemeten: ±6 s].
   6. Bewijs van "opslaan maakt een nieuwe versie": de versielijst heeft precies één versie meer (de
      inhoud van vóór de sessie), `replaced_by` is Anna of Bram, en de huidige docx bevat beide
      woorden.
   7. Schermafdrukken van beide editors gaan als artefact mee. De uitslag komt in
      `docs/fase-4/klaar.md`.
2. **Met de nep-DS, bij elke PR** (§4.4 punt 1): onze kant (tunnel, één key per bestand, callbacks,
   versies, randgevallen) is bewezen, ook als Docker Hub of de job `office` een dag niet wil.
3. **Bij de integratie, via de echte installatie** in de sandbox (`test_install_update.sh`, §2 O6):
   een installatie uit fase 3 bijwerken, en dezelfde test 1 tegen `http://127.0.0.1:<poort>`. Dat
   bewijst ook wat CI niet kan:
   - het interne netwerk: `docker compose exec office curl -m 5 https://example.com` faalt, en de app
     bereikt DS wel;
   - de geheimen uit `.env`, en dat de app bij een fout geheim `office_misconfigured` meldt;
   - de stopvolgorde: `docker compose stop` met een open editor → alles opgeslagen.

   Uitslag in `docs/fase-4/integratie.md`.

### 4.7 Metingen (sandbox, bij de integratie; in `docs/fase-4/metingen.md`)

| Wat | Doel | Waarom |
|---|---|---|
| Koud openen via de app | ≤ 5 s (via nginx 4,1 s in de proef) | de prijs van de eigen proxy en tunnel |
| Warm openen | ≤ 2 s (5 verzoeken in de proef) | |
| Cpu van de app bij koud openen | ≤ 1 core-seconde | 253 verzoeken, 8,7 MB [gemeten] |
| Geheugen van de app met 50 tunnels | ≤ +20 MB | |
| Opslaan: callback tot versie in de database | ≤ 2 s voor 1 MB | |
| Invoercontrole: een xlsx met 10 MB XML, een pptx met 80 MB foto's | ≤ 1 s cpu, in een thread | Keuze 23 |
| XML-grens bij 2 GB opnieuw meten: docx en pptx 4 MB, odt, ods en odp 2 MB, xlsx 10 MB, ook met echte opmaak en formules en een document met heel veel pagina's | geen OOM van x2t; docservice blijft | spike A11 ("stap 18 verder") |
| Conversie klein / 401 pagina's | ±1 s / ≤ 60 s (22 s gemeten) | |
| Conversie van 401 pagina's terwijl een editor opent | openen ≤ 10 s; `/healthcheck` blijft `true` | Keuze 25, spike stap 16 |
| 20 bewerkers (5 documenten × 4), 60 s typen | alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd | basis voor `max_editors` (Keuze 27) |
| Stoppen met open editors | alles opgeslagen; `docker stop` ≤ 60 s | Keuze 29 |
| Kill van DS met open editors | verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min) | Keuze 18 |
| Budget: eerste lading | groeit ≤ 0,5 KB (nu 93,8 van 100 KB, M18) | Keuze 33 |
| Budget: editor en Bestanden | de editorchunk < 50 KB gzip; Bestanden groeit < 1 KB | Keuze 33 |

Op Jonas' LXC (handmatig, met de checklist in het runbook): `docker info` (driver, opslag van
images, limieten), het interne netwerk, `ss -ltn` op de LXC (M7), `docker image inspect` op de naam
van Docker Hub met digest (§5.1), de start van DS, één sessie met twee gebruikers.

---

## 5. Volgorde

1. **O0 Fundament, alleen** (±4 dagen).
   - Dag 1: O0-spike doet de spike (§3.7) met het gereedschap van de proef. Tegelijk beginnen
     O0-api en O0-web aan contracten, migratie, hotspots en de kern van proxy en tunnel.
   - Daarna O0-e2e: harnas, nep-DS als proces, fixtures en de job `office` met de rooktest.
   - Aan het eind stap 2, 5 en 11 van de spike opnieuw, nu via de kern in de app.
   - Poort: §3.8. Tag `fase4-basis`.
2. **Parallelle golf** (±5 dagen): O1 tot en met O6, elk in een eigen worktree vanaf
   `fase4-basis`. Elk levert één PR met tests (ook zijn `*_real.py` in de job `office`),
   `docs/fase-4/<pkg>.md` en een toetsenbord-checklist (waar het web raakt).
3. **Volgorde van samenvoegen: O2 → O1 → O3 → O4 → O5 → O6.**
   - **O2 eerst:** de editorpagina en `POST …/session` zijn nodig voor de e2e van O1, O4 en O5. De
     e2e van O2 draait op de kern van O0 met `PLATFORM_OFFICE_DEV_UNGATED=1`. Die vlag staat alleen
     in de testomgeving; zonder de vlag geeft de kern 404 op elke upgrade. De basis is dus nooit
     open.
   - **O1 meteen daarna:** dan doet `office_dev_ungated` niets meer, en moeten alle e2e van O2 ook
     met de echte toegangscontrole slagen. Dat controleert de PR van O1.
   - **O3** gebruikt `sessions.busy()` van O2. **O4** opent de editor van O2. **O5** leest de
     tellers van O1 en het herstel van O2.
   - **O6 als laatste:** `test_install_update.sh` heeft alles nodig. Loopt de rest uit, dan mag O6
     eerder. Zonder O1 geeft `/office/` toch alleen 404 en zegt de editorpagina "nog niet klaar".
   - Vóór elke merge rebasen. Daarna de volledige set: pytest (met de contracttests en
     `test_error_catalog`), ruff, `tsc -b`, vitest, build, budget, `check:i18n`, de e2e van fase 3,
     `-m office` en de job `office`.
4. **Integratie** (±2 dagen):
   - de e2e met de echte DS (§4.5) en het bewijs van §4.6 → `docs/fase-4/klaar.md`;
   - de echte installatie in de sandbox (`test_install_update.sh`, §4.6 punt 3) →
     `docs/fase-4/integratie.md`, ook het bijwerken van een `.env` van v0.5.0 en het terugrollen
     naar v0.5.0 met `db-downgrade`;
   - de metingen van §4.7, en `max_editors` daarop gezet;
   - `office_dev_ungated` weg uit `config.py` en de harnas;
   - `docs/fase-4/README.md` met wat er is en de bekende grenzen; de vinkjes van fase 4 in het
     stappenplan, en de regels over "eigen Postgres, Redis en RabbitMQ" en de limiet van 20
     bijgewerkt met de reden; B3 en B31 en verder definitief na de keuze van Jonas;
   - versie 0.6.0 in `pyproject.toml`, `app/__init__.py` en `package.json`.

**Eerlijk over de tijd.** O0 ±4 dagen, de golf ±5 dagen, de integratie ±2 dagen: samen **±11
werkdagen**. Dat is één dag meer dan de twee weken van het stappenplan. O0 is zwaarder dan in
revisie 1: de invoercontrole, de gezondheid met de tokencontrole, de dunne DS-aanroepen en de job
met de echte DS zitten er nu in. Dat maakt de golf korter en veiliger.

Loopt het verder uit, dan schuift dit als eerste naar later, in deze volgorde:
1. het tabblad Office op `/beheer` voorbij de meldingen en "Niet opgeslagen" (de cijfers, de
   instellingen en de knop voor tussentijds opslaan). De standaardwaarden werken zonder;
2. "Downloaden als PDF" in de lijst. In de viewer blijft hij;
3. `docs/fase-4/collabora.md` (naar het begin van fase 5);
4. de meting met 20 bewerkers. `max_editors` blijft dan 20, met een notitie.

Wat nooit schuift: de toegangscontrole (O1), opslaan en herstel (O2), de invoercontrole, en de
job met de echte DS.

### 5.1 Risico's

| Risico | Kans | Impact | Wat we doen |
|---|---|---|---|
| Een fout in DS draait op onze origin en handelt namens een bewerker (bijvoorbeeld via het refresh-cookie) | laag tot middel | hoog | Keuze 7 en 9: de editorpagina alleen met het poort-cookie (SameSite=Strict) en het recht van nu, dus nooit via een link van buiten; DS onveranderd en vastgepind, plugins en macro's uit; een volledige CSP; Vraag 3; een eigen hostnaam in fase 8 |
| Een ingetrokken gebruiker blijft in de editor of komt terug | middel | middel | elk dynamisch pad en elke tunnel controleert het recht van nu (15 s cache); het eerste socketpakket moet bij de key van het pad horen; `banned`; is hij er bij de volgende ronde nog, dan stopt de hele sessie (Keuze 20); spike stap 14 |
| Het eerste socketpakket heeft een andere vorm dan verwacht | middel | middel | spike stap 12 legt de vorm vast in `recorded/socket.json`; de nep-DS volgt die; is het token niet leesbaar, dan geldt de terugval van Keuze 20 (de sessie stoppen) |
| Een kwaadaardig bestand raakt een fout in de converter van DS | laag | hoog | de invoercontrole vóór elke conversie en sessie (Keuze 23); uploads via een link en bestanden boven 20 MB alleen op verzoek; `inputLimits`, ook voor ODF; Vraag 3 |
| Een bestand laat DS steeds opnieuw vastlopen, en elke herstart kost open werk | laag | middel | na het versturen telt elke mislukking als poging; `max_attempts = 2`, dan `failed` en een melding, en nooit meer vanzelf (Keuze 25); tussentijds opslaan elke 5 min |
| Een grote conversie houdt het openen of opslaan op (één converter in DS) | middel | middel | geen conversie zolang een sessie opent of opslaat; automatisch alleen tot 20 MB; één tegelijk; meting in §4.7; spike stap 16 |
| Eén gebruiker opent honderden sockets of stuurt grote uploads | laag | middel | per gebruiker 8 tunnels, per key `max_editors`, 1.000 in totaal; editoracties per gebruiker 60 per minuut en 200 MB per 10 minuten (Keuze 5 en 6) |
| DS eist geen tokens meer (verkeerd image of instelling) | laag | hoog | de tokencontrole bij elke overgang naar gezond en elke 10 minuten; anders `misconfigured` en bewerken uit (Keuze 28) |
| Na een update wil Jonas terug naar v0.5.0 | laag | hoog | `db-downgrade 0006` en de stappen in het runbook; liever `OFFICE=uit`; getest in `test_migration_0007.py` en bij de integratie |
| DS bereikt diensten van de LXC via het gateway-adres (M7) | middel | middel | op de LXC luistert alleen 80, 443 en SSH (checklist in het runbook); SSH met sleutel; een firewallregel hoort bij fase 8 |
| De tunnel leunt op hoe uvicorn een upgrade doorgeeft (`ws_protocol_class`, `handle_websocket_upgrade`) | middel bij een update van uvicorn | middel | `test_serve_upgrade.py` en `test_office_tunnel.py` op h11 en httptools; uvicorn staat vast in de lock; een update van uvicorn is een eigen PR |
| De editor gebruikt een pad dat niet in de lijst staat (downloaden, afdrukken, afbeelding) | middel | middel | spike stap 1; een logregel bij elk geweigerd pad; de echte-editor-test in CI |
| Een worker van DS stuurt geen cookie mee | laag | middel | statische paden hebben geen cookie nodig; spike stap 2 |
| De volledige CSP breekt een editoractie | middel | laag | spike stap 9; alleen de richtlijn die breekt wordt ruimer, met een notitie in `spike.md` en een aangepaste Vraag 3 |
| Een harde stop of OOM van DS gooit werk weg (alles in het geheugen) | laag | middel | tussentijds opslaan elke 5 min, geheugengrens 2 GB, nette stop met 60 s, forcesave vóór updates |
| Herstart van de app tijdens bewerken (update): de callback valt in het gat | middel | middel | forcesave vóór de update en bij het stoppen; 8 pogingen (±4 min); `saving` loopt af (Keuze 20); automatisch herstel (Keuze 21); spike stap 4 en 5 |
| Herstart van de LXC: dockerd stopt alle containers tegelijk, niet in de volgorde van compose | middel | laag | DS slaat bij een nette stop op; is de app al weg, dan herstelt Keuze 21 het bij de start |
| Docker Hub geeft 429 bij installeren, bijwerken of in CI | middel | laag | pull op digest met `mirror.gcr.io` als terugval, daarna `docker tag`; een pin in `.env`; de vorige DS blijft; het platform werkt altijd door |
| De klassieke image-opslag van Docker vindt `naam:tag@digest` na `docker tag` niet | laag tot middel | laag | in de sandbox (containerd-opslag) lukt het wel [gemeten]; `install.sh` geeft compose altijd `OFFICE_IMAGE` mee; de checklist op de LXC controleert het; anders noemt O6 in compose de tag en controleert `install.sh` de image-id |
| LXC: `vfs`-opslag, geen cgroup-limieten, het interne netwerk werkt anders in geneste Docker | laag | middel | `install.sh` controleert driver, ruimte en limieten; nooit een verzwakking op de host; checklist in het runbook |
| Het bekende Proxmox-probleem met containerd en `ip_unprivileged_port_start` (nov. 2025) | laag | hoog | geldt voor elke container, niet alleen DS; `install.sh` test al met hello-world en zegt nu of de pull of de start faalt; "AppArmor unconfined" stellen we nooit voor |
| Iemand met het office-geheim maakt callbacks na | laag | hoog | het geheim staat alleen in `.env` (600) en in de twee containers; callbacks alleen op de interne poort; elke opslag controleert het recht van een echte gebruiker; de grens op de download; de vorige inhoud blijft als versie |
| De proxy in Python is te traag bij een koude start (253 verzoeken) | laag | laag | versiepaden zijn `immutable`; warm 5 verzoeken; meten in §4.7 |
| De nep-DS wijkt af van de echte | middel | middel | opgenomen sporen in `recorded/`, `test_fake_docserver.py` per deel, en de echte job vanaf O0 als scheidsrechter |
| Een nieuwe DS-versie verandert paden of protocol | middel | middel | vast op digest; bijwerken is een eigen PR waarin de job `office` en de padtest draaien |
| 20 bewerkers op 4 cores is te zwaar | middel | middel | eigen grens `max_editors`; meting in §4.7 |
| Forgotten files staan niet in de ZFS-snapshot | laag | laag | automatisch herstel binnen minuten; wat blijft, staat op `/beheer`; README |
| Status 3 stuurt een kapot document | laag | laag | herstelkopie, het origineel blijft heel |
| Alt+Shift+Q botst met een sneltoets van DS | middel | laag | spike stap 6; anders een andere combinatie; Sluiten in de kopbalk en Terug blijven altijd |
| AGPL: dat ons platform erbuiten blijft, is een lezing | laag | hoog | onveranderd image, alleen via HTTP; geen juridisch advies [niet getest]; we patchen DS niet |
| Eén proces: sloten en tellers zitten in het geheugen | — | — | goed voor standalone; het cluster vraagt later één DS per document (`shardkey`) en gedeelde status |
| Collabora is alleen een document | middel | middel | de naad en de contracttests houden het op ±1 week; Vraag 1 en 5 |
| De planning loopt uit (±11 dagen) | middel | laag | de lijst hierboven van wat als eerste schuift; de kern schuift nooit |

---

## 6. Verwerkte en afgewezen kritiek

Alle 30 punten heb ik nagelopen in de code, in het image van DS en in de notities. Geen enkel punt
bleek onjuist. Waar ik een voorgestelde fix anders deed of een deel niet overnam, staat dat onder
"Afgewezen kritiek", met de reden.

### Verwerkt

**Veiligheid**

| Kritiek | Fix |
|---|---|
| S1. De HTML van DS ging zonder cookie door. Elke site kon zo de editor op onze origin laten starten, ook bij een lezer of een beheerder die nooit bewerkt. | Een eigen padklasse voor de editorpagina: elke `*.html` en een omleiding daarheen alleen met het poort-cookie (`SameSite=Strict`), en 404 bij `Sec-Fetch-Dest: document` als de kop er is. `Cross-Origin-Resource-Policy: same-origin` op statische bestanden. Keuze 5, 7 en 9, Vraag 3, §3.9 #3–#4, §3.10; `test_office_paths.py`, `test_office_proxy.py`, e2e §4.4 punt 10. |
| S2. Een ingetrokken deling hield niet op: de poort keek niet naar het recht, `members` werd nooit kleiner, DS onthoudt niemand na `drop`, de tunnel las het token in het eerste pakket niet, en `cache/files` vroeg geen lidmaatschap. Uitloggen duurde tot 120 s. | De app beslist bij elk verzoek naar DS: cookie, lid, niet `banned`, sessie niet dicht, en schrijfrecht van nu via `access.resolve`, met hooguit 15 s cache (Keuze 7). De tunnel leest het eerste socketpakket (`oo/stream.py`) en controleert elke 15 s opnieuw (Keuze 6). De ronde haalt uit `members`, zet in `banned`, leegt de cache, sluit de tunnels en stuurt `drop`; is iemand er bij de volgende ronde nog, dan stopt de sessie (Keuze 20). `/gate` en `/save` eisen het recht van nu. `config.token` krijgt `exp` (Keuze 11). Spike stap 12, 14 en 15. Uitloggen: binnen 30 s. |
| S3. De CSP op de HTML van DS had geen `default-src`, en hield dus weinig tegen. Vraag 3 beloofde meer. | De volledige CSP met `default-src 'self'` (Keuze 9), gemeten in spike stap 9. Keuze 9 en Vraag 3 zeggen nu dat navigatie en handelingen binnen de origin niet tegen te houden zijn. |
| S4. Elk office-bestand tot 100 MB, ook anoniem via een uploadlink, ging vanzelf naar de converter van DS, zonder controle op zip-bommen. | De invoercontrole `inspect.py` vóór elke sessie en conversie (Keuze 23). Een upload via een link en een bestand boven 20 MB krijgen `pdf_state = ask`: alleen een ingelogde gebruiker met leesrecht vraagt het (Keuze 25). doc, xls, ppt en rtf hooguit 20 MB. `inputLimits` voor ODF. Genoemd in Keuze 9, Vraag 3 en §5.1. Tests in `test_office_inspect.py` en `test_office_convert.py`. |
| S5. Een bestand dat DS laat vastlopen, kwam eeuwig terug, en elke herstart van DS kost open werk. | Alleen een ongezonde DS **vóór** het versturen telt niet als poging. Daarna telt alles. `office.pdf` heeft `max_attempts = 2`; `on_dead` zet `failed` en geeft de melding `office_convert_failed`; nooit meer vanzelf (Keuze 25). Test met `__fake/kill`. |
| S6. Een sessie kon voor altijd in `saving` blijven, en dan kon het bestand nooit meer open. | De ronde vraagt `info` bij `saving` ouder dan 6 minuten (`updated_at`); `error 1` → dicht met `lost`. Het herstel slaat een `saving`- of `lost`-sessie gewoon op het origineel op (Keuze 20 en 21). Tests in `test_office_races.py`. |
| S7. Callbacks in de verkeerde volgorde konden de laatste opslag overschrijven, en door `squash` bleef die niet eens als versie. | Een merk per opslag (`lastsave`, terugval `history.changes`) en `applied_mark`: een merk dat niet hoger is, schrijft niets. Een tabel voor callbacks op een dichte sessie. Een late opslag gebruikt nooit `squash` (Keuze 15, 16 en 17; `CallbackEvent.mark`; spike stap 13). Het scenario staat in `test_office_races.py`. |
| S8. Conversies waren niet echt begrensd: na onze tijdslimiet kwam er een tweede bij, ze konden openen en opslaan ophouden, lezers konden ze vooraan zetten, en foutcodes ontbraken. | Na onze tijdslimiet dezelfde key opnieuw vragen; `-a<n>` alleen na -2 of -4. Automatisch alleen tot 20 MB. Geen conversie zolang een sessie opent of opslaat. Op verzoek hooguit 10 per 10 minuten per gebruiker. -5 → `none`, -1 en -6 → een gewone poging (Keuze 25 en 26). Spike stap 16 en een meting in §4.7. |
| S9. De tunnel had geen grens per gebruiker of per key. | Per gebruiker 8 tunnels (`office_max_tunnels_user`), per key `max_editors`, anders 429. Editoracties met een body: per gebruiker 60 per minuut en 200 MB per 10 minuten (Keuze 5, 6 en 27). Tests in `test_office_tunnel.py` en `test_office_proxy.py`. |
| S10. `office.recovered` en `office.unsaved` gingen ook naar wie zijn recht net kwijt was, met naam en id van de kopie. | De ontvangers worden bepaald bij het versturen: wie de kopie nu kan zien (`access.watchers`) plus de eigenaar, en voor `unsaved` wie nu schrijfrecht heeft. Wie eruit is, krijgt alleen `office.dropped`, zonder naam (§3.4, Keuze 16 stap 8). Test in `test_office_sweep.py`. |
| S11. Definitief verwijderen werd niet gerespecteerd: de inhoud kwam terug als herstelkopie, en forgotten files van een verwijderde gebruiker bleven staan. | Definitief verwijderd, of de eigenaar verwijderd: `drop` voor iedereen, dicht met `gone`, niets schrijven, geen kopie, en de forgotten files van die keys meteen weg (Keuze 16 stap 5, 19, 20 en 21). |
| S12. Niemand controleerde dat DS echt tokens eist. | Bij elke overgang naar gezond en elke 10 minuten: `version` zonder token moet `error 6` geven, een conversie zonder token `-8`. Anders `misconfigured`, de melding, en bewerken en conversie uit (Keuze 28, `health.py`, `oo/admin.tokens_enforced`). De nep-DS heeft `jwt-off`. Tests in `test_office_health.py` en `test_office_monitor.py`. |
| S13. De download in de callback mocht tot 50 GiB groot zijn; het quotum kwam pas daarna. | De grens is de kleinste van `office_max_save_bytes` (512 MB) en `room_left` van de eigenaar, ook bij forgotten files. Daarboven niets schrijven en `office.unsaved` met `too_large` of `no_room` (Keuze 16 stap 4, Keuze 21). Tests in `test_office_callback.py`. |
| S14. `onRequestRefreshFile` ontbrak, en hergebruik kon een key geven die DS juist gaat opslaan. | De editorpagina vangt `onRequestRefreshFile` op: een nieuwe sessie en `refreshFile`, anders opnieuw laden (Keuze 32, M17). Hergebruik alleen als iemand verbonden is; een lege `open`-sessie geeft 409 `office_busy`, en de pagina vraagt het elke 3 s opnieuw (Keuze 14). In de nep-DS en in spike stap 5. |
| S15. Vraag 4 beloofde dat de inhoud van vóór het bewerken altijd terug te halen is, maar een lange sessie duwde hem eruit. | Hooguit 10 versies per sessie (`versions_made`), daarna alleen vervangen (Keuze 17). Vraag 4 herschreven. Test met 25 tussenstanden, 31 minuten uit elkaar, in `test_office_policy.py`. |

**Haalbaarheid**

| Kritiek | Fix |
|---|---|
| F1. De volgorde van samenvoegen kon niet: O2 had code van O5 nodig, de e2e van O1 de editor van O2, en twee lussen hadden twee eigenaars. | De dunne DS-aanroepen in O0 (`oo/admin.py`) en de gezondheid in O0 (`health.py`). De ronde en het herstel zijn van O2; O5 leest alleen (§2.0). §4.4 punt 1 telt via `fake_info`, niet via `/beheer`. Samenvoegen in de volgorde O2 → O1 → O3 → O4 → O5 → O6, met O2 op de kern van O0 en `office_dev_ungated` alleen in tests (§5). |
| F2. De ronde van 30 s via housekeeping kon niet (die tikt elke 60 s, alles na elkaar), en de e2e had geen instelling om hem sneller te zetten. | Een eigen taak in `sweep.py`, gestart in de lifespan (Keuze 20, §3.5). `office_sweep_seconds` en `office_recovery_seconds` in `config.py` (§3.2); de harnas zet 1 en 2 (§3.6). Een klok die de API-tests kunnen verzetten (§2 O2). M12. |
| F3. `OFFICE=uit` deed niets op een bestaande installatie, een mislukte pull stopte het hele script, en dockerd herstartte vóór het tussentijds opslaan. | `want_office` als eerste regel van `main`, vóór het lezen van `.env`; `merge_env`; het image in een `if`; `systemctl restart docker` alleen na het schrijven van `daemon.json`; de vorm `curl … \| OFFICE=uit bash`. `test_install_env.sh` (Keuze 30, §2 O6). M13. |
| F4. Het image van de mirror werd nergens vastgelegd, en terugrollen bleef niet staan. | Na een pull van de mirror `docker tag` naar de naam van Docker Hub. `OFFICE_IMAGE` in `.env` is een pin die het script nooit vervangt of weggooit. Tests voor de pin en voor een losse `docker compose up -d` (Keuze 29 en 30, §2 O6). M19. |
| F5. De regel voor hergebruik botste met de unieke index, en een callback op een dichte sessie was niet bepaald. | Eén venster van 5 minuten voor `pending`. Een oude rij dicht (`lost`) en een nieuwe in één transactie, onder `node_lock`. `info` ook voor `pending`. De tabel voor dichte sessies (Keuze 14, 15 en 20). Tests in `test_office_sessions.py` en `test_office_callback.py`. |
| F6. Geen pakket kon zijn bewijs met de echte DS leveren vóór het samenvoegen, want de job was van O6. De fixtures voor doc, rtf, xls en ppt hadden geen eigenaar. | De job `office` komt in O0, met een rooktest. Elk pakket voegt zijn `*_real.py` toe, en zijn PR moet door die job (Keuze 31, §4.5). `real_office` kiest zelf de interne poort. De fixtures eenmalig met LibreOffice, in `e2e/fixtures/office/` (O0, §3.6). |
| F7. O0 paste niet in 3 dagen, een bevroren nep-DS hield de golf op, en de spike hing aan code die nog niet bestond. | De spike met het gereedschap van de proef, en aan het eind van O0 stap 2, 5 en 11 opnieuw via de app (§3.7). De nep-DS in drie delen: kern (O0), forgotten files (O2), conversie (O3) (§3.6). De planning eerlijk: ±11 werkdagen, met wat er als eerste schuift (§5). |
| F8. Thumbnails en pagina's uit de PDF zaten in caches die alleen op de sha keyden. De browser hield de oude thumbnail, en een nieuwe `PDF_VERSION` gaf oude pagina's. | `v=<pdf_version>` in het getekende adres van `previews.url` en in `info` (O0). Bij een nieuwe PDF de paginacache leeg, de ETag met `p<v>`, en de versie in `info.json` (O3) (Keuze 25, §3.3, §3.5). Tests in `test_office_convert.py`. |
| F9. De installatietest in de sandbox kon zo niet draaien. | `PLATFORM_REPO` en `PLATFORM_SRC_REF` in `install.sh`; `systemctl` alleen als het nodig is; hello-world eerst van `mirror.gcr.io`, en een pull-fout apart van een fout bij het starten. `test_install_update.sh` met `debian:12`, `docker.sock` en dezelfde absolute paden; de basisimages vooraf van de mirror (Keuze 30, §2 O6). |
| F10. De rij van taken kon "één conversie tegelijk" en "nieuwe uploads eerst" niet uitdrukken. | Is het slot bezet, dan de taak over 5 s opnieuw in de rij, zonder poging. Op verzoek een `run_after` in het verleden. De inhaalronde in stukken met 60 s ertussen, zoals `previews.RERENDER_DELAY` (Keuze 25). |
| F11. De teksten van de haken van O0 hadden geen eigenaar in een gebied dat de route Bestanden laadt. | O0 schrijft het blok `office` in `i18n/areas/{browser,viewer,admin}`, vóór de tag (§2.0, §3.5). |
| F12. De editorconfig had vaste Nederlandse tekst, en de kopbalk beloofde namen die geen API gaf. | `close.text` uit `user.locale`, of weg als DS zelf een tekst per `lang` heeft (§2 O2). De kopbalk toont het aantal uit de nieuwe gebeurtenis `office.presence` (Keuze 32, §3.4). |
| F13. De haak in `App.tsx` zit in de eerste lading, en daar is nog maar 6,2 KB ruimte. | Alleen een kleine luisteraar, die `office/notices.ts` en het gebied `office` pas bij de eerste gebeurtenis laadt. De schil importeert `office/index.ts` nooit. Een poort "eerste lading ≤ +0,5 KB" (Keuze 33, §3.8, §4.7). M18. |
| F14. De dev-proxy wees naar poort 8000, maar `app.serve` luistert op 8080. | De dev-opdracht met `PLATFORM_HTTP_PORT=8000` en de office-variabelen, naast een `docker run` van DS (Keuze 33, `docs/fase-4/e2e.md`). |
| F15. Terugrollen van de app kon niet: v0.5.0 start niet op een database op 0007. | `app.cli db-downgrade` (O0). "App terugrollen" in het runbook, liefst eerst `OFFICE=uit`. `test_migration_0007.py` en `test_cli_db.py` (Keuze 34, §3.1, §3.5, §2 O6). M19. |

### Uitslag van de spike (O0)

Geen kritiek, maar een meting: de spike (O0-spike, stap 1 tot 18; O0-e2e deed stap 2, 5 en 11
opnieuw via de kern in de app) legde het padbeleid vast. §3.10 is daarom bijgewerkt naar de
definitieve tabel van `docs/fase-4/spike.md`. Wat daardoor anders is dan hierboven bij S1 staat:
de editorpagina is niet "elke `*.html` en een omleiding daarheen", maar precies de drie
`…/main/index.html` (plus `…/mobile/index.html`); `/<V>/index.html` is een statuspagina van DS en
blijft dicht. Verder: documentdata ook zonder `_<n>`, `upload/<key>` met `Authorization`, geen
`printfile`, en `cmd` in `SECRET_PARAMS` (spike A2–A6 en A20). De andere afwijkingen van de spike
(A1, A7–A19, en A21–A22 van de herhaling) staan in `docs/fase-4/spike.md`; de pakketten O1 tot O6
verwerken ze.

### Afgewezen kritiek

| Kritiek | Waarom niet |
|---|---|
| S1: "Eis `Sec-Fetch-Dest: iframe` of `frame` op elke HTML van DS." | Over http in het LAN stuurt de browser geen `Sec-Fetch-*` (M16), en de service worker van DS vraagt met `empty`. Met die eis opent de editor in het LAN zonder domein nooit. Wat we wel doen: het cookie is altijd verplicht, en dat gaat door `SameSite=Strict` nooit mee met een link van een andere site. Is de kop er, dan geven `document` en andere waarden 404. |
| S2 punt 3: "Zet `token.session.expires` op 2 uur, en geef `config.token` een `exp` van 10 minuten." | Het sessietoken korter maken kan werk kosten: verloopt het terwijl iemand bewerkt, dan kan zijn editor niet opnieuw verbinden, tenzij DS het vernieuwt (M15). Spike stap 15 meet dat; alleen bij ja komt `"2h"` erin. Voor de veiligheid is het niet nodig: de tunnel kijkt naar het recht van nu, wat het token ook zegt. `config.token` krijgt wel een `exp`, maar 2 uur, gelijk aan het poort-cookie. Na een start van DS duren de fonts tot 280 s, en een tab op de achtergrond kan laat verbinden. Met 10 minuten opent de editor dan niet, zonder dat het iets beschermt. |
| S2 punt 4: "Bij elke intrekking: forcesave, `drop` voor iedereen, de sessie dicht en een nieuwe key." | Dat gooit bij elke intrekking alle andere bewerkers uit de editor. Nodig is het alleen als de ingetrokken gebruiker bij de volgende ronde nog verbonden is, of als het lezen van het eerste socketpakket niet lukt. Dan doen we het wel (Keuze 20). |
| S2 punt 6: "Laat uitloggen de cache van de poort leegmaken." | Dat vraagt een haak in `auth.py`, een bestand van fase 3. Met 15 s cache en een controle elke 15 s is uitloggen binnen 30 s uit de tunnel. Dat is genoeg, en de tekst zegt nu "binnen 30 s". |
| S4 punt 2: "Samen hooguit 64 MB uitgepakt, en hooguit 100 keer kleiner dan uitgepakt." | Te streng: een pptx met 80 MB foto's is gewoon, en zou dan nooit een voorbeeld of editor krijgen. We tellen de echte bytes, met een grens per soort op de XML (gemeten: 4 MB, 2 MB voor ODF, 10 MB voor een echt werkblad), 512 MB in totaal, 10.000 onderdelen, en de verhouding pas boven 64 MB (Keuze 23). Een bom houdt dat net zo goed tegen. |
| S5: "Houd een `crash_count` per sha bij, in de metadata van `pdf_state`." | De rij van taken telt de pogingen al (`attempts`, `max_attempts`, `on_dead`). Een tweede teller is dubbel, en kan uit de pas lopen. Het gedrag is gelijk: hooguit twee kansen, dan `failed` en een melding. |
| S11: "Herstelkopieën alleen bij een automatische bewaartermijn; forgotten files van een verwijderde eigenaar na N dagen weg." | De bewaartermijn haalt alleen weg wat de eigenaar zelf in de prullenbak zette. Ook dat is zijn keuze. Een verschil maken vraagt bovendien een haak in `trash.py`. Daarom: na elk definitief verwijderen geen kopie. Forgotten files van een verwijderde eigenaar gaan **meteen** weg, niet na N dagen: er is niemand meer om ze aan terug te geven. |
| F1, de andere opties: "de beheer-GET van O5 in O0", of "een testpagina in de e2e van O1 die zelf een config tekent." | Met de dunne aanroepen in O0 en tellen via `fake_info` heeft O2 niets van O5 nodig. Een eigen testpagina test een andere pagina dan gebruikers krijgen. O2 vóór O1 samenvoegen is eenvoudiger en test het echte pad. |
| F10, de andere optie: "`jobs.py` naar O0, met een `Kind.concurrency`." | Opnieuw in de rij met `run_after` lost het op zonder een gedeeld bestand van fase 3 te veranderen. Een nieuwe vorm van gelijktijdigheid in de rij is een groter risico voor alle andere taken. |
| F12, de andere optie: "`editors: [display_name]` in de API en in `office.presence`." | DS toont de namen van mede-bewerkers zelf in de editor. Namen in onze gebeurtenis sturen meer persoonsgegevens rond dan nodig. We tonen het aantal. |
