> Werkdocument: het bouwontwerp van fase 4 (office), revisie 1.
> Nog niet besloten: de editorkeuze ligt bij Jonas (Vraag 1).

# Fase 4 (office): bouwontwerp, revisie 1

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `3ccee0a` (v0.5.0, migraties 0001–0006).

Revisie 1 voegt twee ontwerpen samen. Ontwerp A ("standalone en veilig eerst") is de basis. Van
ontwerp B ("de editor is een gast") komen de stukken waar B gelijk had of beter bouwt. Een paar
stukken zijn nieuw, omdat beide ze misten. Waar A en B elkaar tegenspraken, heb ik het in de
sandbox nagemeten.

Bronnen (scratch, niet in het repo):

- `fase4/editors.md`: onderzoek naar OnlyOffice en Collabora, met een bron per bewering;
- `fase4/feasibility.md`: de proef met de echte DS in de sandbox;
- `fase4/code-map.md`: waar fase 4 in de code inhaakt;
- `fase4/judging.md`: het oordeel over A en B, per onderdeel en per lens, met de metingen M1–M11.

Bij een bewering over OnlyOffice staat waar hij vandaan komt:

- **[gemeten]**: gedraaid in de sandbox met `onlyoffice/documentserver:10.0.0.1`;
- **[doc]**: officiële documentatie;
- **[image]**: gelezen in de bestanden van het image;
- **[niet getest]**: niet bevestigd. Daar ontwerpen we voorzichtig. De spike (§3.7) of een test
  (§4) beslist.

Afkortingen: **DS** = OnlyOffice Docs (Document Server). **App** = ons platform (API en web-app in
één container).

**Nagemeten bij deze revisie** (scripts in `fase4/judge/`):

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
2. **De app is de enige ingang.** De browser praat alleen met de app. DS heeft geen poort naar
   buiten en geen weg naar internet. Alleen ingelogde gebruikers (met 2FA) met een open
   bewerksessie komen bij de dynamische delen van DS.
3. **Elk verzoekpad is nagelopen** (§3.9 en §3.10): wie mag het, met welk token, en waar wordt het
   gecontroleerd.
4. **Werk gaat nooit stil verloren.** Wat niet op het origineel kan, wordt een herstelkopie bij de
   eigenaar. Wat ook dat niet kan, staat op `/beheer`.
5. **De editor is vervangbaar.** Sessies, opslaan, versies, toegang en previews zitten in een kern
   die OnlyOffice niet kent. Alleen een kleine adapter spreekt het protocol van OnlyOffice.
6. **Bijwerken mag het draaiende platform niet breken.** Lukt iets met office niet, dan werkt het
   platform verder zonder editor, of met de vorige editor.
7. **Liever schrappen dan risico.** Wat we schrappen, staat onder Keuze 34.

### 0.3 Hoe de browser bij de editor komt (alleen 80 en 443)

```
browser ──https 443 / http 80──▶ app (Edge: tls, koppen, redirect)
                                  ├─ /api/v1/office/…   gewone API (ingelogd, 2FA)
                                  ├─ /office/… http     proxy in de app (ASGI-middleware)
                                  └─ /office/… upgrade  tunnel in de app (eigen ws-klasse)
                                        │ poort-cookie, padbeleid, koppen herschrijven
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
     opslaan, versies, ronde, herstel, poort-cookie, proxy en tunnel, conversietaak, beheer)
     praat alleen met `Engine`.
   - Alles wat OnlyOffice-specifiek is, staat in `app/office/oo/`: de JWT-vormen, `/command` en
     `/converter`, de editorconfig, het lezen van een callback en het padbeleid.
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
     `X-Frame-Options` en `Content-Security-Policy` van DS. Erbij: `X-Frame-Options: SAMEORIGIN`
     en de CSP van Keuze 9. Edge laat die staan en voegt de rest toe. `Cache-Control` van DS
     blijft (`api.js` `no-store`, versiepaden `immutable`).
   - `Location` alleen als die relatief is of naar onze eigen origin wijst. Anders weg.
   - Bodies in stukken, hooguit 100 MB (zoals de nginx van DS). Gzip blijft gzip.
   - Alleen een vaste lijst padklassen gaat door; de rest geeft 404 (§3.10). De beheer- en
     server-eindpunten van DS komen nooit van buiten, ook niet achter een versieprefix: de nginx
     van DS stuurt `/<versie>/<pad>` gewoon door naar docservice [image].
   - DS niet bereikbaar: 502 `office_unavailable`.
6. **WebSocket via een eigen tunnel, zonder WebSocket-bibliotheek.**
   - Uvicorn krijgt `ws=Tunnel` op de publieke luisteraars. `Tunnel` is een eigen
     `asyncio.Protocol` (`app/office/tunnel.py`). Uvicorn geeft hem de herbouwde upgrade-request
     als bytes [gemeten, M3].
   - De tunnel leest alleen de request-regel en de koppen (hooguit 16 KB). Hij controleert pad,
     `Origin` en poort-cookie (Keuze 7). Dan opent hij TCP naar DS, stuurt de herschreven upgrade,
     en pompt daarna bytes heen en weer. Hij leest geen frames.
   - Weigeren gebeurt met een kale HTTP-regel en sluiten: geen match of ander pad dan
     `/office/<versie>/doc/<key>/c/` → 404; `Origin` ontbreekt of hoort niet bij de `Host` → 403;
     geen geldig poort-cookie of geen lid van sessie `<key>` → 401; boven 1.000 open tunnels → 503.
   - Een upgrade naar een ander pad sluit meteen. De app zelf heeft dus nog steeds geen
     WebSockets (B17 blijft).
   - Dezelfde regel als Edge voor poort 80: is er een certificaat en is de Host het domein, dan
     weigert de tunnel een upgrade over http.
   - Tegendruk: lezen pauzeren boven 1 MB in de schrijfbuffer van de andere kant, verder onder
     256 KB. Na 120 s zonder bytes dicht (socket.io pingt elke 25 s). Dicht aan de ene kant is dicht
     aan de andere.
   - Elke 60 s controleert de tunnel het poort-cookie van zijn verbinding opnieuw (gebruiker
     actief, login niet ingetrokken). Zo sluit uitloggen op één apparaat precies die editor.
   - Een register van open tunnels: per key en per gebruiker. Daaruit komen de tellers van Keuze 27.
     `close_all()` bij het stoppen van de app.
   - **De fix van M1:** `HeaderDeadline` overschrijft `handle_websocket_upgrade`. Bij een upgrade
     gaat de deadline weg en zet hij een vlag `_upgraded`. Daarna start `_update_deadline` hem
     nooit meer.
   - Waarom zelf: `ws="websockets"` is verouderd (M2), een expliciete afhankelijkheid botst met B19,
     en een tunnel kost weinig cpu. De proef deed hetzelfde met ±150 regels stdlib [gemeten].
7. **Een poort-cookie voor alles wat niet statisch is.**
   - `POST /api/v1/office/files/{id}/session` zet een cookie `office`: HttpOnly, `SameSite=Strict`,
     `Path=/office/`, `Secure` over https.
   - De waarde is een JWT met het **app-geheim**: `typ: office_gate`, gebruiker, login-family,
     2 uur geldig. DS ziet dit cookie nooit, want de proxy stuurt geen `Cookie` door.
   - De proxy eist het cookie voor documentdata (`cache/files/data/…`), de sessie
     (`doc/<key>/c/`) en de editoracties. Bij de sessie en bij een actie met een key in het pad
     ook: de gebruiker is lid van een open sessie met die key.
   - Statische bestanden van het image gaan zonder cookie door: `web-apps`, `sdkjs`,
     `sdkjs-plugins`, `fonts`, `dictionaries`, `document_editor_service_worker.js`, `api.js`.
     Die zijn op elke DS gelijk en bevatten geen gebruikersdata. Zo breekt niets als een worker
     van DS ergens geen cookie meestuurt [niet getest, spike].
   - Controle per verzoek: handtekening, `exp`, gebruiker actief, login niet ingetrokken. De
     uitslag staat 60 s in het geheugen, want een koude editorstart is 253 verzoeken [gemeten].
   - De editorpagina vernieuwt het cookie elke 30 minuten (`POST /api/v1/office/gate`).
   - Waarom: zonder dit cookie zijn de socket en de cache van DS vanaf internet bereikbaar voor
     iedereen die een key raadt of ziet.
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
     - lezers en publieke links komen nooit bij DS (Keuze 22). Alleen wie mag bewerken, draait
       DS-code;
     - DS onveranderd en vastgepind op digest; plugins, macro's, AI en docbuilder uit
       (`customization.plugins: false`, `customization.macros: false`, `macrosMode: "disable"`,
       `PLUGINS_ENABLED=false`, `aiSettings.enable: false`, `docbuilderEnable: false`)
       [doc, de namen controleert de spike];
     - DS ziet nooit het app-geheim, nooit `Cookie` en nooit `Authorization`;
     - op elke HTML van DS zet de proxy een CSP die uitlekken naar buiten moeilijker maakt:
       `frame-ancestors 'self'; connect-src 'self'; form-action 'self'; img-src 'self' data: blob:;
       object-src 'none'; base-uri 'self'`. Inline scripts en stijlen van DS blijven toegestaan
       (die heeft DS nodig). Of de editor hiermee werkt, test de spike. Zo niet, dan alleen
       `frame-ancestors 'self'`, zoals in de proef;
     - de app-pagina zelf houdt haar strikte CSP (M4).
   - Wat overblijft: een fout in DS kan binnen onze origin handelen namens een ingelogde
     bewerker. Dat staat bij de risico's en in Vraag 3.

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
    - app → browser → DS: de hele editorconfig (zonder `events`), getekend in `config.token`;
    - DS → app, document ophalen: `Authorization: Bearer` met `{"payload": {"url"}, "operation":
      "download"}`. De app eist dat `payload.url` precies de gevraagde URL is;
    - DS → app, callback: token in de body (platte velden) en in de header (velden onder
      `payload`), beide met `operation: callback`, 5 minuten geldig [gemeten]. De app neemt liever
      de body, want de header kan grote velden zoals `history` missen [doc]. De app gebruikt
      **alleen velden uit een geldig token**, nooit de losse body;
    - app → DS, `/command` en `/converter`: `token` in de body, met de parameters als payload.
      Zonder of met een fout token: `{"error": 6}` of `{"error": -8}` [gemeten].
    - Een token met het office-geheim werkt nooit als app-token, en omgekeerd: andere sleutel, en
      `typ` of `operation` wordt gecontroleerd.
12. **Geen geheimen in URL's.** De interne URL's bevatten alleen een willekeurige id: de
    document-key, of een grant-id van 128 bit voor conversies. Toegang komt uit het JWT van DS.
    `SECRET_PARAMS` in `main.py` krijgt er `md5` en `shardkey` bij, zodat de opslaglinks van DS
    nooit in een log staan.

### SSRF

13. **De app volgt nooit een host uit een URL van DS.**
    - Bij de callback-`url`, de conversie-`fileUrl` en `getForgotten` neemt de app alleen pad en
      query. Het pad moet passen bij `^(/office)?/cache/files/data/<key>(_\d+)?/…` met **onze**
      key (bij conversie `conv_<key>`, bij forgotten `forgotten/<key>`). Daarna gaat het verzoek
      naar `PLATFORM_OFFICE_URL` (`http://office`).
    - Dat mag, want de handtekening van de link hangt alleen af van pad, `expires` en geheim
      [gemeten: herschreven URL 200, kapotte md5 403].
    - httpx zonder omleidingen, met `trust_env=False`, een tijdslimiet en een grens op de grootte.
    - `ALLOW_PRIVATE_IP_ADDRESS` en `ALLOW_META_IP_ADDRESS` blijven uit (de standaard). Een URL in
      een geldig JWT gaat sowieso langs dat filter, dus onze eigen URL's werken [doc, gemeten]. We
      vertrouwen het filter niet: het interne netwerk is de grens (Keuze 4).

### Sessies, keys en opslaan

14. **Document-key = `<node-id hex>-<16 hex willekeurig>`** (49 tekens, binnen
    `[0-9A-Za-z._=-]{1,128}`) [doc].
    - Een tabel `office_session` (migratie 0007) houdt per open bestand één sessie bij: key,
      bestand, de inhoud waar de key voor staat, wie meedoet, de status.
    - **Hergebruik** (samen bewerken): een tweede gebruiker krijgt dezelfde key als de sessie
      `pending` is en niet ouder dan 2 minuten. Of als hij `open` is én `info` op die key nu
      `error 0` geeft. Anders komt er een nieuwe key.
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
15. **Wat elke status van de callback doet:**

    | status | betekenis [doc] | de app |
    |---|---|---|
    | 1 | iemand opent of sluit | `connected` bijwerken, `pending` → `open`; `{"error": 0}` |
    | 2 | iedereen weg, klaar om op te slaan | opslaan (Keuze 16), sessie dicht |
    | 3 | fout bij opslaan | staat er een `url`, dan **een herstelkopie** naast het origineel, nooit eroverheen; melding aan de eigenaar en op `/beheer`; sessie dicht |
    | 4 | gesloten zonder wijzigingen | sessie dicht |
    | 6 | forcesave, de sessie loopt door | opslaan als tussenstand (Keuze 17), key blijft |
    | 7 | fout bij forcesave | loggen, niets opslaan; status 2 volgt later |

    - Een onbekende key: status 1 en 4 → `{"error": 0}`; status 2, 3 en 6 → `{"error": 1}`.
    - `notmodified` en `lastsave` gebruikt de app niet. Hij haalt altijd op en laat de sha
      beslissen.
    - Waarom status 3 als kopie: wat DS dan stuurt, kan kapot zijn [niet getest]. Een kopie houdt
      het origineel heel en laat niets weg.
16. **Opslaan volgt het recept van `replace_content`, met twee kleine uitbreidingen van
    `place_file`.** Per callback met status 2, 3 of 6:
    1. Het JWT controleren en de sessie zoeken op key (Keuze 15).
    2. Het slot van dit bestand nemen (`asyncio.Lock` per node-id). De volgorde is altijd: dit
       slot, dan `user_lock(owner)`, dan `blobs.lock`.
    3. Bij status 2 of 3: de sessie op `saving` en committen. Wie nu de editor opent, wacht op het
       slot; na 15 s geeft dat 409 `office_busy`.
    4. **Meteen downloaden** naar `blobs.root/.tmp`, met de grens `max_upload_bytes`. De link leeft
       ±900 s en sterft bij een herstart van DS zonder vast geheim [gemeten]. Al eerder opgeslagen
       (zelfde pad in `saved_url`)? Dan meteen `{"error": 0}`.
    5. Het bestand vers laden. Naam en map kunnen veranderd zijn.
    6. Wie slaat op: de eerste uit [de laatste bewerker uit `history.changes[-1].user.id`, de
       `userid` uit `actions`, de rest van de deelnemers] die **nu** nog schrijfrecht heeft (eigenaar
       die geen gast is, of een deling met `write`) en niet geblokkeerd is.
    7. Er is iemand: `place_file(..., "replace", ..., actor=<die gebruiker>, guard=<guard>,
       only=node.id, exact=True, squash=<zie Keuze 17>)`. De guard is `writable(...)` als de actor
       niet de eigenaar is. Het quotum is dat van de eigenaar, zoals altijd bij een gedeelde map
       (B18).
    8. Er is niemand meer met schrijfrecht, of het bestand is definitief weg: **een herstelkopie**
       `<naam> (hersteld <datum tijd>).<ext>` in de hoofdmap van de eigenaar, met de eigenaar als
       actor. Nooit bij iemand die zijn recht kwijt is. Gebeurtenis `office.recovered` naar de
       eigenaar en de deelnemers.
    9. Gelukt: sessie bijwerken en committen, dan pas `{"error": 0}`.
    - **Tijdelijke fout** (database bezet, DS-link gaf 5xx, schijf): **HTTP 503**. DS probeert het
      dan opnieuw [gemeten: 3 keer na 1, 2 en 4 s]. Wij zetten dat op 8 keer (±4 minuten, Keuze 29),
      zodat ook een herstart van de app past.
    - **Blijvende fout** (ook de kopie lukt niet: quotum vol, eigenaar weg): **`{"error": 1}`**. DS
      probeert het dan niet opnieuw en bewaart het document als "forgotten file" [gemeten]. Keuze
      21 pakt het daar op.
    - Een blijvende fout bij status 6 maakt geen kopie (anders komt er elke 5 minuten een). Het
      antwoord is `{"error": 1}`, en de deelnemers krijgen `office.unsaved` met `final: false`.
    - **Idempotent:** een herhaalde callback met dezelfde inhoud geeft dezelfde sha. `place_file`
      maakt bij dezelfde sha geen nieuwe versie.
    - Nieuwe opties van `place_file` (optioneel, de rest blijft gelijk):
      - `exact=True` (alleen met `only`): zoek het bestand op id, niet op naam en map. Hernoemd,
        verplaatst of in de prullenbak maakt dan niet uit. Weg geeft 404. Dat is eenvoudiger en
        juister dan "409 MOVED en één keer opnieuw".
      - `squash=<sha>`: is de huidige inhoud deze sha, dan wordt die geen versie (de verwijzing
        vervalt gewoon). Anders gaat alles zoals nu.
17. **Versies: één per sessie, plus één per 30 minuten in een lange sessie** (Vraag 4).
    - De eerste opslag in een sessie (een tussenstand of de laatste) maakt van de inhoud van vóór de
      sessie een versie.
    - Elke volgende opslag in dezelfde sessie vervangt de tussenstand zonder nieuwe versie
      (`squash=<checkpoint>`). Behalve als de laatste versie uit deze sessie ouder is dan
      `version_minutes` (standaard 30): dan wordt de tussenstand wel een versie.
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
    blokkeren niets in andere code en zetten geen haken in andere routers. Opslaan zoekt op id
    (Keuze 16) en controleert het recht opnieuw.

    | Gebeurtenis | Gevolg |
    |---|---|
    | Hernoemen of verplaatsen | opgeslagen in het hernoemde of verplaatste bestand (`exact`). De titel in de editor blijft de oude tot opnieuw openen. |
    | De eigenaar zet het in de prullenbak | doet de eigenaar mee: opgeslagen in het bestand in de prullenbak (`exact`), dus terugzetten brengt het werk terug. Deelnemers via een deling verliezen hun toegang (zie "deling ingetrokken"). Nieuwe sessies op een bestand in de prullenbak: 422 `trashed`. |
    | Definitief verwijderd (ook door de bewaartermijn) | herstelkopie in de hoofdmap van de eigenaar. |
    | Nieuwe inhoud via upload of "versie terugzetten" | de editor wint: zijn inhoud wordt de nieuwste, de andere inhoud blijft als versie. Wie de editor intussen opent, doet mee met de lopende sessie en ziet "Dit bestand is buiten de editor veranderd" (`stale: true`). |
    | Deling ingetrokken, gebruiker geblokkeerd of uitgelogd | de ronde zet die gebruiker binnen 30 s uit de editor (`drop`); bij uitloggen sluit de tunnel binnen 60 s. Wat hij deed, slaat op als iemand anders in de sessie nog schrijfrecht heeft. Anders een herstelkopie bij de eigenaar. |
    | Quotum van de eigenaar vol | tussenstand: `{"error": 1}` en een melding in de editorpagina. Laatste opslag: ook de kopie lukt niet → forgotten file → Keuze 21. |
    | Eigenaar verwijderd | het bestand gaat mee weg → forgotten file. Terugzetten kan niet meer; alleen weggooien op `/beheer`. |

20. **Elke 30 s een ronde** (`app/office/sweep.py`, een housekeeping-taak van `jobs`):
    - `pending` ouder dan 5 minuten zonder verbinding → dicht;
    - per verbonden gebruiker: actief en nog schrijfrecht? Nee → `drop` met die gebruiker
      [gemeten: drop werkt en DS slaat dan op], en de tunnels van die gebruiker op die key dicht;
    - elke 2 minuten `info` per open key. `error 1` (DS kent de key niet meer, bijvoorbeeld na een
      herstart van DS) → dicht met reden `lost`, zodat de volgende opener een nieuwe key krijgt;
    - tussentijds opslaan (Keuze 18);
    - gesloten rijen ouder dan 30 dagen weg.
21. **Forgotten files herstellen we vanzelf.**
    - Wanneer ontstaat er een: de app antwoordde `{"error": 1}`, of de app was onbereikbaar na alle
      pogingen. Het tweede is de normale oorzaak: bij een update of een herstart van de LXC breken
      de sockets, en DS stuurt status 2 terwijl de app weg is.
    - Bij de start van de app en daarna elke 10 minuten: `getForgottenList`. Per key:
      1. `getForgotten` en ophalen (Keuze 13). Dezelfde sha als het bestand nu → alleen
         `deleteForgotten`.
      2. Het bestand bestaat, er loopt geen sessie op, en zijn inhoud is nog wat deze sessie het
         laatst zag → gewoon opslaan volgens Keuze 16 en 17.
      3. Anders → een herstelkopie bij de eigenaar (Keuze 16, stap 8).
      4. Gelukt → `deleteForgotten`, gebeurtenis `office.recovered`.
      5. Lukt ook de kopie niet (quotum vol, eigenaar weg) → hij blijft staan. `/beheer` → Office →
        "Niet opgeslagen" toont hem met de naam en eigenaar uit `office_session`, met de knoppen
        **Terugzetten** (zelfde stap 3, als er nu ruimte is) en **Verwijderen**. Melding
        `office_unsaved`.
    - Ze staan in het volume `office-lib`, niet in `/tank/platform`, dus niet in de ZFS-snapshot.
      Het herstel zet ze binnen enkele minuten in de app; dan zitten ze er wel in.

### Rechten en formaten

22. **Wie mag wat.**

    | Wie | Editor | Voorbeeld |
    |---|---|---|
    | Eigenaar (geen gast) | bewerken | PDF-pagina's |
    | Deling met `write` (gebruiker of gast) | bewerken; slaat op als bestand van de eigenaar, in diens quotum | PDF-pagina's |
    | Deling met `read` | **geen editor** | PDF-pagina's, downloaden, PDF-export |
    | Publieke link (ook met uploadrecht) | **geen editor, ook niet alleen-lezen** | PDF-pagina's via de bestaande publieke paginaroutes |
    | Album | n.v.t. (alleen foto's en video's) | — |
    | Eerdere versie | niet te bewerken (eerst terugzetten) | PDF-pagina's |
    | Zonder 2FA (2FA is verplicht) | nee: alles vraagt `CurrentUser` | — |

    - Er is dus **geen alleen-lezen-modus van DS**. Wie niet mag bewerken, ziet het omgezette
      PDF-voorbeeld. Zo draait alleen wie mag bewerken ooit code van DS (Keuze 9).
23. **Bewerken alleen voor docx, xlsx en pptx, tot 100 MB.**
    - 100 MB is de grens waarop DS een bestand nog downloadt (`maxDownloadBytes` = 104857600)
      [image]. Groter: `office_too_large`.
    - odt, ods, odp, doc, xls, ppt, rtf en de macroformaten (docm, xlsm, pptm) krijgen alleen het
      PDF-voorbeeld. DS kan doc en ppt niet schrijven [gemeten]. ODF of macro's terugschrijven is een
      risico op verlies van opmaak dat we nu niet nemen. Omzetten naar docx hoort bij fase 10.
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
    - Eerst draait de preview zoals nu (B21: `doc.json` en de ingebedde thumbnail). Daarna zet een
      haak in `previews._store` een taak `office.pdf` klaar (key `office.pdf:<sha>`), als office aan
      staat en het bestand ≤ 100 MB is.
    - Soorten: docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt, rtf en docm, xlsm, pptm. `mime.py`
      herkent doc, xls en ppt al (`OLE_BY_EXT`) en rtf ook.
    - Conversie-key bij DS: `p<PDF_VERSION>-<sha>`, na een mislukte poging met `-a<n>` erachter.
      Die key is een cache bij DS: dezelfde key geeft hetzelfde antwoord, ook voor een ander bestand
      [gemeten]. Met de sha erin kan dat niet misgaan.
    - `async: true` en elke seconde opnieuw vragen, tot 120 s. Klein: ±1 s. 401 pagina's: 22 s en
      641 MB [gemeten]. Eén conversie tegelijk, zodat DS en de cpu's niet vollopen.
    - DS haalt de bron op via `/internal/office/src/<grant>` (een grant van 10 minuten in het
      geheugen, alleen voor deze taak). Het resultaat (`fileUrl`, ±900 s geldig) halen we meteen op.
    - **De PDF staat in `data_dir/office/pdf/<sha[:2]>/<sha>.pdf`**, niet in `previews/<sha>/` (M10).
      Eerst naar `data_dir/office/.tmp`, controle op `%PDF-` en de grens `office_max_pdf_bytes`,
      dan onder `blobs.lock` op zijn plek, alleen als de blob er nog is (zoals `viewer._place`).
    - De staat staat in twee nieuwe kolommen: `blob.pdf_state` (`NULL | pending | ready | failed |
      none`) en `blob.pdf_version`. Niet in `preview_meta`, want die wordt bij een nieuwe preview
      opnieuw gezet (M10).
    - Daarna maakt het bestaande renderproces de thumbnails (256 en 1024) uit pagina 1 van die PDF
      (de pdf-tak, zonder netwerk). Ze vervangen alleen de webp's in `previews/<sha>/`; `doc.json`
      blijft. Komt er later een nieuwe preview (`RENDER_VERSION`), dan zet de haak de taak weer
      klaar. Die ziet dat de PDF er al is en maakt alleen de thumbnails opnieuw.
    - De viewer toont echte pagina's: `GET /files/{id}/document` geeft `kind: "pdf"` met `source:
      "office"`, en de paginaroute rendert de omgezette PDF. Dat geldt vanzelf ook voor de publieke
      link. Is er (nog) geen PDF, dan blijft `OfficeView` met `doc.json` (B21).
    - `previews.discard` haalt ook de PDF weg (één regel), dus `collect_garbage` ruimt alles op.
    - Fouten van DS: -4 (download) en -2 (tijd) → later opnieuw; -3, -7, -9 en -10 → `failed`, geen
      nieuwe poging tot een nieuwe `PDF_VERSION` of een knop op `/beheer`; -8 → melding
      `office_misconfigured`. DS niet gezond → de taak zet zichzelf over 60 s opnieuw in de rij
      (`jobs.chain`), en dat telt niet als poging.
    - Een inhaalronde `office.pdf.backfill` (in stukken van 200 blobs, na de nieuwe uploads) zet de
      ontbrekende conversies klaar: bij de start en zodra DS weer gezond is. `PDF_VERSION` ophogen
      zet alles opnieuw om, zonder `RENDER_VERSION` te raken.
26. **PDF-export = de omgezette PDF downloaden.** Actie "Downloaden als PDF" (lijst, viewer), met
    leesrecht: `POST /api/v1/office/files/{id}/pdf` geeft een downloadlink. Is de PDF er nog niet,
    dan zet de app hem eerst om en wacht hooguit 60 s; anders 409 `pdf_pending`. "Opslaan als PDF"
    in de map en "omzetten naar docx" horen bij fase 10.

### Bewaken en uitval

27. **De verbindingen tellen we zelf, in de tunnel.**
    - Elke editor-tab heeft één WebSocket [gemeten]. Dat is precies wat DS vroeger als "verbinding"
      telde. De tunnel telt ze per key en per gebruiker.
    - **Een eigen grens `max_editors`, standaard 20** (op `/beheer`, 1–500). Vol: de sessie-aanroep
      geeft 429 `office_too_many`; het PDF-voorbeeld werkt gewoon. 24 editors op een klein document
      gingen goed [gemeten]; zwaar werk op 4 cores niet. De meting bij de integratie (§4.7) stelt de
      standaard bij.
    - `license` geeft de limiet van DS zelf (elke 10 minuten, met `version`). Is die eindig (een
      ouder image), dan telt de kleinste van de twee, met een melding vanaf 80 %.
    - `/info/info.json` en StatsD gebruiken we niet. Het eerste mag alleen van 127.0.0.1, het tweede
      is een extra dienst [image].
28. **Als DS er niet is of niet werkt.**

    | Toestand | Wat er gebeurt |
    |---|---|
    | Niet ingesteld (`PLATFORM_OFFICE_URL` leeg) | geen interne luisteraar, `/office/` geeft 404, geen knoppen Bewerken en Nieuw document, previews alleen via B21. Precies fase 3. |
    | Ingesteld, niet gezond | elke 30 s `GET /healthcheck` (moet `true` zijn). Openen geeft 503 `office_unavailable`; het PDF-voorbeeld en B21 blijven; conversies wachten. Na 2 minuten de melding `office_down`. Nieuw document werkt (het is een bestand). |
    | Valt weg tijdens het bewerken | de editor toont waarschuwing -18 en komt niet vanzelf terug [gemeten]. Onze pagina zegt "De verbinding met de editor is weg. Wat het laatst is opgeslagen, staat in het bestand." met "Opnieuw laden". Een nette stop slaat alles op (status 2 binnen 1 s) [gemeten]. |
    | Weer gezond | sessies die DS niet meer kent, sluit de ronde (`lost`); conversies gaan verder (de inhaalronde). |
    | Herstart van de app tijdens het bewerken | de sockets breken; DS houdt de sessie in het geheugen. Of de editor vanzelf opnieuw verbindt, is [niet getest] (spike). Om niets te verliezen: forcesave bij het stoppen (5 s), 8 pogingen van DS (±4 minuten), en daarna Keuze 21. |

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
    - Instellingen zonder omgevingsvariabele via een alleen-lezen `local-production-linux.json`:
      `callbackBackoffOptions.retries: 8`, `docbuilderEnable: false`, `aiSettings.enable: false`.
      Dat bestand laat het startscript van DS met rust [gemeten met `docbuilderEnable`]. Of `retries`
      en `aiSettings` zo werken, controleert de spike.
30. **install.sh blijft één commando, en kan het platform niet breken.**
    - Ontbrekende sleutels worden **toegevoegd**, nooit overschreven: `OFFICE=aan`,
      `OFFICE_JWT_SECRET`, `OFFICE_LINK_SECRET`. `OFFICE=uit bash install.sh` zet office uit.
    - Vóór het ophalen van het image: de opslagdriver is `overlay2` of `overlayfs` (M8; bij `vfs`
      wordt het image vele malen groter [gemeten: 3,1 GB uitgepakt]), en er is ≥ 8 GB vrij voor
      Docker (oud en nieuw image even naast elkaar). Niet goed → office blijft zoals het was, met
      uitleg. Nooit een aanpassing op de host voorstellen die de beveiliging verzwakt.
    - Het image ophalen op digest: eerst Docker Hub, dan `mirror.gcr.io` (zelfde digest; Docker Hub
      gaf soms 429 [gemeten]). Lukt geen van beide: draait er al een DS, dan blijft die. Zo niet, dan
      start het platform zonder office en zegt het script dat.
    - Vóór het vervangen van de app: `app.cli office-checkpoint` in de draaiende app. Een oudere app
      kent dat commando niet; dan gaat het script gewoon door.
    - Eerst de app vervangen en wachten tot `/health` antwoordt. Pas dan DS bijwerken (alleen als
      zijn image of instellingen veranderden). Op DS wacht het script niet: die is in 5–15 s gezond
      [gemeten], en de app meldt het zelf.
    - Oude images van DS weghalen die geen container meer gebruikt (`docker image prune` doet dat
      niet, want ze hebben nog een tag).
    - Het script eindigt met `[ "${INSTALL_LIB:-}" = 1 ] || main "$@"`, zodat een test de functies
      kan laden zonder iets te installeren.
31. **CI.**
    - **infra:** `docker compose config -q` voor de combinaties (zonder office, met office, met
      office en grenzen). De images komen uit `docker compose … config --images`; het DS-image
      wordt nagekeken met pogingen en `mirror.gcr.io` als terugval. `test_install_env.sh`.
    - **e2e:** draait ook `-m office` (met de nep-DS), in elke run.
    - **office (nieuw):** de echte DS 10.0.0.1, als harde poort (§4.5).
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
      - een `role="status"`-regio: "Verbinden…", "Bewerken met Bram", "Opgeslagen om 10:42",
        "Verbinding weg".
    - Uit de iframe kom je met **Alt+Shift+Q** (focus naar de kopbalk; de iframe is van dezelfde
      origin, dus de pagina kan daar luisteren), met de sluitknop van DS (`customization.close` met
      `onRequestClose`) en met Terug in de browser. Of DS Alt+Shift+Q zelf gebruikt en of
      `customization.close` in 10.0 werkt, test de spike [niet getest].
    - De iframe krijgt `title="Editor: <naam>"`. Na `onDocumentReady` krijgt hij de focus. Bij sluiten
      gaat de focus terug naar waar je vandaan kwam.
    - Taal (`lang` nl of en), thema (licht, donker of systeem) en `type: "mobile"` op een smal
      scherm gaan mee naar DS [niet getest: de precieze waarden van `uiTheme`].
    - Bij het verlaten van de pagina: `destroyEditor()`.
33. **Geen nieuwe web-afhankelijkheden, en de CSP van de app blijft.**
    - `api.js` laadt de pagina tijdens het draaien van `/office/`; dat telt niet mee voor het
      budget. De editorpagina is een lazy chunk (< 50 KB gzip; verwacht ±8 KB). Bestanden groeit
      met het menu "Nieuw" (< 1 KB).
    - **De CSP van de app verandert niet.** `api.js` werkt onder `WEB_CSP` zonder één melding (M4).
      Geen terugval met `style-src-attr 'unsafe-inline'` en geen eigen hostpagina.
    - `pwa/routing.ts`: `NEVER` krijgt `office(\/|$)`. De service worker van de app komt nooit aan
      `/office/`; DS heeft zijn eigen worker onder `/office/<versie>/`.
    - `vite.config.ts`: `"/office": { target: "http://localhost:8000", ws: true }`. Voor de tunnel
      draait de API in dev met `python -m app.serve` (gewone uvicorn heeft onze `Tunnel` niet).

### Schema en wat we schrappen

34. **Eén migratie 0007**, gemaakt door O0: de tabel `office_session` en twee kolommen op `blob`
    (`pdf_state`, `pdf_version`). Gewone `ADD COLUMN`, geen batch (zoals 0006). Met downgrade.
    Geen kolom op `version`: de versielijst toont al wie de vorige inhoud verving (`replaced_by`).

**Geschrapt in fase 4** (bewust, met reden):

- DS voor wie niet mag bewerken, een alleen-lezen-modus van DS en DS via een publieke link. Het
  PDF-voorbeeld doet dat zonder DS bloot te stellen (Keuze 9).
- Bewerken van ODF, doc/xls/ppt/rtf, macroformaten en pdf.
- WOPI en Collabora-code. Alleen de naad, de contracttests en het document (Keuze 3).
- Een eigen hostpagina, `sandbox` in de CSP van DS en `credentialless`: gemeten, en ze schermen
  niet af (M4–M6).
- "Opslaan als PDF in de map" en "omzetten naar docx" (fase 10).
- De titel in een open editor bijwerken bij hernoemen (`command meta`).
- Directe haken in `shares.py`, `groups.py` en `auth.py` voor het intrekken van rechten. De ronde
  doet het binnen 30 s, de tunnel bij uitloggen binnen 60 s.
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
   van DS ±5 minuten 1–2 cores [gemeten]. Uit: `OFFICE=uit` in `/opt/platform/.env` en het script
   opnieuw draaien. Bij nee start het script DS pas na `OFFICE=aan`.
3. **Het restrisico van DS op hetzelfde adres accepteren?** (Keuze 9) Standaard: **ja**. Een fout in
   DS kan dan handelen namens een ingelogde bewerker. We beperken het: alleen bewerkers draaien DS,
   DS onveranderd en vastgepind, plugins en macro's uit, een CSP die uitlekken naar buiten
   tegenhoudt. In fase 8 komt als optie een eigen hostnaam (`office.<domein>`) met een tweede
   certificaat; dat vraagt een DNS-record van jou. Bij nee: bewerken pas met domein en eigen
   hostnaam, dus niet in het LAN zonder domein.
4. **Hoeveel versies maakt de editor?** (Keuze 17) Standaard: **één per bewerksessie, plus één per
   30 minuten in een lange sessie**. Tussentijds opslaan (de knoppen, elke 5 minuten) werkt het
   bestand bij, maar maakt daarnaast geen eigen versie. Zo blijft de inhoud van vóór het bewerken
   altijd terug te halen. Bij "elke opslag een versie" duwt één middag bewerken alle geschiedenis
   eruit (max. 20).
5. **De Collabora-adapter nu bouwen?** Standaard: **nee, alleen klaarzetten** (Keuze 3): de naad,
   de motor-neutrale contracttests en `docs/fase-4/collabora.md`. Omschakelen kost dan later ±1 week.
   Bij ja: ±3 dagen extra (een WOPI-host zonder locks, een nep-Collabora, en de echte CODE één keer
   in de sandbox), en dat past niet in twee weken naast de rest.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Naam | Grootte | Draait |
|---|---|---|---|
| **O0** | Fundament en spike (§3) | L | **eerst, alleen.** Werkers: O0-api (met de spike op dag 1), O0-web, daarna O0-e2e |
| O1 | Toegang: proxy, tunnel en poort-cookie | M | parallel |
| O2 | Bewerken: sessies, opslaan, herstel, editorpagina | XL | parallel |
| O3 | Voorbeelden en PDF via conversie | M | parallel |
| O4 | Nieuw document | S | parallel |
| O5 | Beheer en bewaking | M | parallel |
| O6 | Uitrol: compose, install.sh, CI, runbook | M | parallel |

Elk pakket werkt in een eigen git-worktree vanaf de tag `fase4-basis` (na O0).

**Eigendom na O0.** "Frozen" betekent: daarna verandert alleen de *O0-hotfix-lane* het, met een
kleine PR op de basis waar iedereen op rebaset.

**API** (paden onder `api/`)

| Bestand | Eigenaar |
|---|---|
| `app/models.py` (`OfficeSession`, `Blob.pdf_state`, `Blob.pdf_version`), `migrations/versions/0007_office.py`, `app/config.py`, `app/serve.py`, `app/main.py`, `app/routers/files.py` (alleen `place_file`), `app/previews.py` (alleen de twee haakregels, §3.5), `app/routers/admin.py` (alleen de meldingen-haak), `app/office/{__init__,engine,keys,state,grants,runtime,urls}.py`, `app/office/oo/{__init__,tokens,client}.py`, `app/routers/office_contracts.py`, `tests/fake_docserver/**`, `tests/office_contract.py`, tests `test_migration_0007.py`, `test_office_tokens.py`, `test_place_file_exact.py`, `test_serve_upgrade.py`, `test_office_imports.py`, `test_fake_docserver.py` | O0 (frozen) |
| `app/office/proxy.py`, `app/office/tunnel.py`, `app/office/gate.py`, `app/office/oo/paths.py`, tests `test_office_paths.py`, `test_office_proxy.py`, `test_office_tunnel.py`, `test_office_gate.py` | O1 |
| `app/office/sessions.py`, `app/office/policy.py`, `app/office/saving.py`, `app/office/sweep.py`, `app/office/recovery.py`, `app/office/oo/editor.py`, `app/routers/office.py`, `app/routers/office_internal.py` (`doc`, `callback`), `app/cli.py`, tests `test_office_sessions.py`, `test_office_policy.py`, `test_office_callback.py`, `test_office_races.py`, `test_office_sweep.py`, `test_office_recovery.py`, `test_office_contract.py`, `test_cli_office.py` | O2 |
| `app/office/convert.py`, `app/office/oo/convert.py`, `app/routers/office_pdf.py` (export en de interne `src`), `app/render.py` (alleen de bron van de pdf-tak), `app/routers/viewer.py`, tests `test_office_convert.py`, `test_viewer_office.py`, `test_office_pdf.py` | O3 |
| `app/office/blank.py`, `app/routers/office_new.py`, tests `test_office_blank.py`, `test_office_new.py` | O4 |
| `app/office/monitor.py`, `app/office/oo/admin.py`, `app/routers/admin_office.py`, tests `test_office_monitor.py`, `test_admin_office.py` | O5 |

O0 zet elke module van een ander pakket als stub met de signaturen van §3.3, zodat alles
compileert en de app zonder office precies fase 3 is.

**Web** (paden onder `web/src/`)

| Bestand | Eigenaar |
|---|---|
| `routes.tsx`, `App.tsx` (haak `useOfficeNotices`), `pwa/routing.ts`, `../vite.config.ts`, `browser/actions.ts` (haken `edit` en `pdf`), `Files.tsx` (menuknop "Nieuw" met haak, sneltoets O), `viewer/Viewer.tsx` (haak in de werkbalk), `Beheer.tsx` (zevende tabblad), `office/types.ts`, `office/api.ts`, `office/index.ts`, `i18n/errors/office.ts`, `i18n/types.ts` (gebied `office`) | O0 (frozen) |
| `office/EditorPage.tsx`, `office/editor.ts`, `office/loadApi.ts`, `office/gate.ts`, `office/notices.ts`, `office/actions.ts`, `office/office.css`, `office/{editor,actions,notices}.test.ts`, `i18n/areas/office.*` | O2 |
| `viewer/**` behalve `Viewer.tsx`, `office/pdf.ts`, `office/pdf.test.ts`, `i18n/areas/viewer.*` | O3 |
| `office/newItems.ts`, `office/newItems.test.ts`, `i18n/areas/browser.*` | O4 |
| `admin/OfficeTab.tsx`, `admin/format.ts`, `admin/office.test.ts`, `i18n/areas/admin.*` | O5 |

**Repo**

| Bestand | Eigenaar |
|---|---|
| `e2e/conftest.py`, `e2e/pytest.ini`, `e2e/office_harness.py`, `docs/fase-4/e2e.md`, `docs/fase-4/spike.md` | O0 (frozen) |
| `infra/single/**`, `.github/workflows/ci.yml`, `docs/runbooks/office.md`, `docs/fase-4/collabora.md` | O6 |
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
9. UI-tekst via `t()` in de eigen gebieden, met Engels. Alles bereikbaar met het toetsenbord, met een
   checklist in `docs/fase-4/toetsenbord-<pkg>.md`.
10. Commentaar en commitberichten in het Nederlands.

**Afhankelijkheden tussen pakketten** (alleen via de contracten en stubs van O0):

| Wie | Gebruikt | Gemaakt door |
|---|---|---|
| O2, O3, O5 | `engine.current()`, `oo.client.DocServer`, `state.OfficeState`, `oo.tokens.*`, `urls.*`, `grants` | O0 |
| O2 (e2e) | proxy en tunnel | O0 (de kern, alleen met `PLATFORM_OFFICE_DEV_UNGATED=1`); O1 maakt ze af |
| O2 | `gate.issue(response, user, family)` | O0 (signatuur, zet het cookie al); O1 (de controle) |
| O3, O5 | `sessions.find_by_key` | O2 (O0-stub) |
| O5 | `recovery.restore(key)`, `recovery.forget(key)` voor de knoppen op `/beheer` | O2 (O0-stub) |
| O5 | de tellers van de tunnel in `state` | O1 |
| O4 (web) | het openen van de editor (`/bewerken/<id>`) | O2 (O0 zet een stubpagina) |
| O6 | `app.cli office-checkpoint` | O2 (O0-stub die niets doet en 0 teruggeeft) |

---

### O1 — Toegang: proxy, tunnel en poort-cookie

**Doel.** Alles onder `/office/` veilig doorgeven aan DS: alleen toegestane paden, alleen voor wie
mag, met de juiste koppen, over http én WebSocket, en tellen.

**Maakt**
- `app/office/oo/paths.py`: het padbeleid van §3.10 als pure functie
  `classify(method, path, upgrade) -> PathClass | None`.
  - Eerst normaliseren: prefix `/office` eraf, een versiesegment `^\d+\.\d+\.\d+-[0-9a-f]{8,64}$`
    eraf, hoofdletters van de map niet uitmaken.
  - Weigeren: `..`, `.`, lege segmenten, `\`, NUL, en procent-codes die een `/`, `\` of `.` worden.
  - De lijst komt uit de spike (§3.7) en ligt daarna vast.
- `app/office/gate.py`: `issue()` (O0-signatuur) en `check(headers, key=None) -> GateUser | None`,
  met een cache van 60 s (sleutel: sha-256 van het cookie). Met een key ook het lidmaatschap van de
  sessie.
- `app/office/proxy.py` maakt O0's kern af: de koppen van Keuze 5, `Location`, de 100 MB, de CSP van
  Keuze 9 op HTML, de vlag `PLATFORM_OFFICE_DEV_UNGATED` weg, en een logregel bij een geweigerd pad
  (`office: pad geweigerd <klasse> <pad>`), zodat een nieuw pad van een nieuwe DS opvalt.
- `app/office/tunnel.py` maakt O0's kern af: `Origin`, poort-cookie en lidmaatschap vóór de upgrade
  naar DS; de regel voor poort 80; tegendruk; idle; de controle elke 60 s; het register met
  `counts()`, `close_for(key, user)` en `close_all()`.

**Wijzigt.** Niets buiten de eigen bestanden. O0 hing de middleware al in `main.py` en zette `ws=Tunnel`
en de fix van M1 in `serve.py`.

**Gedrag** (geen nieuwe eindpunten):
```
/office/<statisch pad>            GET|HEAD, zonder cookie                         → DS
/office/cache/files/data/…        GET, cookie                                     → DS
/office/<V>/doc/<key>/c/…         GET|POST (polling), cookie + lid van <key>      → DS
/office/<V>/doc/<key>/c/ upgrade  tunnel: Origin, cookie, lid van <key>           → DS
/office/<V>/<editoractie>         zie §3.10, cookie (+ lid bij een key in het pad) → DS
al het andere onder /office       404 not_found (en een logregel)
geen of ongeldig cookie           401 office_gate
upgrade: verkeerde Origin 403, geen cookie of geen lid 401, ander pad 404 — vóór er iets naar DS gaat
```

**Tests**
- `test_office_paths.py`: elke klasse; het versieprefix; hoofdletters; `%2e%2e`, `%2f`, dubbele
  slashes; `command` achter een versieprefix; `/office/` zelf (welkomstpagina met
  `Clear-Site-Data`); alle paden uit de proxylogs van de proef (vaste lijst in
  `tests/fixtures/ds_paths.txt`) mogen.
- `test_office_proxy.py` (nep-DS als ASGI-app): koppen in en uit als vaste verwachting; `Cookie`
  gaat nooit naar DS; `Set-Cookie`, `Clear-Site-Data` en `Service-Worker-Allowed` komen nooit terug;
  `Location` naar een vreemde host weg; body boven 100 MB 413; een antwoord van 50 MB in stukken met
  begrensd geheugen; gzip-bytes ongewijzigd; DS weg → 502.
- `test_office_tunnel.py` (echte sockets, zoals `test_serve.py`): een upgrade via de h11- en de
  httptools-luisteraar naar een kleine WebSocket-echo; de verbinding leeft langer dan
  `HEADER_TIMEOUT` (in de test 0,2 s); ook over https met een zelfgetekend certificaat; vreemde
  Origin 403; geen cookie 401; ander pad 404; boven de grens 503; stil na de idle-tijd dicht;
  tegendruk met een trage lezer; uitloggen sluit binnen de controle; `counts()` en `close_all()`.
- `test_office_gate.py`: geen cookie, verlopen, andere `typ`, office-geheim in plaats van
  app-geheim, uitgelogd (family ingetrokken), geblokkeerd, geen lid van de sessie, cache van 60 s.
- e2e `test_office_o1.py` (nep-DS): twee contexts openen de nep-editor; tekst van de één verschijnt
  bij de ander via de tunnel; een tweede browser zonder login krijgt 401 op `cache/files` en een
  geweigerde upgrade; `/office/command` en `/office/converter` geven 404.

**Klaar als**
- Elk pad uit de inventaris van de spike gaat door; elk ander pad geeft 404.
- Zonder login kom je bij geen enkel dynamisch pad van DS, ook niet via de tunnel.
- De editor werkt via `/office/` op http én https, met de echte DS (spike en §4.5).

---

### O2 — Bewerken: sessies, opslaan, herstel en de editorpagina

**Doel.** Openen, samen bewerken, opslaan als nieuwe versie, en alle randgevallen van Keuze 14–21.

**Maakt (API)**
- `app/office/sessions.py`: `open_session(session, user, node) -> (OfficeSession, stale)` met de
  regels van Keuze 14 en 22; `find_by_key`; `close`.
- `app/office/policy.py`: pure functies zonder database, met tabeltests:
  - `make_version(row, current_sha, now, cfg) -> bool` (Keuze 17);
  - `choose_actor(history, actions, members, can_write) -> UUID | None` (Keuze 16, stap 6);
  - `target(node_alive, actor, owner_alive) -> "original" | "copy" | "none"` (Keuze 16, stap 7–8);
  - `recovery_name(name, when, locale) -> str`.
- `app/office/saving.py`: `store(row, tmp, sha, size, event) -> Outcome`
  (`saved | same | copy | retry | refused`). Het recept van Keuze 16, zonder OnlyOffice.
- `app/office/sweep.py`: de ronde van Keuze 20 (`jobs.housekeeping("office.sweep", …, 30)`), met
  het tussentijds opslaan van Keuze 18, en `checkpoint_all(wait)`.
- `app/office/recovery.py`: het herstel van Keuze 21 (bij de start en elke 10 minuten), plus
  `restore(key)` en `forget(key)` voor de knoppen van O5.
- `app/office/oo/editor.py`: de editorconfig bouwen en tekenen; een callback lezen tot een
  motor-neutrale `CallbackEvent` (§3.3); `info`, `forcesave` en `drop`.
- `app/routers/office.py`: `status`, `files/{id}`, `files/{id}/session`, `files/{id}/save`, `gate`.
- `app/routers/office_internal.py`: `doc/{key}` en `callback` (op de interne app).
- `app/cli.py`: `office-checkpoint [--wait 30]` (voor `install.sh`).
- Bij het stoppen van de app (lifespan): `checkpoint_all(wait=5)`.
- Gebeurtenissen `office.unsaved`, `office.recovered` en `office.dropped` (§3.4).

**Maakt (web)**
- `office/EditorPage.tsx` (route `/bewerken/:id`, lazy):
  - vraagt `POST …/session`, laadt `api.js` via `loadApi.ts` (een `<script src>` van `/office/`,
    één keer per pagina), maakt `new DocsAPI.DocEditor("office-editor", config)` met de events
    `onAppReady`, `onDocumentReady`, `onDocumentStateChange`, `onError`, `onWarning`,
    `onRequestClose`;
  - de kopbalk van Keuze 32: `<h1>`, Sluiten, Nu opslaan, de statusregio;
  - meldingen bij `stale`, `office_too_many`, `office_unavailable`, `office_busy` (één keer vanzelf
    opnieuw na 3 s), `office.dropped` ("Je hebt geen toegang meer; de editor is gesloten"),
    `office.unsaved` met `final: false`, en waarschuwing -18;
  - Alt+Shift+Q in de iframe zet de focus op Sluiten. Sluiten gaat terug naar waar je vandaan kwam
    (anders naar de map van het bestand);
  - vernieuwt het cookie elke 30 minuten (`gate.ts`);
  - luistert naar `node.changed` voor dit bestand ("Opgeslagen om …");
  - `destroyEditor()` bij het verlaten.
- `office/actions.ts`: wanneer "Bewerken" er is (formaat, `canWrite`, office aan), voor de haak in
  `browser/actions.ts` en de knop in de viewer.
- `office/notices.ts`: de meldingen bij `office.unsaved` en `office.recovered` (voor de haak in
  `App.tsx`), met een link naar de herstelkopie.
- `i18n/areas/office.nl.ts` en `.en.ts`.

**API**
```
GET  /api/v1/office/status
     → { enabled: bool, editing: bool, state: "off"|"starting"|"ok"|"down",
         edit_formats: ["docx","xlsx","pptx"], new_kinds: ["docx","xlsx","pptx"], max_bytes: 104857600 }
GET  /api/v1/office/files/{id}
     → { can_edit: bool,
         reason: null|"read_only"|"format"|"too_large"|"trashed"|"album"|"office_off"|"office_down"|"too_many",
         open: { editors: int } | null, pdf_state: null|"pending"|"ready"|"failed"|"none" }
POST /api/v1/office/files/{id}/session   body { type?: "desktop"|"mobile", theme?: "light"|"dark" }
     → 200 { api_url: "/office/web-apps/apps/api/documents/api.js", config: {…, token}, key: str,
             stale: bool, gate_seconds: 7200 }   + Set-Cookie: office=…
     fouten: 404 not_found; 403 read_only | guest (gast-eigenaar); 422 not_a_file | album | trashed
             | office_format | office_too_large; 409 office_busy; 429 office_too_many {max};
             503 office_off | office_unavailable | office_misconfigured
POST /api/v1/office/files/{id}/save      → 202 {}   (deelnemer met schrijfrecht; 404 als er geen open sessie is)
POST /api/v1/office/gate                 → 204 + Set-Cookie (alleen met een open sessie waar je lid van bent; anders 404)

Intern (poort 8081):
GET  /internal/office/doc/{key}   Authorization: Bearer <DS-download-JWT>
     → 200 de blob van de sessie (application/octet-stream, Repr-Digest: sha-256=:<base64>:)
       401 office_token; 404 not_found
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
                                       "close": { "visible": true, "text": "Sluiten" } } },
  "token": "<HS256>" }
```
- De spike controleert `close`, `uiTheme`, `macros`, `macrosMode` en `plugins` tegen 10.0. Een
  sleutel die DS niet kent, gaat eruit.

**Tests**
- `test_office_sessions.py`: de rechtentabel van Keuze 22 rij voor rij (eigenaar, `write`-deling als
  gebruiker en als gast, `read` 403, album 422, prullenbak 422, zonder 2FA 403
  `totp_setup_required`, geblokkeerd 401, gast-eigenaar 403); twee gelijktijdige openingen
  (`asyncio.gather`) geven één key; hergebruik (pending binnen 2 min, open met `info` 0, open met
  `info` 1 → nieuwe key, dicht → nieuwe key); de config is getekend en de handtekening dekt
  `document.url`, `callbackUrl`, `permissions` en `mode`; `office_too_many` bij de grens;
  `office_busy` tijdens `saving`; `stale` na een upload.
- `test_office_policy.py`: tabellen voor `make_version`, `choose_actor`, `target`, `recovery_name`.
- `test_office_callback.py` (de test speelt DS met het office-geheim):
  - status 1 werkt `connected` bij; een onbekende `ds_uid` geeft alleen een logregel;
  - status 2 → één nieuwe versie, `replaced_by` = de laatste bewerker, inhoud = wat de nep-link gaf;
  - status 6, 6, 2 binnen 30 minuten → één nieuwe versie (squash), en de key blijft na elke 6;
  - status 6 na 31 minuten (klok verzet) → een tweede versie;
  - 30 keer status 6 binnen 30 minuten laten de oudste versies staan;
  - dezelfde callback twee keer → geen tweede versie;
  - status 3 met `url` → herstelkopie naast het origineel, het origineel ongewijzigd;
  - token alleen in de header, alleen in de body, verkeerd geheim, verlopen, `operation: download`
    op de callback, `alg: none`, ontbrekende `operation` → 401 `office_token`;
  - losse body-velden die afwijken van het token worden genegeerd;
  - een `url` met een vreemde host, een andere key in het pad, of `..` → nooit opgehaald (de
    `MockTransport` faalt op elke andere host);
  - database bezet (geïnjecteerd) → 503; een download die halverwege stopt → 503 en geen wijziging.
- `test_office_races.py`: elke rij van Keuze 19 als test.
- `test_office_sweep.py`: pending verloopt; `info` 1 sluit; ingetrokken en geblokkeerde gebruikers
  krijgen `drop` (de nep-DS schrijft het commando op); tussentijds opslaan na 5 minuten, en
  `error 4` is goed.
- `test_office_recovery.py`: forgotten met dezelfde sha → alleen weg; ongewijzigd bestand → gewoon
  opgeslagen; veranderd, weg of zonder schrijver → herstelkopie; quotum vol → blijft staan en
  meldt; bij de start van de app.
- `test_office_contract.py`: de scenario's van §4.4 met de nep-DS.
- `test_cli_office.py`: `office-checkpoint` stuurt forcesave voor open sessies en wacht.
- vitest: `editor.test.ts` (statusovergangen, het vernieuwen van het cookie, de foutteksten),
  `actions.test.ts` (wanneer Bewerken zichtbaar is), `notices.test.ts`.
- e2e `test_office_o2.py` (nep-DS): §4.4, punten 1, 2, 3, 6.
- e2e `test_office_o2_real.py` (echte DS, marker `office_real`): §4.5, punten 1 en 4.

**Klaar als**
- Twee gebruikers bewerken via de nep-DS tegelijk één docx. Na sluiten is er precies één nieuwe
  versie met de tekst van allebei. Hetzelfde met de echte DS in CI.
- Elke rij van de tabellen in Keuze 15 en 19 heeft een test.
- Een forgotten file na een herstart van de app staat binnen 10 minuten weer in de app.
- Alleen met het toetsenbord: openen (E, of Enter op Bewerken), Nu opslaan, Sluiten, Alt+Shift+Q.

---

### O3 — Voorbeelden en PDF via conversie

**Doel.** Een echt voorbeeld van office-bestanden zonder de editor te openen, ook voor doc, xls,
ppt en rtf, en PDF-export.

**Maakt**
- `app/office/convert.py`: de taak `office.pdf` (payload `{sha}`) en de inhaalronde
  `office.pdf.backfill` (payload `{after}`, in stukken van 200, `jobs.chain`, na de nieuwe uploads).
  - eerst de gezondheid; niet gezond → zichzelf over 60 s opnieuw (telt niet als poging);
  - de PDF er al, met de juiste `pdf_version` → alleen de thumbnails opnieuw;
  - anders: grant maken, `engine.convert(...)`, `fileUrl` herschrijven en ophalen, opslaan (Keuze 25);
  - de thumbnails 256 en 1024 uit pagina 1 in het renderproces; `preview_meta.sizes` bijwerken;
    `previews.announce` (dan ziet de lijst de nieuwe thumbnail);
  - `blob.pdf_state` en `blob.pdf_version` bijwerken;
  - `on_preview_done(session, blob)` en `on_discard(blobs, sha)` voor de haken van O0.
- `app/office/oo/convert.py`: `POST /converter` (`async: true`, `Accept: application/json`,
  `filetype`, `outputtype: "pdf"`, `key`, `title`, `url`), elke seconde opnieuw tot `endConvert`,
  hooguit `office_convert_timeout`; de foutcodes naar `ConvertError(code, transient)`.
- `app/routers/office_pdf.py`:
  - `POST /api/v1/office/files/{id}/pdf` (lezen genoeg) → `{url, expires_in}`;
  - `GET /api/v1/office/pdf/{id}?token=` → `application/pdf`, `attachment; filename="<stam>.pdf"`
    (download-token, `active_user`, toegang per verzoek, net als `/download`);
  - intern: `GET /internal/office/src/{grant}` (download-JWT van DS; de grant moet bestaan).
- web: in de viewer bij een omgezet document het label "Voorbeeld" en de knop "Downloaden als PDF";
  zolang `pdf_state` `pending` is, de eenvoudige weergave met "Voorbeeld met opmaak wordt gemaakt…";
  `office/pdf.ts` voor de actie in de lijst (haak van O0 in `actions.ts`, groep `convert`).

**Wijzigt (exclusief)**
- `app/render.py`: de pdf-tak kan een ander bronpad krijgen (de omgezette PDF) dan de blob zelf.
- `app/routers/viewer.py`: `_document` geeft bij een PDF met `pdf_state = ready` `kind: "pdf"`,
  `source: "office"`; `docinfo` en `_page` lezen dan de omgezette PDF. De paginacache blijft
  `previews/<sha>/pages/`. De publieke varianten volgen vanzelf. Een eerdere versie zonder PDF: de
  taak komt vooraan en het antwoord zegt `pdf_state: "pending"`.

**API**
```
GET  /api/v1/files/{id}/document   → DocumentOut + source: "pdf"|"office"|null, pdf_state   (optionele velden erbij)
POST /api/v1/office/files/{id}/pdf → { url: "/api/v1/office/pdf/{id}?token=…", expires_in: 600 }
     fouten: 404, 422 office_format, 409 pdf_pending, 503 office_unavailable, 502 office_convert_failed {code}
GET  /api/v1/office/pdf/{id}?token= → application/pdf
```

**Tests**
- `test_office_convert.py` (nep-DS): docx, xlsx, pptx, odt, doc, xls, ppt, rtf → PDF en thumbnails;
  `doc.json` blijft; dezelfde sha in twee bestanden → één conversie; de nep-DS geeft bij hergebruik
  van een key het oude resultaat (zoals de echte), en onze key per sha voorkomt dat; DS niet gezond →
  opnieuw zonder poging; -3 blijvend; -8 geeft de melding; een `fileUrl` met een vreemde host →
  geweigerd; een grant verloopt; **een nieuwe preview gooit de PDF niet weg** (M10); de blob verdwijnt
  tijdens het omzetten → niets blijft achter; `collect_garbage` haalt de PDF weg; de inhaalronde
  loopt over 450 blobs in drie stukken; zonder office wordt niets klaargezet.
- `test_viewer_office.py`: `/document` met en zonder PDF; pagina's van de omgezette PDF; een
  publieke link ziet de pagina's; een ingetrokken deling stopt de pagina's meteen.
- `test_office_pdf.py`: downloaden als PDF met leesrecht; 409 `pdf_pending` en daarna 200; een
  verlopen token.
- e2e `test_office_o3.py` (nep-DS): §4.4 punt 4 en 8.
- e2e `test_office_o3_real.py` (`office_real`): §4.5 punt 2.

**Klaar als**
- "Office-preview werkt zonder de editor te openen": bewezen met de nep-DS en, in CI, met de echte DS.
- Zonder DS precies het gedrag van fase 3 (de bestaande `test_office.py` en `test_viewer.py` slagen).

---

### O4 — Nieuw document

**Doel.** Een leeg docx, xlsx of pptx maken in de huidige map en meteen openen.

**Maakt**
- `app/office/blank.py`: `blank(kind, locale) -> bytes` (Keuze 24).
- `app/routers/office_new.py`: `POST /api/v1/office/new`.
- web: `office/newItems.ts` (de items voor het menu "Nieuw" en het contextmenu van de map, via de
  haak van O0); na het maken naar `/bewerken/<id>`.

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
  onderdeel; de B21-lezer leest alle drie; twee keer maken geeft dezelfde bytes.
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
terugzetten.

**Maakt**
- `app/office/monitor.py`: gezondheid elke 30 s; `version`, `license` en `getForgottenList` elke
  10 minuten; de piek; de overgang naar "weer gezond" (start de inhaalronde van O3);
  `notices(state, session) -> list[Notice]` voor de haak van O0 in `admin.system_info`.
- `app/office/oo/admin.py`: `healthcheck`, `version`, `license`, `getForgottenList`,
  `getForgotten`, `deleteForgotten` achter `Engine`.
- `app/routers/admin_office.py` (alleen `AdminUser`).
- `OfficeConfig` in `Setting` met sleutel `office`, in het geheugen zoals `SecurityPolicy`.
- web: `admin/OfficeTab.tsx` (tabblad 7, Alt+7), `admin/format.ts` (`NOTICES`, `JOB_TYPES`
  `office.pdf` en `office.pdf.backfill`), teksten in `i18n/areas/admin.*`. Het tabblad zegt ook
  hoe je office aan- of uitzet (het commando in de console).

**API**
```
GET  /api/v1/admin/office → OfficeAdmin {
       configured: bool, enabled: bool, state: "off"|"starting"|"ok"|"down", down_since: str|null,
       version: str|null,                         # command version
       license_connections: int|null,             # command license; ≥ 2147483647 = "geen limiet"
       max_editors: int, connections: int, people: int, documents: int,
       peak: { connections: int, at: str } | null,  # sinds de start van de app
       open: [ { name, owner, editors: [display_name], since } ],
       unsaved: [ { key, name, owner: str|null, at: str|null, reason: str|null } ],
       conversions: { queued: int, failed: int } }
PUT  /api/v1/admin/office  body { enabled: bool, max_editors: int (1–500),
                                  checkpoint_minutes: int (1–60), version_minutes: int (5–1440) } → OfficeAdmin
POST /api/v1/admin/office/checkpoint → { sent: int, saved: int }
POST /api/v1/admin/office/unsaved/{key}/restore → NodeOut      (404 not_found; 409 office_owner_gone; 413 no_room)
DELETE /api/v1/admin/office/unsaved/{key} → 204
```
- Meldingen: `office_down` (> 2 min onbereikbaar), `office_misconfigured`, `office_version`
  (DS < 10.0, of een andere versie dan vastgepind), `office_limit` {used, max} (≥ 80 %),
  `office_unsaved` {count}.

**Tests**
- `test_office_monitor.py`: licentie 2147483647 → geen limiet; licentie 20 (nep-DS) → melding vanaf
  16; een andere versie → `office_version`; forgotten files → melding; gezond ↔ weg, en de
  inhaalronde start bij "weer gezond".
- `test_admin_office.py`: alle velden met de nep-DS; niet-beheerder 403; grens opslaan en meteen
  gebruikt; terugzetten en verwijderen; eigenaar weg → 409.
- e2e `test_office_o5.py`: §4.4 punt 7.

**Klaar als:** het tabblad toont de echte verbindingen (2 tijdens de test met twee gebruikers);
terugzetten werkt; alle meldingen zijn vertaald.

---

### O6 — Uitrol: compose, install.sh, CI en runbook

**Doel.** Office aan met hetzelfde ene commando, en bijwerken zonder dat het platform breekt.

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
  "aiSettings": { "enable": false }
}
```
- De sleutels staan zo in `default.json` van het image [image]. `docbuilderEnable` werkte via dit
  bestand [gemeten]. `callbackBackoffOptions` en `aiSettings` controleert de spike (8 pogingen tellen
  in de log van de nep-app).
- Geen `ALLOW_PRIVATE_IP_ADDRESS`: de standaard (uit) blijft. Geen `autoAssembly`: de app slaat zelf
  tussentijds op (Keuze 18).

**Wijzigt `install.sh`**

```bash
OFFICE_REPO=onlyoffice/documentserver
OFFICE_DIGEST=sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3

hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
add_key() { grep -q "^$1=" "$ENV_FILE" || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }
set_key() { sed -i "s/^$1=.*/$1=$2/" "$ENV_FILE"; }

# Na het maken of lezen van .env (OFFICE uit de omgeving gaat voor):
want_office=${OFFICE:-}
add_key OFFICE aan
add_key OFFICE_JWT_SECRET "$(hex)"
add_key OFFICE_LINK_SECRET "$(hex)"
[ -n "$want_office" ] && set_key OFFICE "$want_office"
chmod 600 "$ENV_FILE"
. "$ENV_FILE"

# Het image op digest: eerst Docker Hub, dan de mirror
office_image() {
  local hub="$OFFICE_REPO@$OFFICE_DIGEST" mirror="mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST" img
  for img in "$hub" "$mirror"; do
    docker image inspect "$img" >/dev/null 2>&1 && { echo "$img"; return 0; }
  done
  for img in "$hub" "$mirror"; do
    for _ in 1 2 3; do docker pull -q "$img" >/dev/null 2>&1 && { echo "$img"; return 0; }; sleep 5; done
  done
  return 1
}
```
Stappen, in deze volgorde:
1. **Controle:** `docker info --format '{{.Driver}}'` is `overlay2` of `overlayfs` (M8), en
   `df --output=avail -B1G /var/lib/docker` geeft ≥ 8. Niet goed: melding met uitleg, en office
   blijft zoals het was.
2. **Image:** `OFFICE_IMAGE=$(office_image)`. Mislukt: draait de container `platform-office-1`, dan
   `OFFICE_IMAGE` = zijn huidige image ("office blijft op de vorige versie"). Anders office voor deze
   keer uit ("het platform werkt zonder editor; draai het script later opnieuw").
3. **Bestanden:** `-f compose.yaml`, plus `-f compose.office.yaml` als office meedoet, plus
   `-f compose.office-limits.yaml` als `docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}'`
   `true true` geeft.
4. **Tussentijds opslaan:** `"${compose[@]}" exec -T platform python -m app.cli office-checkpoint
   --wait 30 || true`.
5. **App:** `OFFICE_IMAGE=… "${compose[@]}" up -d --build --remove-orphans platform`, dan de
   bestaande wachtlus op `/health`.
6. **DS:** `OFFICE_IMAGE=… "${compose[@]}" up -d office` (alleen als office meedoet; compose
   vervangt hem alleen als er iets veranderde). Daarna oude images van DS weghalen die geen
   container meer gebruikt.
7. **Slot:** de eindtekst noemt office ("start op de achtergrond, over ±1 minuut klaar") of waarom
   het uit staat.

**Ook**
- `infra/single/.env.example`: `OFFICE=aan`, `OFFICE_JWT_SECRET=`, `OFFICE_LINK_SECRET=` (leeg; het
  script vult ze).
- `infra/single/tests/test_install_env.sh` (bash, met `INSTALL_LIB=1`, in CI): een `.env` uit fase 3
  krijgt de drie sleutels; een bestaand geheim blijft staan; `OFFICE=uit` werkt; de waarden zijn
  hex; `chmod 600`; de driver-controle accepteert `overlay2` en `overlayfs` en weigert `vfs`.
- `infra/single/README.md`: wat office is, wat het kost, aan en uit, waar de data staat, het
  herstel van forgotten files, "Office start op" en "Office onbereikbaar".
- `docs/runbooks/office.md`: bijwerken en terugrollen (een oudere digest in `OFFICE_IMAGE`);
  niet-opgeslagen werk; de digest vervangen bij een nieuwe DS (met de echte-editor-test); de
  belasting meten op de LXC; de controle dat er op de LXC niets anders luistert dan 80, 443 en SSH
  (M7).
- `docs/fase-4/collabora.md` (Keuze 3): de gemeten feiten en wat een WOPI-host vraagt. CheckFileInfo,
  GetFile, PutFile (met `X-COOL-WOPI-Timestamp` en 409 met `COOLStatusCode 1010`), locks als we die
  aanbieden, `aliasgroup1`, `ssl.termination=true`, `net.service_root=/office` (de proxy **houdt**
  het prefix), `net.proto=IPv4`, nooit `/cool/convert-to` of `/cool/adminws` van buiten, het
  welkomstvenster in elke e2e, en dat CODE "not recommended for production" is [doc, gemeten].
  Welke modules van `app/office/oo/` een Collabora-adapter vervangt. Schatting: ±1 week.
- `.github/workflows/ci.yml`: de jobs van Keuze 31. De job `office` staat in §4.5.

**Klaar als**
- In de sandbox (een Debian-container met Docker): een installatie uit fase 3 bijwerken geeft office
  aan, de geheimen in `.env`, DS gezond, de app op 80. `OFFICE=uit` haalt DS weg; opnieuw `aan`
  brengt hem terug; een geblokkeerde pull laat het platform werken.
- `test_install_env.sh` en de nieuwe CI-jobs zijn groen.

---

## 3. Fundament (O0, eerst en alleen)

O0 raakt alle hotspots en legt de contracten vast. Werkers: **O0-api** (begint met de spike, §3.7),
**O0-web** tegelijk, en daarna **O0-e2e** (harnas en nep-DS als proces). Eén PR op de
fase-4-branch, getagd `fase4-basis`. Daarna werkt de app zonder office-instellingen precies als
fase 3. Met office ingesteld zegt de stubpagina "nog niet klaar".

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
  saved_at      DATETIME NULL                     -- laatste opslag (voor het tussentijds opslaan)
  state         VARCHAR(8)  NOT NULL DEFAULT 'pending'   -- pending | open | saving | closed
  members       JSON NOT NULL DEFAULT '{}'        -- {ds_uid: user_id} wie een config kreeg
  connected     JSON NOT NULL DEFAULT '[]'        -- [ds_uid] volgens de laatste status 1 of info
  saved_url     VARCHAR(512) NULL                 -- pad van de laatst opgeslagen link (idempotent)
  last_error    VARCHAR(32) NULL                  -- no_writer | gone | no_room | owner_gone | error_status
  closed_reason VARCHAR(16) NULL                  -- saved | no_changes | lost | copy | refused
  node_name     VARCHAR(64) NOT NULL              -- settings.node_name (voor het cluster later)
  created_at    DATETIME NOT NULL
  updated_at    DATETIME NOT NULL
  closed_at     DATETIME NULL
INDEX ix_office_session_node (node_id)
INDEX ix_office_session_state (state)
UNIQUE INDEX ux_office_session_open (node_id) WHERE state <> 'closed' AND node_id IS NOT NULL
                                              -- sqlite_where en postgresql_where

blob
  pdf_state     VARCHAR(8) NULL                   -- NULL | pending | ready | failed | none
  pdf_version   INTEGER NOT NULL DEFAULT 0
INDEX ix_blob_pdf (pdf_version)
```
- `node_id` met `SET NULL`: na definitief verwijderen blijft de rij. Een late callback en het herstel
  weten dan nog wat het was.
- Geen foreign key op `blob_id` en `checkpoint`: de sessie houdt geen verwijzing vast. `ref_count`
  blijft van de bestanden en versies.
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
office_max_tunnels: int = 1000
office_dev_ungated: bool = False   # alleen O0, spike en tests: proxy en tunnel zonder poort-cookie; O1 haalt hem weg

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
def convert_key(sha: str, attempt: int = 1) -> str   # f"p{PDF_VERSION}-{sha}", vanaf poging 2 met "-a<n>"

# app/office/engine.py (de naad; zie Keuze 3)
class PathClass(StrEnum): STATIC; DATA; SESSION; SETTINGS; ACTION
@dataclass(frozen=True)
class Health: ok: bool; version: str | None; limit: int | None
@dataclass(frozen=True)
class OpenContext: key: str; ds_uid: str; user_name: str; title: str; file_type: str
    lang: str; region: str; theme: Literal["light", "dark"]; mobile: bool; doc_url: str; callback_url: str
@dataclass(frozen=True)
class CallbackEvent:
    kind: Literal["presence", "final", "failed", "closed", "checkpoint", "checkpoint_failed", "other"]
    key: str
    url: str | None            # pad en query, nog niet herschreven
    connected: list[str]       # ds_uid's
    editors: list[str]         # ds_uid's, de laatste bewerker eerst (history, dan actions)
    explicit: bool             # opslaan op verzoek van een gebruiker (OnlyOffice: forcesavetype 1)
    filetype: str | None
class TokenError(Exception): ...
class ConvertError(Exception): code: int; transient: bool
class Engine(Protocol):
    name: str
    strip_prefix: bool                      # OnlyOffice: ja; Collabora: nee
    edit_formats: frozenset[str]
    convert_formats: frozenset[str]
    def classify(self, method: str, path: str, upgrade: bool) -> PathClass | None
    def session_key(self, path: str) -> str | None
    def editor_config(self, ctx: OpenContext) -> dict          # getekend
    def read_callback(self, body: bytes, authorization: str | None) -> CallbackEvent   # TokenError
    def callback_reply(self, outcome: str) -> tuple[int, dict]   # saved|same|copy → (200, {"error":0}) …
    def check_download(self, authorization: str | None, url: str) -> None              # TokenError
    async def health(self) -> Health
    async def present(self, key: str) -> list[str] | None     # None: de editor kent de key niet
    async def checkpoint(self, key: str, userdata: str) -> Literal["sent", "no_changes", "unknown"]
    async def drop(self, key: str, ds_uids: Sequence[str]) -> None
    async def convert(self, src_url: str, ext: str, key: str, title: str, out: Path, limit: int) -> None
    async def forgotten(self) -> list[str]
    async def fetch_forgotten(self, key: str, out: Path, limit: int) -> tuple[str, int]   # (sha, grootte)
    async def delete_forgotten(self, key: str) -> None
def current() -> Engine | None                             # None als office niet ingesteld is

# app/office/oo/__init__.py: class OnlyOffice(Engine), gebundeld uit oo/paths.py (O1),
#   oo/editor.py (O2), oo/convert.py (O3) en oo/admin.py (O5); O0 zet die als stubs.

# app/office/oo/tokens.py (alles HS256 met settings.office_secret; speling 60 s)
def sign(settings, payload: dict, minutes: int = 5) -> str
def browser_token(settings, config: dict) -> str              # hele config zonder "events"
def command_body(settings, params: dict) -> dict              # {**params, "token": sign(params)}
def callback_data(settings, body: dict, authorization: str | None) -> dict
    # body["token"] eerst (platte velden), anders de header (velden onder "payload");
    # eist operation == "callback"; TokenError bij elke fout
def check_download(settings, authorization: str | None, url: str) -> None
    # eist operation == "download" en payload.url == url (precies)
def ds_user_id(settings, user_id: uuid.UUID) -> str

# app/office/oo/client.py (de enige weg naar DS)
class DocServer:
    async def healthy(self) -> bool                           # GET /healthcheck == "true"
    async def command(self, c: str, **params) -> dict         # POST /command?shardkey=<key>
    async def convert(self, **params) -> dict                 # POST /converter, Accept: application/json
    async def fetch(self, url: str, dest: Path, limit: int) -> tuple[str, int]   # al herschreven URL

# app/office/urls.py
def internal_url(settings, path: str) -> str                  # settings.office_app_url + path
def rewrite(settings, url: str, *, key: str, kind: Literal["data", "conv", "forgotten"]) -> str
    # alleen pad en query; het pad moet passen (Keuze 13); anders ValueError

# app/office/state.py (op app.state.office; één proces)
class OfficeState:
    health: Literal["off", "starting", "ok", "down"]; down_since: datetime | None
    version: str | None; license_connections: int | None
    tunnels: dict[str, dict[uuid.UUID, int]]   # key → gebruiker → open tunnels (O1)
    peak: tuple[int, datetime] | None
    def node_lock(self, node_id: uuid.UUID) -> asyncio.Lock
def office_state(app) -> OfficeState

# app/office/runtime.py: bind(app) in de lifespan; app() voor de tunnel (die is geen ASGI)

# app/office/grants.py (conversiebronnen, in het geheugen, 10 minuten)
def grant(sha: str) -> str ; def resolve(grant_id: str) -> str | None

# app/office/gate.py (signatuur; O1 vult check in)
GATE_COOKIE = "office"
def issue(response: Response, settings, user: User, family: uuid.UUID | None, https: bool) -> None
    # app-geheim, typ "office_gate", 2 uur, HttpOnly, SameSite=Strict, Path=/office/, Secure bij https
async def check(headers: Headers, key: str | None = None) -> GateUser | None

# app/office/sessions.py (O0-stub; O2)
async def find_by_key(session, key: str) -> OfficeSession | None

# app/office/saving.py (O0-stub; O2)
async def store(row: OfficeSession, tmp: Path, sha: str, size: int, event: CallbackEvent) -> str
    # "saved" | "same" | "copy" | "retry" | "refused"

# app/office/recovery.py (O0-stub; O2)
async def run_once() -> int ; async def restore(key: str) -> Node ; async def forget(key: str) -> None

# app/office/convert.py (O0-stub; O3)
async def on_preview_done(session, blob: Blob) -> None ; async def on_discard(blobs, sha: str) -> None

# app/routers/files.py: place_file krijgt twee optionele parameters (Keuze 16)
async def place_file(..., only=None, exact: bool = False, squash: str | None = None, ...)
```

### 3.4 API-contracten, gebeurtenissen en codes (frozen)

- De eindpunten van O2 tot en met O5 zoals in §2. Pydantic-modellen in
  `routers/office_contracts.py`. O0 zet stubs die 503 `office_off` geven.
- **Gebeurtenissen** (SSE, na de commit):
  - `office.unsaved {node_id|null, name, reason: "no_room"|"owner_gone"|"error_status", final: bool}`
    naar de eigenaar en de deelnemers;
  - `office.recovered {node_id|null, copy_id, name, reason: "status3"|"no_writer"|"gone"|"forgotten"}`
    naar de eigenaar en de deelnemers;
  - `office.dropped {node_id, key}` alleen naar wie eruit gezet is.
  - `affectsFolder` negeert ze. `node.changed` komt al uit `place_file`.
- **Nieuwe foutcodes**, allemaal in `web/src/i18n/errors/office.ts` (nl en en): `office_off`,
  `office_unavailable`, `office_format`, `office_too_large`, `office_busy`, `office_too_many` {max},
  `office_gate`, `office_token`, `office_convert_failed` {code}, `pdf_pending`, `office_owner_gone`,
  `office_misconfigured`. Bestaande codes blijven: `not_found`, `read_only`, `trashed`,
  `not_a_file`, `album`, `no_room`, `folder_not_found`, `guest`.
- **Meldingscodes** voor `NOTICES`: `office_down`, `office_misconfigured`, `office_version`,
  `office_limit`, `office_unsaved`.

### 3.5 Hotspots die O0 zelf aanpast

- **`serve.py`**:
  - `ws=Tunnel` op de publieke luisteraars. De kern van O0 geeft door zonder poort-cookie, maar
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
  - `SECRET_PARAMS` wordt `([?&](?:token|sig|key|md5|shardkey)=)`;
  - de routers van office; `internal_app` met de interne routers en `internal_app.state = app.state`;
  - in `lifespan`: `OfficeState`, `runtime.bind(app)`, de gezondheidslus (elke 30 s) en
    `recovery.run_once()` (stub), alleen als office ingesteld is;
  - de catch-all geeft 404 voor `office/…` en `internal/…`, ook als de proxy uit staat.
- **De kern van proxy en tunnel** (`app/office/proxy.py` en `tunnel.py`, daarna van O1): http
  doorgeven met httpx (streamen in beide richtingen), bytes pompen in de tunnel, het prefix eraf en de
  `X-Forwarded-*`-koppen. Nog zonder padbeleid en poort-cookie, en daarom alleen met
  `office_dev_ungated`. Zo kunnen de spike en O2 werken, en is de basis toch nooit open.
- **`routers/files.py`**: alleen `exact` en `squash` in `place_file`, met tests.
- **`previews.py`**: twee haakregels. In `_store` vóór de commit `await convert.on_preview_done(session,
  blob)`; in `discard` `await convert.on_discard(blobs, sha)`. O3 vult ze.
- **`routers/admin.py`**: in `system_info` de meldingen van `office.monitor.notices(...)` (stub: `[]`).
- **`pyproject.toml`** en **`package.json`**: ongewijzigd.
- **Web**:
  - `routes.tsx`: `route("/bewerken/:id", () => import("./office/EditorPage"), ["office"],
    "office.title", null)`;
  - `pwa/routing.ts`: `NEVER` krijgt `office(\/|$)`;
  - `vite.config.ts`: `"/office": { target: "http://localhost:8000", ws: true }`;
  - de haken: `browser/actions.ts` (`edit` met sneltoets `e`, `pdf` in groep `convert`, de
    beschikbaarheid uit `office/actions.ts` en `office/pdf.ts`), `Files.tsx` (menuknop "Nieuw" met
    Map plus `newItems()`, sneltoets `o`), `viewer/Viewer.tsx` (Bewerken en PDF in de werkbalk, `e`),
    `Beheer.tsx` (tabblad "Office", Alt+7), `App.tsx` (`useOfficeNotices()`);
  - `office/types.ts`, `office/api.ts` en `office/index.ts`: de typen en aanroepen van §2;
  - `i18n/types.ts`: gebied `office`; `i18n/errors/office.ts`.
- **CSP:** O0 verandert de CSP niet (M4).

### 3.6 De nep-DS en het harnas

**`api/tests/fake_docserver/`** (`app.py`, `protocol.py`, `docx.py`, `pdf.py`, `static/api.js`,
`static/editor.html`, `static/editor.js`, `recorded/*.json`, `__main__.py`). Een kleine ASGI-app
(FastAPI, al aanwezig) die het protocol van DS 10.0 spreekt zoals gemeten. In pytest via httpx
`ASGITransport`. In de e2e als proces: `python -m tests.fake_docserver --port P --secret-file F
--link-secret-file G --time-scale S`, met uvicorn en `ws="websockets-sansio"` (testcode; alles staat
al in de lock).

| Onderdeel | Gedrag zoals de echte DS |
|---|---|
| `GET /healthcheck` | `true` (of `false` in de stand "start op") |
| `/web-apps/apps/api/documents/api.js` | een kleine `DocsAPI.DocEditor(id, config)`. Controleert `config.token` en vergelijkt de getekende velden met de config. Maakt een iframe naar `<prefix>/<V>/web-apps/apps/documenteditor/main/index.html` met het prefix uit `X-Forwarded-Prefix`; zonder die kop klopt het adres niet en komt `onError` -4, net als bij de echte. Events `onAppReady`, `onDocumentReady`, `onDocumentStateChange`, `onError`, `onWarning` (-18 als de socket wegvalt), `onRequestClose`; `destroyEditor()`. `<V>` heeft de vorm `10.0.0-<32 hex>`. |
| editorpagina | een `<textarea aria-label="Nep-editor">`, een knop Opslaan (forcesave type 1), een WebSocket naar `<prefix>/<V>/doc/<key>/c/?shardkey=<key>&EIO=4&transport=websocket`. Tekst gaat live naar de andere sockets van dezelfde key. Geen inline script, geen console-fouten (de guard van de e2e kijkt ook in iframes). |
| eerste opening van een key | haalt `document.url` op met `Authorization: Bearer` (`{payload: {url}, operation: "download"}`) en `User-Agent: DocumentServer`. Controleert `Repr-Digest` als die er is. Stuurt status 1 met `actions` type 1. |
| callbacks | token in de body (platte velden, `operation`, `iat`, `exp` = +300 s) en in de header (`payload`-omhulsel, zonder `history`). Status 1 bij elke socket die opent of sluit; status 6 bij forcesave (knop: type 1, commando: type 0, met `userdata`) met `url`, `changesurl`, `history`, `filetype`, `users`; status 2 na de laatste sluiter plus `--save-delay` (standaard 5 s, in tests 0,2 s); status 4 als er nooit iets veranderde. |
| fouten van de app | 429 of 5xx: opnieuw na 1, 2 en 4 s (maal `--time-scale`, of `retries` uit de instellingen); `{"error": 1}`: niet opnieuw; beide daarna naar de forgotten files |
| `/cache/files/…` | links op de host van de aanvrager, met een echte `md5` over `expires + pad + linkgeheim`; fout 403, verlopen 403. De app moet de host dus herschrijven, net als bij de echte. |
| `POST /command` | token in body of header, anders `error 6`; een config-token ook `error 6`. `info` (gebruikers, en een status-1-callback), `forcesave` (`error 4` zonder wijzigingen), `drop` (met of zonder `users`), `version` (`10.0.0.169`), `license` (instelbaar, standaard 2147483647), `getForgottenList`, `getForgotten`, `deleteForgotten`; onbekende key → `error 1` |
| `POST /converter` | zonder `Accept: application/json` geen JSON; zonder token -8; bron ophalen met het download-JWT (anders -4); docx → doc -3, pptx → ppt -7; uitvoer een kleine geldige PDF met N pagina's (`pdf.py`, stdlib); `async`: eerst `{endConvert: false}`, daarna klaar; **dezelfde key geeft hetzelfde resultaat, ook voor een ander bestand** |
| opgeslagen bestand | docx: een nieuwe docx met één alinea per gebruiker (stdlib `zipfile`), zodat de test de inhoud kan lezen; xlsx en pptx: het origineel plus `customXml/fake.xml` met de tekst |
| `/__fake/*` (alleen in de nep) | `log` (elk verzoek, zonder geheimen), `kill` (alles weg zonder callbacks, zoals `docker kill`), `stop` (status 2 voor alles binnen 1 s, zoals SIGTERM), `restart` (nieuw linkgeheim tenzij vast: oude links 403), `health`, `license` |

`tests/test_fake_docserver.py` houdt de nep eerlijk. De veldnamen en de tokenvorm van zijn callbacks,
downloads en commando's moeten gelijk zijn aan die in `recorded/`: opgenomen uit de proeven
(`coedit-result.json`, `stub.log`), met tokens opnieuw getekend en zonder geheimen. De nep-DS is
**geen** vervanging van de echte; die draait in §4.5.

**`tests/office_contract.py`**: het testprotocol `Driver` en de scenario's van §4.4 (O0 schrijft ze,
O2 laat ze slagen).

**`e2e/office_harness.py`** en **`e2e/conftest.py`** (O0-e2e):
- fixture `office` (sessie): start de nep-DS als proces (vrije poort, geheimen in een tijdelijke map,
  stoppen op PID) en een eigen `Server` met `PLATFORM_OFFICE_URL`, `PLATFORM_OFFICE_SECRET`,
  `PLATFORM_OFFICE_APP_URL=http://127.0.0.1:<interne poort>` en `PLATFORM_OFFICE_INTERNAL_PORT`;
- fixture `real_office`: dezelfde server, maar met `E2E_REAL_DS` (de URL van een echte DS) en
  `PLATFORM_OFFICE_INTERNAL_HOST=0.0.0.0`; zonder die variabele wordt de test overgeslagen;
- de guard krijgt de optie `office_frames="record"`: meldingen uit frames onder `/office/` worden
  bewaard en als bijlage geschreven, maar laten de test niet falen. Alleen voor tests met marker
  `office_real`. Met de nep-DS faalt alles zoals nu;
- hulpjes: `frame_editor(page)`, `type_in_editor(page, text)` (echte DS: klik in `#id_main_view` in
  de iframe `frameEditor` en `keyboard.type` [gemeten]), `wait_callback(server, key, status)`,
  `docx_text(bytes)`;
- `e2e/pytest.ini`: markers `office` en `office_real` (`--strict-markers` blijft).

### 3.7 De spike met de echte DS (O0-api, dag 1)

Met de echte DS 10.0.0.1 in de sandbox, **via de kern van proxy en tunnel in de app** (met
`office_dev_ungated`). M1, M3, M4, M5 en M6 zijn al gemeten; die horen er niet meer bij. Uitslag in
`docs/fase-4/spike.md`. Daarna ligt het vast.

1. **Padinventaris:** koud en warm openen, typen, Opslaan, downloaden als PDF en docx, afdrukken,
   afbeelding uit een bestand invoegen, een opmerking, sluiten. Elke methode en padklasse → §3.10
   wordt de vaste lijst.
2. **Cookie bij elk verzoek:** stuurt elk dynamisch verzoek (ook uit workers en de service worker
   van DS) het poort-cookie mee?
3. **Knop Opslaan:** geeft hij status 6 met `forcesavetype 1`?
4. **Opnieuw proberen:** werkt `callbackBackoffOptions.retries: 8` via `local-production-linux.json`?
   Geldt het ook bij "verbinding geweigerd" (de app gestopt vlak voor status 2), niet alleen bij 5xx?
5. **Herstart van de app terwijl iemand bewerkt:** verbindt de editor vanzelf opnieuw door de tunnel?
   Gaat er iets verloren? Komt status 2 later toch binnen?
6. **Configsleutels in 10.0:** `customization.close` en `onRequestClose`, `uiTheme`, `macros`,
   `macrosMode`, `plugins`, `help`; werkt `aiSettings.enable: false`? Gebruikt DS Alt+Shift+Q zelf?
7. **`Repr-Digest`** op `/internal/office/doc/{key}`: neemt DS hem aan?
8. **Eigen lege bestanden:** de xlsx en pptx van Keuze 24 openen in edit-modus en zetten om naar PDF.
9. **De CSP van Keuze 9** op de HTML van DS: werkt de editor nog, met alle acties van stap 1?
10. **Status 3** proberen op te wekken (bijvoorbeeld een kapotte `changesurl`). Wat staat er in `url`?
11. **Tijden via de app:** koud en warm openen via proxy en tunnel, tegen 4,1 s via nginx in de proef.

Elke uitkomst heeft al een terugval in dit ontwerp. Geen enkele uitkomst vraagt een andere
architectuur, behalve als stap 1 een pad laat zien dat de editor nodig heeft en dat we niet willen
doorgeven. Dan beslist de integratie, met een notitie.

### 3.8 Klaar als (O0)

- Alle bestaande tests groen; ruff, tsc, build en budget schoon.
- `test_migration_0007.py`: een database van 0006 met data → upgrade → tabel, kolommen en indexen er
  → twee open sessies voor één bestand geven een `IntegrityError` → downgrade.
- `test_office_tokens.py`: elke vorm uit §3.9 heen en terug, en elke weigering. Een office-token werkt
  niet als app-token en omgekeerd.
- `test_place_file_exact.py`: `exact` na hernoemen, verplaatsen en prullenbak; 404 als het weg is;
  `squash` alleen bij de juiste sha; de oude blob is daarna vrij voor `collect_garbage`.
- `test_serve_upgrade.py`: een geüpgradede verbinding blijft open na `HEADER_TIMEOUT` (in de test
  0,5 s), met h11 **en** httptools (M1); een stille gewone verbinding sluit nog steeds; zonder
  `office_dev_ungated` geeft elke upgrade 404; de interne poort kent alleen `/internal/office/…`; de
  publieke poort geeft 404 op `/internal/…`.
- `test_office_imports.py`: buiten `app/office/oo/` importeert alleen `engine.py` de adapter.
- `test_fake_docserver.py` is groen; de nep-DS draait in pytest en als proces.
- De spike is gedaan; `docs/fase-4/spike.md` legt de lijst vast.
- Zonder office-instellingen is de app precies fase 3 (de bestaande e2e bewijst het).
- De keuzes staan als B31 en verder in `docs/fase-0/beslissingen.md` (voorstel), en
  `docs/fase-4/README.md` heeft een skelet.

### 3.9 Elk verzoekpad, met token en controle

| # | Van → naar | Pad | Token | Wie controleert wat |
|---|---|---|---|---|
| 1 | browser → app | `/api/v1/office/…` (`status`, `files/{id}`, `session`, `save`, `gate`, `new`, `pdf`) | access-token van de app (Bearer) | `CurrentUser` (2FA), toegang via `access.py`, rate limit 30/min per gebruiker op `session` |
| 2 | app → browser | antwoord van `session` | `config.token`: HS256, office-geheim, hele config; plus cookie `office` | DS controleert de config bij openen [doc] |
| 3 | browser → app → DS | `/office/<statisch>` | geen | padbeleid (alleen GET en HEAD, vaste klassen) |
| 4 | browser → app → DS | `/office/cache/files/data/…` | cookie `office` (app-geheim) + `md5`/`expires` van DS | app: cookie; DS: secure link [gemeten] |
| 5 | browser → app → DS | upgrade `/office/<V>/doc/<key>/c/` (tunnel) | cookie `office` + config-token in het protocol van DS | tunnel: `Origin`, cookie, lid van sessie `<key>`, elke 60 s opnieuw; DS: config-token |
| 6 | browser → app → DS | `/office/<V>/<editoractie>` | cookie `office` | app: cookie, lid bij een key in het pad; DS: zijn eigen sessie |
| 7 | DS → app (poort 8081) | `GET /internal/office/doc/{key}` | `Authorization: Bearer`, `{"payload": {"url"}, "operation": "download"}` | app: handtekening, `exp`, `operation`, `payload.url` == de URL; sessie niet dicht |
| 8 | DS → app (poort 8081) | `POST /internal/office/callback` | body-`token` (plat) en/of header (onder `payload`), `operation: callback`, 5 min | app: handtekening, `exp`, `operation`; alleen velden uit het token; key bekend |
| 9 | app → DS | `GET` de `url` uit de callback | `md5`/`expires` van DS | app: alleen pad en query, onze key, host = `office_url` |
| 10 | app → DS | `POST /command?shardkey=<key>` | body-`token`, payload = parameters | DS [gemeten: `error 6` zonder] |
| 11 | app → DS | `POST /converter` | body-`token`, payload = parameters | DS [gemeten: -8 zonder] |
| 12 | DS → app (poort 8081) | `GET /internal/office/src/{grant}` | als #7 | app: als #7, en de grant bestaat nog (10 min) |
| 13 | app → DS | `GET` de `fileUrl` of de `getForgotten`-URL | `md5`/`expires` | app: als #9 (`conv_<key>` of `forgotten/<key>`) |
| 14 | app → DS | `GET /healthcheck` | geen | intern netwerk |

Vanaf internet zijn alleen #1 en #3–#6 bereikbaar. #7, #8 en #12 staan op een poort die niet
gepubliceerd is. #9–#11, #13 en #14 gaan van de app naar DS over het interne netwerk.

### 3.10 Padbeleid van `/office/` (begin; de spike maakt het af)

Na normaliseren (prefix `/office` eraf, versiesegment `^\d+\.\d+\.\d+-[0-9a-f]{8,64}$` eraf):

| Klasse | Paden | Methoden | Cookie | Bron |
|---|---|---|---|---|
| statisch | `web-apps/…` (ook de 302 naar het versiepad), `sdkjs/…`, `sdkjs-plugins/…`, `fonts/…`, `dictionaries/…`, `document_editor_service_worker.js`, `web-apps/apps/api/documents/api.js` | GET, HEAD | nee | [gemeten, M11] |
| documentdata | `cache/files/data/…` | GET | ja | [gemeten, M11] |
| sessie | `doc/<key>/c/…` | GET, POST (polling), upgrade (tunnel) | ja + lid | [gemeten: GET en upgrade] |
| editorinstellingen | `plugins.json`, `themes.json` | GET | ja | [gemeten, M11] |
| editoracties | wat de spike vindt bij downloaden, afdrukken, afbeelding invoegen en opmerkingen (verwacht onder meer `downloadas/<key>`, `upload/<key>`, `printfile/<key>/…`) | volgens de spike | ja + lid | [niet getest] |
| geweigerd (404) | al het andere, met name `/` (welkomstpagina met `Clear-Site-Data`), `welcome`, `command`, `coauthoring/…`, `converter`, `ConvertService.ashx`, `docbuilder`, `info`, `internal`, `hosting/…`, `example`, `healthcheck`, `version`, `ai-proxy`, `cache/files/forgotten/…`, `favicon.ico` | — | — | [image: `ds-docservice.conf`] |

---

## 4. Tests en meten

### 4.1 Vier lagen

| Laag | Waartegen | Waar | Poort |
|---|---|---|---|
| Unit | pure functies: tokens, keys, herschrijven, padbeleid, regels, lege bestanden | pytest, vitest | hard, in elke CI-run |
| API | de app met de nep-DS in hetzelfde proces | pytest | hard |
| e2e | de echte server, Chromium, de nep-DS als proces | `pytest ../e2e -m "office"` | hard |
| Echte editor | de echte server, Chromium, DS 10.0.0.1 in Docker | job `office`, `-m office_real` | hard |

Plus metingen in de sandbox (§4.7), alleen als rapport.

### 4.2 Unit en API

**Unit** (zonder netwerk):
- tokens in elke richting en elke weigering (§3.9), ook `alg: none`, HS512, een token van de app zelf,
  en een uitgaand token van DS dat als inkomend terugkomt;
- keys: vorm, uniek over 10.000 sessies, hergebruik, een nieuwe key na sluiten;
- URL herschrijven: alleen ons pad en onze key, geen host, geen userinfo of poort van buiten, geen
  `..`, de query blijft;
- padbeleid (alle gemeten paden, de weigerlijst, padtrucs) en de koppen van de proxy;
- het herschrijven van de upgrade-request in de tunnel (bytes in, bytes uit);
- de regels van `policy.py` als tabellen;
- `place_file` met `exact` en `squash`;
- lege bestanden: vaste bytes, geldige zip, leesbaar voor B21;
- web: de statusovergangen van de editorpagina, wanneer Bewerken er is, de items van "Nieuw", de
  opmaak van de beheerwaarden.

**API** (de nep-DS als ASGI-app): de lijsten per pakket in §2. Samen dekken ze:
- de rechtentabel (Keuze 22);
- elke status van de callback (Keuze 15) en elke vorm van het token;
- herhaalde en late callbacks (idempotent);
- elke rij van Keuze 19, de ronde (Keuze 20) en het herstel (Keuze 21);
- SSRF: een `httpx.MockTransport` die faalt op elke host behalve `PLATFORM_OFFICE_URL`;
- de interne routes met en zonder het JWT van DS;
- conversie met de valkuil van de cache-key, en de terugval op B21;
- proxy en tunnel met echte sockets;
- bewaking met een eindige licentie;
- DS weg tijdens openen, tijdens opslaan en tijdens een conversie;
- `test_error_catalog.py` voor alle nieuwe codes.

### 4.3 De volgorde van bewijzen

Elke bewering in "Klaar als" heeft eerst een unit- of API-test, dan een e2e met de nep-DS, en
waar het om de editor zelf gaat, een test met de echte DS. Een test met de echte DS vervangt nooit
een test met de nep: de nep draait bij elke PR, ook als Docker Hub een dag niet wil.

### 4.4 Contracttests en e2e met de nep-DS

**Contracttests** (`tests/office_contract.py`, O0; slagen na O2). Scenario's tegen een klein
testprotocol, los van OnlyOffice:

```python
class Driver(Protocol):            # nu één: de nep-DS van OnlyOffice
    async def open(self, user, node) -> Handle
    async def type(self, h: Handle, text: str) -> None
    async def save(self, h: Handle, explicit: bool) -> None
    async def close(self, h: Handle) -> None
    async def crash(self) -> None    # kill zonder opslaan
    async def restart_app(self) -> None
```

Scenario's: twee gebruikers → één key → beide teksten in de opgeslagen versie; tussentijds opslaan
binnen 30 minuten → geen extra versie; na 30 minuten → wel; hernoemen, verplaatsen, prullenbak,
purge, upload ertussen, deling intrekken, quotum vol, crash, app weg tijdens status 2.
`pytest -k contract` draait ze voor elke `Driver`. Een tweede editor krijgt later dezelfde
scenario's: zo is "de terugval is echt" een test, geen belofte.

**e2e met de nep-DS** (marker `office`, in elke CI-run). Via de echte server, de echte proxy en
tunnel, het echte poort-cookie en een echte Chromium:

1. **Samen bewerken** (O2): Anna deelt een map met schrijfrecht met Bram. Ze openen allebei
   `/bewerken/<id>` in een eigen context. Beide editors klaar. Anna typt "Anna was hier"; Bram ziet
   het meteen (dat bewijst tunnel en meedoen op dezelfde key). `GET /api/v1/admin/office` toont 2
   verbindingen op die key. Bram typt. Bram drukt "Nu opslaan" → de versielijst heeft er één bij.
   Allebei sluiten → status 2 → nog steeds precies één nieuwe versie (binnen 30 minuten), en de docx
   bevat beide teksten.
2. **Randgevallen** (O2): hernoemen tijdens het bewerken → opgeslagen in het hernoemde bestand.
   Prullenbak terwijl de eigenaar bewerkt → het bestand in de prullenbak heeft de nieuwe inhoud.
   Deling ingetrokken terwijl alleen Bram bewerkt → binnen enkele seconden uit de editor (de ronde
   draait in de test elke seconde), met de melding; Anna krijgt `office.recovered`, en de
   herstelkopie staat in haar hoofdmap.
3. **Rechten** (O2): een `read`-gebruiker ziet geen Bewerken en krijgt 403. Uitgelogd → 401 op
   `cache/files` en een geweigerde upgrade. Uitloggen in een andere context sluit de open editor van
   die login.
4. **Voorbeeld zonder editor** (O3): een docx en een doc uploaden → de thumbnail verandert en de
   viewer toont pagina's. De log van de nep-DS heeft geen enkele editorverbinding.
5. **Nieuw document** (O4) met alleen het toetsenbord → de editor opent.
6. **DS weg en app weg** (O2): de nep-DS stoppen (op PID) → Bewerken geeft "niet bereikbaar", het
   voorbeeld blijft; weer starten → het werkt weer zonder de app te herstarten. Dan de app stoppen
   (op PID) terwijl Anna bewerkt: de nep-DS kan status 2 niet kwijt en zet het bij de forgotten files.
   De app weer starten → de herstelronde zet de inhoud in het bestand.
7. **Beheer** (O5): tabblad Office met Alt+7; grens op 1 → de tweede bewerker krijgt de melding;
   een niet-opgeslagen document (quotum vol) terugzetten na het vrijmaken van ruimte.
8. **Publieke link** (O3): een docx toont pagina's, geen Bewerken, en nooit een upgrade naar
   `/office/`.
9. **Toetsenbord en taal** (elk pakket zijn deel): E, O, Alt+Shift+Q, Nu opslaan, Sluiten; alles nog
   een keer in het Engels.

### 4.5 De echte editor in CI (job `office`, marker `office_real`)

Haalbaar volgens de proef: ±1,5–2 minuten extra per run.

```
1. docker pull mirror.gcr.io/onlyoffice/documentserver@sha256:ca17… (3 pogingen; anders docker.io)
2. docker run -d --name ds -p 127.0.0.1:18080:80 --add-host=host.docker.internal:host-gateway \
     -e JWT_SECRET=<per run> -e SECURE_LINK_SECRET=<per run> -e WELCOME_PAGE_ENABLED=false \
     -e PLUGINS_ENABLED=false \
     -v $PWD/infra/single/office/local-production-linux.json:/etc/onlyoffice/documentserver/local-production-linux.json:ro <image>
   (als eerste stap, zodat de fonts van DS bouwen terwijl de web-app bouwt)
3. web bouwen, Chromium installeren, wachten op /healthcheck == true
4. E2E_REAL_DS=http://127.0.0.1:18080 PLATFORM_OFFICE_APP_URL=http://host.docker.internal:<poort> \
     uv run --with playwright pytest ../e2e -m office_real
5. bij een fout: docker logs ds en de schermafdrukken als artefact
```

Tests:
1. **Twee gebruikers** (O2): zie §4.6.
2. **Voorbeeld zonder editor** (O3): docx, xlsx, pptx, doc en rtf uploaden → binnen 60 s
   `pdf_state: "ready"` en pagina's in de viewer, zonder upgrade naar `/office/`.
3. **Nieuw docx, xlsx en pptx** (O4): openen zonder `onError`; sluiten zonder wijziging → status 4,
   geen versie; omzetten naar PDF lukt.
4. **Herstart van de app** (O2) terwijl Anna bewerkt: de server op PID stoppen en starten → opnieuw
   laden → wat ze typte, staat er. Via de pogingen van DS of via het herstel (Keuze 21).

Verschil met productie: in CI hangt DS aan de standaardbridge (met uitgang), niet aan het interne
netwerk. Proxy, tunnel, cookie en tokens zijn gelijk. Het interne netwerk bewijst §4.6 punt 3.

Deze job is een **harde poort**, net als de e2e (B29). Valt hij door een pull van het image, dan
opnieuw draaien; het image is op digest vastgepind.

### 4.6 Hoe "twee gebruikers bewerken tegelijk één docx" bewezen wordt

Drie bewijzen die elkaar dekken.

1. **Met de echte editor, in CI, als harde poort** (`test_office_o2_real.py::test_twee_gebruikers_een_docx`):
   1. Anna (eigenaar) en Bram (`write`-deling) in twee browsercontexts, elk ingelogd met 2FA.
   2. Beide openen `/bewerken/<id>` van dezelfde docx via de knop Bewerken. Bewijs van "tegelijk":
      allebei `onDocumentReady`, dezelfde key in beide configs, `/api/v1/admin/office` meldt op dat
      moment 2 verbindingen op die key (de tellers van de tunnel), en `info` noemt beide gebruikers
      [gemeten in de proef].
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
3. **Bij de integratie, via de echte installatie** in de sandbox: `docker compose -f compose.yaml -f
   compose.office.yaml up` met het gebouwde image, en dezelfde test 1 tegen
   `http://127.0.0.1:<poort>`. Dat bewijst ook wat CI niet kan:
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
| Conversie klein / 401 pagina's | ±1 s / ≤ 60 s (22 s gemeten) | |
| 20 bewerkers (5 documenten × 4), 60 s typen | alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd | basis voor `max_editors` (Keuze 27) |
| Stoppen met open editors | alles opgeslagen; `docker stop` ≤ 60 s | Keuze 29 |
| Kill van DS met open editors | verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min) | Keuze 18 |
| Budget | de editorchunk < 50 KB gzip; Bestanden groeit < 1 KB | Keuze 33 |

Op Jonas' LXC (handmatig, met de checklist in het runbook): `docker info` (driver, limieten), het
interne netwerk, `ss -ltn` op de LXC (M7), de start van DS, één sessie met twee gebruikers.

---

## 5. Volgorde

1. **O0 Fundament, alleen** (±3 dagen). O0-api begint met de spike (§3.7, dag 1), dan contracten en
   hotspots. O0-web tegelijk. O0-e2e als laatste (harnas en nep-DS als proces). Poort: §3.8. Tag
   `fase4-basis`.
2. **Parallelle golf** (±6 dagen): O1 tot en met O6, elk in een eigen worktree vanaf `fase4-basis`.
   Elk levert één PR met tests, `docs/fase-4/<pkg>.md` en een toetsenbord-checklist (waar het web
   raakt).
3. **Volgorde van samenvoegen: O1 → O2 → O3 → O4 → O5 → O6.**
   - O1 eerst: zonder padbeleid en poort-cookie gaat de vlag `office_dev_ungated` er niet uit.
   - O2 daarna: O3, O4 en O5 gebruiken de sessies en het herstel.
   - O6 als laatste: de job met de echte editor heeft O1–O4 nodig. De rest van O6 (compose,
     install.sh) mag eerder, met office nog uit in de standaard.
   - Vóór elke merge rebasen. Daarna de volledige set: pytest (met de contracttests en
     `test_error_catalog`), ruff, `tsc -b`, vitest, build, budget, `check:i18n`, de e2e van fase 3 en
     `-m office`.
4. **Integratie** (±2 dagen):
   - de e2e met de echte DS (§4.5) en het bewijs van §4.6 → `docs/fase-4/klaar.md`;
   - de echte installatie in de sandbox (§4.6 punt 3) → `docs/fase-4/integratie.md`, ook het bijwerken
     van een `.env` van v0.5.0;
   - de metingen van §4.7, en `max_editors` daarop gezet;
   - `docs/fase-4/README.md` met wat er is en de bekende grenzen; de vinkjes van fase 4 in het
     stappenplan, en de regels over "eigen Postgres, Redis en RabbitMQ" en de limiet van 20
     bijgewerkt met de reden; B3 en B31 en verder definitief na de keuze van Jonas;
   - versie 0.6.0 in `pyproject.toml`, `app/__init__.py` en `package.json`.

Grove planning binnen de twee weken: O0 ±3 dagen (met de spike), de golf ±6 dagen, de integratie
±2 dagen.

### 5.1 Risico's

| Risico | Kans | Impact | Wat we doen |
|---|---|---|---|
| Een fout in DS draait op onze origin en handelt namens een bewerker (bijvoorbeeld via het refresh-cookie) | laag tot middel | hoog | Keuze 9: alleen bewerkers draaien DS, DS onveranderd en vastgepind, plugins en macro's uit, CSP tegen uitlekken; Vraag 3; een eigen hostnaam in fase 8 |
| DS bereikt diensten van de LXC via het gateway-adres (M7) | middel | middel | op de LXC luistert alleen 80, 443 en SSH (checklist in het runbook); SSH met sleutel; een firewallregel hoort bij fase 8 |
| De tunnel leunt op hoe uvicorn een upgrade doorgeeft (`ws_protocol_class`, `handle_websocket_upgrade`) | middel bij een update van uvicorn | middel | `test_serve_upgrade.py` en `test_office_tunnel.py` op h11 en httptools; uvicorn staat vast in de lock; een update van uvicorn is een eigen PR |
| De editor gebruikt een pad dat niet in de lijst staat (downloaden, afdrukken, afbeelding) | middel | middel | spike stap 1; een logregel bij elk geweigerd pad; de echte-editor-test in CI |
| Een worker van DS stuurt geen cookie mee | laag | middel | statische paden hebben geen cookie nodig; spike stap 2 |
| De CSP van Keuze 9 breekt een editoractie | middel | laag | spike stap 9; terugval naar alleen `frame-ancestors 'self'` (zoals in de proef) |
| Een harde stop of OOM van DS gooit werk weg (alles in het geheugen) | laag | middel | tussentijds opslaan elke 5 min, geheugengrens 2 GB, nette stop met 60 s, forcesave vóór updates |
| Herstart van de app tijdens bewerken (update): de callback valt in het gat | middel | middel | forcesave vóór de update en bij het stoppen; 8 pogingen (±4 min); automatisch herstel (Keuze 21); spike stap 4 en 5 |
| Herstart van de LXC: dockerd stopt alle containers tegelijk, niet in de volgorde van compose | middel | laag | DS slaat bij een nette stop op; is de app al weg, dan herstelt Keuze 21 het bij de start |
| Docker Hub geeft 429 bij installeren, bijwerken of in CI | middel | laag | pull op digest met `mirror.gcr.io` als terugval; de vorige DS blijft; het platform werkt altijd door |
| LXC: `vfs`-opslag, geen cgroup-limieten, het interne netwerk werkt anders in geneste Docker | laag | middel | `install.sh` controleert driver, ruimte en limieten; nooit een verzwakking op de host; checklist in het runbook |
| Het bekende Proxmox-probleem met containerd en `ip_unprivileged_port_start` (nov. 2025) | laag | hoog | geldt voor elke container, niet alleen DS; `install.sh` test al met hello-world; "AppArmor unconfined" stellen we nooit voor |
| Iemand met het office-geheim maakt callbacks na | laag | hoog | het geheim staat alleen in `.env` (600) en in de twee containers; callbacks alleen op de interne poort; elke opslag controleert het recht van een echte gebruiker en de vorige inhoud blijft als versie |
| De proxy in Python is te traag bij een koude start (253 verzoeken) | laag | laag | versiepaden zijn `immutable`; warm 5 verzoeken; meten in §4.7 |
| De nep-DS wijkt af van de echte | middel | middel | opgenomen sporen in `recorded/`, `test_fake_docserver.py`, en de echte job als scheidsrechter |
| Een nieuwe DS-versie verandert paden of protocol | middel | middel | vast op digest; bijwerken is een eigen PR waarin de job `office` en de padtest draaien |
| 20 bewerkers op 4 cores is te zwaar | middel | middel | eigen grens `max_editors`; meting in §4.7 |
| Grote conversies houden de cpu bezig | middel | laag | één tegelijk, tijdslimiet, 100 MB, nieuwe uploads gaan voor |
| Forgotten files staan niet in de ZFS-snapshot | laag | laag | automatisch herstel binnen minuten; wat blijft, staat op `/beheer`; README |
| Status 3 stuurt een kapot document | laag | laag | herstelkopie, het origineel blijft heel |
| Alt+Shift+Q botst met een sneltoets van DS | middel | laag | spike stap 6; anders een andere combinatie; Sluiten in de kopbalk en Terug blijven altijd |
| AGPL: dat ons platform erbuiten blijft, is een lezing | laag | hoog | onveranderd image, alleen via HTTP; geen juridisch advies [niet getest]; we patchen DS niet |
| Eén proces: sloten en tellers zitten in het geheugen | — | — | goed voor standalone; het cluster vraagt later één DS per document (`shardkey`) en gedeelde status |
| Collabora is alleen een document | middel | middel | de naad en de contracttests houden het op ±1 week; Vraag 1 en 5 |

---

## 6. Verwerkte en afgewezen kritiek

Revisie 2 vult deze sectie.
