# Spike O0: de echte DS 10.0.0.1

Datum: 26 oktober 2026. Gedaan door O0-spike, volgens §3.7 van `docs/fase-4/bouwontwerp.md`
(revisie 2). Alle 18 stappen zijn gedaan tegen de echte DS in de sandbox. Aan het eind van O0 deed
O0-e2e stap 2, 5 en 11 opnieuw via de kern in de app (proxy en tunnel van `app.serve`); zie
"Stap 2, 5 en 11 opnieuw, via de kern in de app".

**Kort.** Geen enkele uitkomst vraagt een andere architectuur. Stap 1 vond geen pad dat de editor
nodig heeft en dat we niet willen doorgeven. Stap 12 liet een leesbaar token zien, dus de terugval
van Keuze 20 is niet nodig. Wel wijkt een aantal dingen af van wat het ontwerp aanneemt. Die staan
onderaan in "Afwijkingen van het ontwerp". De zeven belangrijkste:

1. Het sessietoken van DS is niet getekend met het office-geheim. De sleutel is
   `HMAC-SHA256(office-geheim, "editor-session")` als hex-tekst.
2. Een afbeelding uploaden werkt alleen als de proxy `Authorization` van de browser doorgeeft
   (op `upload/<key>`).
3. Afdrukken gaat via `downloadas/<key>`, niet via `printfile`. De query `cmd` bevat een sessietoken.
4. Documentdata heeft ook paden zonder `_<n>`: `cache/files/data/<key>/Editor.bin/…` en
   `cache/files/data/<key>/media/…`.
5. `'unsafe-eval'` blijft in de CSP. Zonder start de editor niet.
6. Een bestand onder de grens van 50 MB XML kan DS laten vastlopen op geheugen (2 GB). Bij een
   docx van 42 MB XML ging docservice zelf eruit (OOM), en daarmee alle open sessies. Een latere
   meting (stap 18 verder) zag x2t al vastlopen bij 10 MB XML in een docx; de invoercontrole
   heeft nu 4 MB (OOXML-tekst en -presentaties), 2 MB (ODF) en 10 MB (een echt werkblad).
7. Twee à drie minuten na de start van een **nieuwe** container herstart DS zichzelf (fonts). Open
   sessies zijn dan weg, zonder callback en zonder herstelbestand.

De spike-proxy is niet de app. Daarom deed O0-e2e stap 2, 5 en 11 opnieuw via de kern in de app.
Stap 2 en 11 kloppen ook zo. Twee nieuwe dingen:

8. Na een herstart van de app verbindt de editor meteen opnieuw. Maar kon DS zijn status 2 al
   kwijt vóór die herverbinding, dan is de sessie dicht en komt `onRequestRefreshFile` (A21). Met
   de standaardconfig van DS (3 pogingen) gaat de opslag na ±12 s uitval naar de forgotten files.
9. Een pptx opent via de app soms niet (4 van 22 keer), door een race in de code van DS die de
   wachttijd van de proxy vaker laat optreden (A22). Rechtstreeks bij DS: 0 van 17.

---

## Opzet

**Image.** `onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3`
(10.0.0, build 169). Runtime "memory": geen Postgres, Redis of RabbitMQ. De converter draait in
het proces van docservice (`convertermaster: memory runtime detected - embedded converter runs
inside DocService; no workers will be forked`).

**Geheimen.** Twee keer 32 willekeurige bytes als hex, gemaakt bij de start in `.jwt` en `.link`
(rechten 600). Ze gaan alleen als omgeving naar Docker. Na afloop gewist. Een zoektocht over alle
bestanden van de spike (logs, opnames, pagina's) vond geen van beide, en ook de afgeleide sessiesleutel niet.

**De container** (`spike-ds`, poort 18091 alleen op 127.0.0.1):

```sh
JWT_SECRET="$(cat .jwt)" SECURE_LINK_SECRET="$(cat .link)" docker run -d --name spike-ds -p 127.0.0.1:18091:80 \
 -e JWT_ENABLED=true -e JWT_SECRET -e JWT_HEADER=Authorization -e SECURE_LINK_SECRET \
 -e WELCOME_PAGE_ENABLED=false -e PLUGINS_ENABLED=false \
 --add-host=host.docker.internal:host-gateway \
 --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID --cap-add DAC_OVERRIDE \
 --cap-add FOWNER --cap-add NET_BIND_SERVICE --cap-add KILL \
 --security-opt no-new-privileges:true --pids-limit 512 --memory 2g --memory-swap 2g --stop-timeout 60 \
 -v $PWD/cfg/local-production-linux.json:/etc/onlyoffice/documentserver/local-production-linux.json:ro \
 onlyoffice/documentserver@sha256:ca17889f…50b3
```

`cfg/local-production-linux.json` (zoals in O6, zonder `token.session`):

```json
{
  "services": { "CoAuthoring": { "callbackBackoffOptions": { "retries": 8 },
                                 "server": { "docbuilderEnable": false } } },
  "FileConverter": { "converter": { "inputLimits": [
    { "type": "docx;dotx;docm;dotm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
    { "type": "xlsx;xltx;xlsm;xltm", "zip": { "uncompressed": "300MB", "template": "*.xml" } },
    { "type": "pptx;ppsx;potx;pptm;ppsm;potm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
    { "type": "vsdx;vstx;vssx;vsdm;vstm;vssm", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
    { "type": "odt;ott;odp;otp", "zip": { "uncompressed": "50MB", "template": "*.xml" } },
    { "type": "ods;ots", "zip": { "uncompressed": "150MB", "template": "*.xml" } } ] } },
  "aiSettings": { "enable": false }
}
```

Voor één stap elk draaiden even extra containers, met dezelfde opties:
`spike-ds2` (`token.session.expires: "2m"`, stap 15 en de herstarttest), `spike-ds3`
(`GENERATE_FONTS=false`) en `spike-ds4` (`maxprocesscount: 2`, stap 16). Alle containers zijn weer
weg; de images zijn gebleven.

**De spike-proxy** (`proxy.py`, een kopie van `fase4/proef/proxy.py`, uitgebreid). Hij speelt de
publieke kant van de app:
- `/office` eraf, `X-Forwarded-Prefix: /office`, `X-Forwarded-Host`, `X-Forwarded-Proto` en
  `X-Forwarded-For` erbij; `Cookie` en `Authorization` eruit (behalve met `AUTH_ACTION=1`:
  `Authorization` door op editoracties);
- elk verzoek krijgt een klasse (STATIC, PAGE, DATA, SESSION, SETTINGS, ACTION of DENY) en wordt
  gelogd met methode, pad, status, cookie, `Sec-Fetch-Dest`, content-type en grootte;
- `GATE=observe` logt alleen; `GATE=enforce` weigert zoals §3.10 (404 voor HTML zonder cookie, 401
  voor de rest zonder cookie, 404 voor DENY);
- `CSP=full` zet de CSP van Keuze 9 op elke HTML van DS; `CSP=noeval` dezelfde zonder `'unsafe-eval'`;
- de tunnel pompt bytes en logt elk WebSocket-frame (ook met permessage-deflate, als dat er was).

Hij draaide op poort 18099, op 127.0.0.1 (een veilige context voor Chromium) en op het eigen
netwerkadres van de machine, 192.0.2.2 (gewone http, geen veilige context).

**De stub** (`stub.py`, op 172.17.0.1:18766) speelt de interne luisteraar van de app:
`/doc/<naam>` = `/internal/office/doc/{key}`, `/src/<naam>` = `/internal/office/src/{grant}`,
`/callback` = `/internal/office/callback`. Hij controleert de JWT's, haalt `url` en `changesurl`
op na status 2 en 6, en kent standen voor zijn antwoord (`ok`, `500`, `503`, `error1`) en voor
`Repr-Digest` (`none`, `ok`, `bad`), ook per key-prefix.

De scripts en ruwe logs van de spike stonden in een tijdelijke werkmap en zijn niet in de repo
gekomen. Hieronder staan hun namen alleen als herkomst. De opnames (zonder geheimen) staan in
`api/tests/fake_docserver/recorded/` (zie "Bestanden").

**De browser.** Playwright met Chromium `/opt/pw-browsers/chromium`, `--no-proxy-server`, headless,
locale nl-NL. Steeds vanuit `api/`:

```sh
cd api && BASE=http://127.0.0.1:18099 uv run --with playwright python <spike>/actions.py <label> docx
```

De hostpagina (`pages/<naam>.html`) laadt `/office/web-apps/apps/api/documents/api.js` en
`/pages/host.js`. Die logt elk event van de editor in `window.__events`.

---

## Overzicht

| Stap | Vraag | Uitkomst | Gevolg voor het ontwerp |
|---|---|---|---|
| 1 | Padinventaris | 27 padpatronen; HTML zijn alleen de drie `…/main/index.html` | de tabel van §3.10 hieronder is de vaste lijst |
| 2 | Cookie bij elk verzoek | ja, ook uit de service worker en bij de upgrade; via de app ook na een herstart | geen terugval nodig |
| 3 | Opslaan → status 6, `forcesavetype 1` | ja, in docx, xlsx en pptx | zoals ontwerp |
| 4 | `retries: 8`; ook bij "verbinding geweigerd" | ja: 9 pogingen over ±255 s, daarna herstelbestand; ook bij geweigerd | zoals ontwerp |
| 5 | Herstart van de app tijdens bewerken | verbindt vanzelf opnieuw; geen `onRequestRefreshFile`; niets kwijt; status 2 komt. Via de app: zo alleen als de editor terug is vóór de volgende poging van DS, anders `onRequestRefreshFile` | `onRequestRefreshFile` altijd afhandelen; config van O6 nodig (A21) |
| 6 | Configsleutels in 10.0 | `close` werkt (`text` mag weg), `uiTheme` werkt, plugins, help, macro's en AI uit; Alt+Shift+Q vrij | twee sleutels erbij: `suggestFeature: false`, geen `onRequestSaveAs` |
| 7 | `Repr-Digest` | goed: aangenomen; fout: `EBADDIGEST`, 3 pogingen, dan -4 | zoals ontwerp |
| 8 | Lege xlsx en pptx | openen in edit-modus, bewerken, opslaan, PDF | docx: `word/settings.xml` erbij |
| 9 | Volledige CSP | 0 schendingen bij alle acties; zonder `'unsafe-eval'` start de editor niet | `'unsafe-eval'` blijft |
| 10 | Status 3 | opgewekt; er is geen `url` en geen herstelbestand | herstel niet op `url` bouwen |
| 11 | Tijden | koud 2,9–4,1 s, warm 2,3–3,4 s, sluiten → status 2 5,5–5,8 s. Via de app: koud 2,8–4,8 s, warm 2,4–3,2 s, status 2 5,8 s; pptx soms niet klaar | proxy ±0,3 s trager dan DS zelf; herhaling bij pptx (A22) |
| 12 | Eerste socketpakket | CONNECT `40{…}` met `token` (eerste keer) of `session` (opnieuw), plus `data.jwtOpen`; key in `data.docid`, `data.openCmd.id` en `document.key` | `recorded/socket.json`; sessietoken met afgeleide sleutel |
| 13 | Het merk | `lastsave` stijgt, maar per hele seconde; `history.changes` niet cumulatief | merk "niet lager" (`>=`) plus de sha |
| 14 | Na `drop`; token van een andere key | ja en ja | Keuze 6 en 7 zijn nodig, zoals verwacht |
| 15 | Sessietoken met `"2m"` | geen vernieuwing; herverbinden na verloop: 4005 | de standaard (30 dagen) blijft |
| 16 | Conversie en editor tegelijk | editor wacht op de conversie (24 s in plaats van 3,4–4,7 s); healthcheck niet | ontwerp klopt; `maxprocesscount` helpt niet |
| 17 | `Sec-Fetch-Dest` | iframe/empty/serviceworker/script/style; upgrade zonder kop; via LAN geen koppen | zoals ontwerp; `empty` nodig voor de pagina |
| 18 | `inputLimits` voor ODF | boven de grens -10 (odt en docx); onder de grens OOM bij 2 GB | grens van Keuze 23 te hoog voor 2 GB |

---

## Stap 1. Padinventaris

**Wat.** Per editor (docx, xlsx, pptx): koud openen (nieuwe browsercontext), typen, Opslaan (knop),
downloaden als PDF en als eigen formaat, afdrukken (menu en knop in de kopbalk), afbeelding uit een
bestand, een opmerking, sluiten, en daarna warm openen (tweede document, zelfde context). Eerst met
`GATE=observe` (runs `inv127`, `inv127x`, `inv127p`), daarna met `GATE=enforce` (runs `enf127`,
`enfx`, `enfp` op 127.0.0.1 en `enflan` op 192.0.2.2). Afdrukken in xlsx en pptx via het menu
lukte in het script niet (klik op `#fm-btn-print` hing); via de knop in de kopbalk wel
(`explore4.py`), met dezelfde paden.

**Bewijs.**

```sh
./run_proxy.sh 127.0.0.1,192.0.2.2 18099 CSP=full GATE=enforce AUTH_ACTION=1
cd api && BASE=http://127.0.0.1:18099 uv run --with playwright python <spike>/actions.py enf127 docx
python3 -I analyze.py inv127,inv127x,inv127p,enf127,enfx,enfp,enflan --json logs/inv-all.json
```

Uitslag van `enf127` (docx, enforce):

```
open-koud:end {"secs": 3.69}
opslaan-knop:end {"knop": "#slot-btn-dt-save", "status6": true, "forcesavetype": 1}
download-pdf:end {"naam": "d-367fcae3.pdf", "url": ".../office/cache/files/data/akt-enf127-…_8856/output.pdf/d-367fcae3.pdf?md5=…&expires=…&shardkey=…&filename=…"}
download-docx:end {"naam": "d-367fcae3.docx", ...}
afdrukken-menu:end, afdrukken-kopbalk:end {"zichtbaar": ["#slot-btn-dt-print"]}
afbeelding:end {"item": "Afbeelding uit bestand"}
opmerking:end {"knop": "Toevoegen"}
sluiten:end {"status": 2, "secs": 5.83}
open-warm:end {"secs": 2.65}
fouten 0; console: alleen 404 op /favicon.ico (de hostpagina)
```

Alle paden, met klasse, aantallen, cookie, `Sec-Fetch-Dest` en content-type, staan in
`recorded/paths.json`. Samengevat (na normaliseren: `/office` eraf, versiesegment = `<V>`):

| Methode | Pad | Klasse | Wanneer |
|---|---|---|---|
| GET | `web-apps/apps/api/documents/api.js` (zonder `<V>`) | statisch | openen |
| GET | `<V>/web-apps/apps/{documenteditor,spreadsheeteditor,presentationeditor}/main/index.html` | editorpagina | openen (het enige HTML) |
| GET | `<V>/web-apps/**` (.js, .css, .json, .template, .svg, .png) | statisch | openen, acties |
| GET | `<V>/sdkjs/**` (.js, .json, .png, .wasm, .bin; .css in xlsx) | statisch | openen |
| GET | `<V>/fonts/<n>` | statisch | openen |
| GET | `<V>/dictionaries/**` (.aff, .dic) | statisch | typen (spelling) |
| GET | `<V>/document_editor_service_worker.js` | statisch | openen (200 of 304) |
| GET | `<V>/themes.json` | editorinstellingen | openen |
| GET | `cache/files/data/<key>/Editor.bin/Editor.bin` | documentdata | openen (zonder `_<n>`) |
| GET | `cache/files/data/<key>/media/<hash>.<ext>/<hash>.<ext>` | documentdata | afbeelding (zonder `_<n>`) |
| GET | `cache/files/data/<key>_<n>/output.<ext>/<naam>` | documentdata | downloaden en afdrukken |
| POST | `<V>/downloadas/<key>?cmd=<json>&shardkey=<key>` | editoractie | downloaden en afdrukken (body tot 244 KB) |
| POST | `<V>/upload/<key>?shardkey=<key>` | editoractie | afbeelding (met `Authorization: Bearer <sessietoken>`) |
| upgrade | `<V>/doc/<key>/c/?shardkey=<key>&EIO=4&transport=websocket` | sessie | openen, herverbinden |

Niet gezien, in geen enkele run: `printfile/…`, `savefile/…`, `downloadfile/…`, `plugins.json`
(met `plugins: false`), polling (`GET`/`POST doc/<key>/c/` zonder upgrade), `preload.html`, een 302.

Welke paden zijn HTML: alleen de drie `…/main/index.html`. Rechtstreeks bij DS zijn er meer HTML-paden
die de editor niet vraagt: `/` (302 naar `/welcome/`), `/welcome/`, `/index.html` en
`/<V>/index.html` (een statuspagina "Server is functioning normally. Version: 10.0.0. Build: 169"),
`…/api/documents/preload.html` en `…/api/documents/cache-scripts.html` (`logs/ds-paths.txt`).

De weigerlijst, nagespeeld via de proxy in enforce-stand (`logs/enforce-neg.txt`):

```
GET  /office/<V>/web-apps/apps/documenteditor/main/index.html            -> 404   (geen cookie)
GET  /office/<V>/web-apps/apps/documenteditor/main/index.html (document) -> 404   (cookie, Sec-Fetch-Dest: document)
GET  /office/<V>/web-apps/apps/api/documents/preload.html                -> 404
GET  /office/<V>/web-apps/apps/api/documents/api.js                      -> 200
GET  /office/<V>/sdkjs/word/sdk-all-min.js                               -> 200
POST /office/<V>/sdkjs/word/sdk-all-min.js                               -> 404
GET  /office/cache/files/data/akt-x/Editor.bin/Editor.bin                -> 401
GET  /office/<V>/doc/akt-x/c/?EIO=4&transport=polling                    -> 401
GET  /office/<V>/themes.json, /office/<V>/plugins.json                   -> 401
POST /office/<V>/downloadas/akt-x?cmd=%7B%7D, /office/<V>/upload/akt-x   -> 401
GET  /office/, /office/welcome/, /office/<V>/index.html, /office/index.html,
     /office/healthcheck, /office/<V>/healthcheck, /office/info/info.json,
     /office/cache/files/forgotten/…, /office/cache/files/data/conv_…,
     /office/example/, /office/meta/formats, /office/favicon.ico         -> 404
POST /office/<V>/command, /office/coauthoring/CommandService.ashx, /office/<V>/converter,
     /office/<V>/ConvertService.ashx, /office/<V>/dummyCallback, /office/<V>/ai-proxy -> 404
GET  /office/<V>/baseurl                                                 -> 404
```

In de enforce-runs (alle drie editors, beide adressen) weigerde de proxy niets. De 29 weigeringen
in de log zijn allemaal van de lijst hierboven.

Wat DS zelf antwoordt als je hem rechtstreeks vraagt (`logs/ds-paths.txt`), en waarom die paden
dicht moeten: `POST /<V>/command` en `/coauthoring/CommandService.ashx` → `{"error":6}`;
`POST /<V>/converter`, `/converter/from-file` en `/ConvertService.ashx` → -8;
`GET /<V>/baseurl` → `http://127.0.0.1:18099/office` (echo van de koppen);
`POST /<V>/dummyCallback` → `{"error":0}`; `GET /<V>/plugins.json` → de config van de marketplace;
`GET /meta/formats` → de formatenlijst; `POST /<V>/ai-proxy` → 403 "AI is disabled".

**Betekenis.** De tabel van §3.10 hieronder is de vaste lijst. Er is geen pad dat de editor nodig
heeft en dat we niet willen doorgeven. Wel drie aanpassingen: documentdata ook zonder `_<n>`;
`upload` met `Authorization`; `cmd` in `SECRET_PARAMS`. De editorpagina is geen "elke `*.html`",
maar precies de bekende pagina's (anders gaat `/<V>/index.html` mee).

## Stap 2. Cookie bij elk verzoek

**Wat.** Gaat het poort-cookie mee bij elk verzoek naar `/office/`, ook uit workers en uit de
service worker van DS?

**Bewijs.** Dezelfde runs als stap 1. In `recorded/paths.json` staat per pad `"cookie": {"ok": n}`.
Bij elk pad is dat het totaal: 1181 keer `web-apps/**.js`, 97 keer `fonts/<n>`, 12 upgrades, 17 keer
`downloadas`, 6 keer `upload`, enzovoort. Geen enkel verzoek zonder cookie. Ook verzoeken die de
service worker deed (Playwright: `request.service_worker`), en ook via 192.0.2.2.

**Betekenis.** Ja. Het ontwerp kan het cookie eisen op elk dynamisch pad. Geen terugval nodig.
Let op: in de spike veranderde het cookie bij elke herstart van de proxy (een nieuwe willekeurige
waarde). In de app is het een getekend token, dat een herstart overleeft. O0-e2e heeft dat opnieuw
gemeten via de app: zie onderaan.

## Stap 3. Knop Opslaan

**Wat.** Geeft de knop Opslaan status 6 met `forcesavetype: 1`?

**Bewijs.** In elke run van stap 1, alle drie editors:

```
opslaan-knop:end {"knop": "#slot-btn-dt-save", "status6": true, "forcesavetype": 1}
```

Een `forcesave` via `/command` geeft status 6 met `forcesavetype: 0` en de `userdata` van het
commando (`recorded/commands.json`, `callback_na_forcesave`).

**Betekenis.** Ja. Zoals ontwerp (Keuze 18).

## Stap 4. Opnieuw proberen

**Wat.** Werkt `callbackBackoffOptions.retries: 8` via `local-production-linux.json`? Ook bij
"verbinding geweigerd"?

**Bewijs.** `save_tests.py`, deel 4a en 4b (`logs/save_tests-4a-4b.json`).

4a: de stub antwoordt 503 op elke callback van keys die met `ret5-` beginnen.

```
4a-503 {"pogingen": 9, "tijden": [5.7, 6.7, 8.7, 12.7, 20.7, 36.7, 68.8, 132.8, 260.8],
        "tussen": [1.0, 2.0, 4.0, 8.0, 16.0, 32.1, 64.0, 128.0],
        "forgotten_list": [200, {"error": 0, "keys": ["ret5-83cede29d66a"]}]}
4a-getForgotten {"key": "ret5-83cede29d66a", "error": 0,
                 "url": "http://127.0.0.1:18091/cache/files/forgotten/ret5-…/output.docx/output.docx?md5=…&expires=…"}
```

4b: de stub gestopt vlak voor het sluiten, na 40 s weer gestart.

```
4b-stub-gestopt
4b-stub-weer-aan {"na_s": 40.3}
4b-status {"status": 2, "na_sluiten_s": 68.9, "saved": 200}
```

In de log van docservice: zes keer `Error: connect ECONNREFUSED` voor die key, daarna lukte het.

**Betekenis.** Ja, beide. 1 + 8 pogingen met 1, 2, 4 … 128 s ertussen, samen ±255 s. Daarna komt
het bestand bij `getForgottenList`. Ook "verbinding geweigerd" telt als fout om opnieuw te proberen.
Het ontwerp klopt (Keuze 21 voor het herstel). Let op: DS logt bij elke mislukte poging de hele
callback, met de opslaglinks (`md5`, `expires`), op niveau ERROR. Zie "Extra bevindingen".

## Stap 5. Herstart van de app terwijl iemand bewerkt

**Wat.** Breekt de tunnel of stopt de app, verbindt de editor dan vanzelf opnieuw? Komt
`onRequestRefreshFile`? Gaat er iets verloren? Komt status 2 later toch?

**Bewijs.** `reconnect.py rc1` (uitval 20 s) en `reconnect.py rc2` (uitval 60 s). Eerst een
tunnelbreuk (`/__ctl/close-ws`), dan de proxy en de stub gestopt en later weer gestart. Tijdens de
uitval tikt het script "Regel drie".

```
rc2: open 3.6 s; breuk → terug na 1.18 s; herstart:stop; uitval:getypt;
     herstart:start na 64 s → "connected" 0.25 s later (64.52 s na de stop)
events: ... ["info","{\"connection\":\"disconnected\"}"], ["info","{\"connection\":\"reconnecting\"}"],
        ["info","{\"connection\":\"connected\"}"]
refresh 0
saved_has {"Regel een": true, "Regel twee": true, "Regel drie": false, "Regel vier": true}
sluiten:status {"status": 2, "secs": 5.75}
uitval_meldingen ["toolbar-mask: ", "status-label: Alle wijzigingen zijn opgeslagen", ...]
```

rc1 gaf hetzelfde (terug na 1,42 s en na 24,07 s). DS geeft in het `auth`-antwoord
`settings.reconnection: {attempts: 50, delay: 2000}`.

**Betekenis.** Ja: de editor verbindt vanzelf opnieuw, ook na 64 s. Er komt geen
`onRequestRefreshFile`. Wat de editor had aangenomen, is er nog. "Regel drie" is niet verloren
maar nooit aangenomen: tijdens de uitval legt de editor een masker over de werkbalk en neemt hij
geen invoer aan. Status 2 komt gewoon na het sluiten. Het ontwerp klopt. Nieuw: bij elke
herverbinding stuurt DS opnieuw een callback met status 1 (zie stap 14). De app moet status 1 dus
vaak kunnen krijgen. Via de app opnieuw gemeten (onderaan): dit geldt alleen als de editor terug is
vóórdat DS zijn status 2 kwijt kan (A21).

## Stap 6. Configsleutels in 10.0

**Wat.** `customization.close` (met en zonder `text`) met `onRequestClose`, `uiTheme`, `macros`,
`macrosMode`, `plugins`, `help`; werkt `aiSettings.enable: false`; gebruikt DS Alt+Shift+Q zelf?

**Bewijs.** `step6.py` en `step6b.py` (`logs/step6.json`, `logs/step6b.json`, `logs/step6be.json`),
docx, per variant een eigen editor.

```
a_close_tekst  {"close": {"visible": true, "text": "Terug naar bestanden"}}
   knop #slot-btn-close zichtbaar, aria-label "Terug naar bestanden"; menu Bestand: "fm-btn-close:Terug naar bestanden"
   na_klik_x ["documentReady", "requestClose"]
b_close_zonder_tekst {"close": {"visible": true}}
   aria-label "Bestand sluiten"; menu: "fm-btn-close:Bestand sluiten"; na_klik_x [..., "requestClose"]
c_geen_close {}  → geen sluitknop, geen menu-item
uiTheme "theme-dark"   → body: theme-dark, theme-type-dark
uiTheme "default-dark" → body: theme-night, theme-type-dark
geen uiTheme           → body: theme-white, theme-type-light
plugins false → tab "Plug-ins" verborgen; plugins true → tab zichtbaar
help false → geen "Help" in het menu; help true → "fm-btn-help:Help"
macros false → #btn-macros niet zichtbaar
btn-general-ai: in elke variant aanwezig maar niet zichtbaar; rechtstreeks POST /<V>/ai-proxy → 403 "proxyRequest: AI is disabled"
menu Bestand standaard: ... "fm-btn-save-copy:Kopie opslaan als", ..., "fm-btn-suggest:Functie voorstellen"
e_suggest_ai_saveas {"suggestFeature": false} en geen onRequestSaveAs in de events
   menu: Menu sluiten, Opslaan, Downloaden als, Afdrukken, Beveiligen, Documentinfo, Geavanceerde instellingen
alt_shift_q {"keys": [{"key": "Q", "alt": true, "shift": true, "prevented": false, "active": "area_id"}],
             "focus_voor": "area_id", "focus_na": "area_id", "dialogen": []}
```

**Betekenis.**
- `close` werkt in 10.0 en geeft `onRequestClose`. `text` is niet nodig: zonder tekst zegt DS
  zelf "Bestand sluiten" in de taal van `lang`. Volgens het ontwerp laten we `text` dan weg.
- `uiTheme` werkt. `"theme-dark"` en `"default-dark"` geven donker, zonder sleutel is het licht.
  Een thema "volg het systeem" is niet getest.
- `plugins: false`, `help: false`, `macros: false` doen wat ze moeten. AI is uit: de knop is er niet
  en `/ai-proxy` weigert (`aiSettings.enable: false` werkt).
- Twee sleutels erbij, die het ontwerp nog niet had:
  - `customization.suggestFeature: false`. Anders staat in het menu "Functie voorstellen", een
    link naar buiten.
  - de hostpagina zet **geen** `onRequestSaveAs`. Staat dat event er wel, dan toont DS
    "Kopie opslaan als", en dat bouwen we niet.
- DS gebruikt Alt+Shift+Q niet: het event wordt niet tegengehouden, de focus blijft, er komt geen
  dialoog. Maar de toets komt binnen in het venster van de iframe (focus op `area_id`), niet in de
  hostpagina. De pagina moet dus luisteren op `iframe.contentWindow` (dat mag, zelfde origin), zoals
  het ontwerp al zegt.

## Stap 7. `Repr-Digest`

**Wat.** Neemt DS `Repr-Digest` aan op `/internal/office/doc/{key}` (hier `/doc/<naam>` van de stub)?

**Bewijs.** `open_close.py digok` en `digbad` met de stub op `digest: ok` en `digest: bad`; ook via
`/converter` (`conv_tests.py`).

```
GET /doc/d-dd5ddada.docx  koppen van DS: Want-Repr-Digest: sha-256=10, Want-Content-Digest: sha-256=10
  served {"digest": "ok"}  → editor opent (3.36 s), sluiten → status 2 na 5.8 s
GET /doc/d-4d37d338.docx  served {"digest": "bad"}, 3 keer (20:00:34, :35, :36)
  docservice: error downloadFile ... attempt=1..3; code:EBADDIGEST ... Repr-Digest mismatch for sha256
  editor: onError -4 "Download mislukt."; callback status 4
/converter met digest bad → {"error": -4} na 4 s; met digest ok of none → PDF
```

De stub stuurt `Repr-Digest: sha-256=:<base64 van sha256>:`.

**Betekenis.** Ja. DS vraagt er zelf om, neemt een juiste aan en weigert een foute (drie pogingen,
dan -4). Zoals ontwerp.

## Stap 8. Eigen lege bestanden

**Wat.** Openen de lege xlsx en pptx van Keuze 24 in edit-modus, en zetten ze om naar PDF?

**Bewijs.** `make_blank.py` maakt `leeg-nl` en `leeg-en` van docx, xlsx en pptx, alleen met
`zipfile`, vaste datum. Inhoud: `[Content_Types].xml`, `_rels/.rels`, het hoofddeel met zijn
`_rels`, stijlen (docx, xlsx), en voor pptx master, layout, dia en thema.

```
inv127x (xlsx): open-koud 4.12 s; opslaan-knop status6 true, forcesavetype 1; download xlsx; sluiten status 2
inv127p (pptx): open-koud 3.75 s; opslaan-knop status6 true; download-pdf %PDF; download pptx; sluiten status 2
enfx (xlsx):    download-pdf {"kop": "255044…"} (%PDF)
conv_tests: leeg-nl.xlsx → PDF 2881 B, leeg-nl.pptx → 3003 B, leeg-en.xlsx → 2881 B, leeg-en.pptx → 3003 B
```

Bij de docx kwam bij "Downloaden als docx" een melding: "Het document wordt opgeslagen in het
nieuwe formaat…" (knop OK). De lege docx heeft geen `word/settings.xml` met een
`compatibilityMode`.

**Betekenis.** Ja voor xlsx en pptx. Voor de docx van O4: voeg `word/settings.xml` toe met
`<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word"
w:val="15"/></w:compat>` (en noem het in `[Content_Types].xml` en `document.xml.rels`). Dan komt
die melding niet. Dat is niet meer opnieuw getest.

## Stap 9. De volledige CSP van Keuze 9

**Wat.** Werkt de editor met de volledige CSP op de HTML van DS, bij alle acties van stap 1? Ook
zonder `'unsafe-eval'`?

**Bewijs.** Alle runs van stap 1 draaiden met `CSP=full`. De init-script van de context logt elke
`securitypolicyviolation` als `CSPV …`, ook in iframes.

```
enf127, enflan, inv127, inv127x, inv127p: CSPV-meldingen: 0; console: alleen 404 op /favicon.ico
```

Zonder `'unsafe-eval'` (`CSP=noeval`, `csp_probe.py`, `logs/csp-noeval.json`):

```
JSERR …/documenteditor/main/index.html Uncaught EvalError: Refused to evaluate a string as JavaScript
because 'unsafe-eval' is not an allowed source of script ... "script-src 'self' 'unsafe-inline'"
CSPV {"dir":"script-src","blocked":"eval","src":"…/web-apps/vendor/underscore/underscore.js","line":936}
events: []   (geen appReady, geen documentReady)
```

**Betekenis.** De volledige CSP van Keuze 9 werkt, met alle acties, in alle drie editors, zonder
één schending. `'unsafe-eval'` blijft erin: de templates van underscore gebruiken `new Function`,
en zonder start de editor niet.

## Stap 10. Status 3

**Wat.** Kan status 3 ontstaan, en wat staat er dan in `url`?

**Bewijs.** `status3.py A` (de cache van de key in DS gewist vlak voor het sluiten) en
`status3.py B` (`Editor.bin` vervangen door rommel).

```
A: [{"dt": 5.0, "status": 3, "url": "", "changesurl": false,
     "keys": ["actions", "key", "lastsave", "notmodified", "status", "token", "users"]}]
B: [{"dt": 5.1, "status": 3, "url": "", "changesurl": false, "keys": [... zelfde ...]}]
getForgottenList daarna: geen herstelbestand voor die key
```

**Betekenis.** Status 3 bestaat. Er is **geen** veld `url` (en geen `changesurl`); de body heeft
alleen `actions`, `key`, `lastsave`, `notmodified`, `status`, `token`, `users`. Er komt ook geen
herstelbestand. Het herstel bij status 3 kan dus niet op `url` bouwen: de sessie gaat dicht als
"verloren", en wat het laatst is opgeslagen (de laatste status 6) blijft. De nep-DS moet status 3
zonder `url` sturen.

## Stap 11. Tijden via de proxy

**Wat.** Koud en warm openen via proxy en tunnel, tegen 4,1 s via nginx in de proef.

**Bewijs.** De merktekens van alle runs (`logs/actions-*.json`, `logs/oc-*.json`,
`logs/reconnect-*.json`):

```
koud openen  (n=20): 2.87 – 4.12 s   (docx 3.22–4.09, xlsx 3.68–4.12, pptx 3.75–3.86; LAN 2.87)
warm openen  (n=8):  2.32 – 3.39 s
sluiten → status 2 (n=10): 5.50 – 5.83 s
```

**Betekenis.** Via een proxy met tunnel is openen niet trager dan via nginx (4,1 s). Sluiten tot
status 2 duurt ±5,7 s; dat is vooral de wachttijd van DS na de laatste gebruiker
(`savetimeoutdelay` 5000 ms). Via de kern in de app opnieuw gemeten: onderaan.

## Stap 12. Het eerste socketpakket

**Wat.** De frames van de browser na de upgrade. Waar staat het token? Welk veld heeft de key?

**Bewijs.** `record_socket.py` maakt `recorded/socket.json` uit `logs/ws.log` (tokens vervangen door
`<JWT …>`, met de gedecodeerde claims en een controle van de handtekening). Drie tunnels: de eerste
verbinding, een herverbinding na een breuk, en een herverbinding na een herstart van de proxy.

```
s2c 1  0{"sid":…,"upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":100000000}
c2s 1  40{"data":{"type":"auth","docid":"<key>","token":"fghhfgsjdgfjs",
          "openCmd":{"c":"open","id":"<key>",…},"jwtOpen":"<config-token>",…},
          "token":"<config-token>"}                               ← eerste verbinding
c2s 1  40{"data":{…,"jwtOpen":"<config-token>",…},"session":"<sessietoken>"}   ← herverbinden
s2c 2  40{"sid":…}
s2c 3  42["message",{"type":"license",…}]
c2s 2  42["message",{"type":"auth","docid":"<key>",…,"jwtOpen":"<config-token>"}]            ← eerste keer
c2s 2  42["message",{"type":"auth",…,"sessionId":"…","jwtSession":"<sessietoken>"}]         ← herverbinden
s2c 4  42["message",{"type":"auth","result":1,"sessionId":…,"jwt":"<sessietoken>",
          "settings":{"reconnection":{"attempts":50,"delay":2000},…}}]
controle: config-token → office-geheim klopt;
          sessietoken  → office-geheim klopt niet; HMAC-SHA256(office-geheim,"editor-session").hex klopt
          (iat → exp = 2592000 s = 30 dagen); document.key == key van het pad: true
Sec-WebSocket-Extensions van de browser: "permessage-deflate; client_max_window_bits"; van DS: geen
```

De sleutel van het sessietoken is gevonden met `derive_test.py` (proberen van afleidingen tot er één
past).

**Betekenis.**
- Het token is leesbaar. De terugval van Keuze 20 is niet nodig.
- Het eerste pakket is de CONNECT van socket.io (`40` + JSON = `handshake.auth`). Daarin:
  `token` (config-token) bij de eerste verbinding, of `session` (sessietoken) bij herverbinden, en
  altijd `data.jwtOpen` (het config-token). Het tweede pakket (`auth`) draagt `jwtOpen` of
  `jwtSession` opnieuw.
- De key staat in `data.docid`, `data.openCmd.id` (alleen eerste keer gevuld) en in `document.key`
  van elk token. `data.token` is een vaste nepwaarde (`"fghhfgsjdgfjs"`) en moet genegeerd worden.
- **Afwijking:** het sessietoken is getekend met `HMAC-SHA256(office-geheim, "editor-session")` als
  hex-tekst, niet met het office-geheim. `oo/stream.py` moet dat token met de afgeleide sleutel
  controleren, en het config-token met het office-geheim.
- Veiligere lezing, niet gemeten: de wacht kijkt niet alleen naar het eerste pakket, maar ook naar
  elk `auth`-bericht van de browser, en eist dat elke key (`docid`, `openCmd.id`, `document.key` van
  elk token) gelijk is aan de key van het pad. Of DS een CONNECT met token A en daarna een `auth`
  met token B aanneemt, is niet getest.
- DS onderhandelt geen permessage-deflate. De wacht mag daar niet op rekenen, maar moet een
  gecomprimeerd frame wel weigeren of kunnen lezen.

## Stap 13. Het merk

**Wat.** Is `lastsave` bij status 6, 6, 2 steeds hoger? Zo niet: is `history.changes` dat?

**Bewijs.** `save_tests.py 13` (`logs/save_tests-13.json`): typen, Opslaan, typen, Opslaan, sluiten.

```
status 1  lastsave null
status 6  lastsave 2026-10-26T19:33:33.000Z  forcesavetype 1  history.changes: 1
status 6  lastsave 2026-10-26T19:33:37.000Z  forcesavetype 1  history.changes: 1
status 2  lastsave 2026-10-26T19:33:42.000Z                    history.changes: 1
```

**Betekenis.** `lastsave` stijgt, maar heeft alleen hele seconden (`.000Z`). Twee keer opslaan in
dezelfde seconde geeft dus hetzelfde merk. `history.changes` is niet cumulatief: steeds 1 element,
het stuk sinds de vorige opslag. Daarom: het merk is `lastsave`, en de app neemt een opslag aan als
het merk **niet lager** is dan het vorige (`>=`), niet alleen als het hoger is. Bij een gelijk merk
beslist de sha: dezelfde inhoud is een herhaling, andere inhoud is nieuwer.

## Stap 14. Na een `drop`; een token van een andere key

**Wat.** Verbindt een script na `drop` opnieuw met zijn sessietoken, en neemt DS dat aan? En een
socket op het pad van key A met het token van key B?

**Bewijs.** `step14.py` (`logs/step14.json`) en `TWEE=1 step14.py` (`logs/step14b.json`, een
tweede gebruiker u3 op A, zodat de sessie van A openblijft). Het script speelt de echte pakketten
van de browser opnieuw af.

```
drop u1 op A → {"error": 0}
A-na-drop-met-sessietoken (u1 alleen op A): {"type":"error","description":"Update Version error","code":4008}
A-na-drop-met-sessietoken (u3 ook op A):    auth result 1, participants [u3, u1]   ← aangenomen
pad-A-config-token-B: documentOpen met Editor.bin van key B (…/cache/files/data/s14b-…/Editor.bin…&shardkey=s14a-…)
pad-A-sessietoken-B:  auth result 1, participants [u2]   ← u2 is de gebruiker van B
```

**Betekenis.** Ja en ja, zoals verwacht. `drop` onthoudt niemand: wie zijn sessietoken heeft, komt
terug zolang de sessie openstaat. Was hij de laatste, dan is de sessie al opgeslagen en dicht, en
weigert DS met 4008. DS volgt de key uit de pakketten, niet uit het pad. Daarom leggen Keuze 6 en 7
de toegang in de app: elke tunnel controleert het recht van nu, en het eerste pakket moet bij de key
van het pad horen. Ook `drop` op een key die DS niet kent, geeft `{"error": 0}`; de app kan aan dat
antwoord dus niet zien of er iemand is weggezet.

## Stap 15. Het sessietoken

**Wat.** Met `token.session.expires: "2m"` een editor 5 minuten open. Vernieuwt DS het token? Lukt
een herverbinding na 3 minuten?

**Bewijs.** `spike-ds2` met `cfg2` (alleen `"token": {"session": {"expires": "2m"}}` erbij),
proxy op 18098, `TAG=b step15.py` (`logs/step15-b.json`, `.out`). De eerste run (`step15-a`) viel
samen met de herstart van DS voor de fonts en telt niet; hij staat wel in de logs.

```
  2.3 s  s2c auth: sessietoken iat → exp = 120 s (afgeleide sleutel)
  5 – 142 s: elke 45 s getypt; geen bericht "refreshToken" van DS
180.7 s  tunnelbreuk
181.9 s  c2s CONNECT: config-token (geldig, 7200 s) + session (verlopen)
181.9 s  s2c 44{"message":"not authorized","data":{"code":4005,"description":"token:jwt expired"}}
events: … "connected", "disconnected"  (de editor blijft weg)
```

In `step15-a` stuurde DS wel `{"type":"refreshToken"}`, maar alleen bij het herverbinden na zijn
eigen herstart, samen met fout 4009 en `onRequestRefreshFile`.

**Betekenis.** Nee: DS vernieuwt het sessietoken niet bij een levende verbinding (M15 klopt hier
niet). Na verloop lukt herverbinden niet (4005), ook al is het config-token nog geldig. Dus geen
`"2h"`: de standaard van 30 dagen blijft, zoals het ontwerp voor dit geval zegt. De echte grendel
blijft Keuze 7. Let op: bij het sluiten kwam in deze run geen status 2 binnen. Dat komt door het
harnas: de stub zet de URL's van callbacks om naar DS1, dus de download van DS2 gaf 404.

## Stap 16. Conversie en editor tegelijk

**Wat.** De docx van 401 pagina's omzetten, en tegelijk een editor openen en `/healthcheck` vragen.

**Bewijs.** `step16.py` (`logs/step16.json`), en hetzelfde op `spike-ds4` met `maxprocesscount: 2`
(`logs/step16-mpc2.json`). `groot.docx`: 38 KB zip, 2,6 MB XML.

```
                        maxprocesscount 1     maxprocesscount 2
editor los               3.39 / 4.71 s         4.07 / 4.30 s
conversie los            22.04 s               22.04 s
editor tijdens conversie 24.1 s                23.58 s
conversie samen          24.03 s               24.03 s
healthcheck los          mediaan 5.6 ms        6.35 ms
healthcheck samen (n≈50) mediaan 5.7, max 31 ms   mediaan 6.3, max 159 ms
```

**Betekenis.** Een editor die opent terwijl een conversie loopt, wacht tot die klaar is (24 s in
plaats van 3–5 s). De conversie wacht niet op de editor. `/healthcheck` blijft snel. Met
`maxprocesscount: 2` verandert er niets: in de memory-runtime is er één ingebouwde converter. Het
ontwerp (een conversie wacht als een sessie opent of opslaat, één slot in de module) klopt en is
nodig. Erbij: een lange conversie houdt elk openen op. De editorpagina moet bij lang wachten iets
zeggen, en de app mag conversies van grote bestanden niet naast het bewerken laten lopen.

## Stap 17. `Sec-Fetch-Dest`

**Wat.** Welke waarde heeft `Sec-Fetch-Dest` via `localhost` (een veilige context) voor de iframe van
de editor, de frames in DS en de service worker?

**Bewijs.** `recorded/paths.json`, velden `sec_fetch_dest_localhost` en `sec_fetch_dest_lan`.

```
…/main/index.html               localhost: iframe (koud) / empty (warm, via de service worker); LAN: geen kop
document_editor_service_worker.js localhost: serviceworker
api.js                          localhost: script
web-apps/**.js                  localhost: empty (1000×, via de service worker), script (14×)
web-apps/**.css, sdkjs/**.css   localhost: style
statisch overig, Editor.bin, media, themes.json, downloadas, upload: empty
cache/files/data/<key>_<n>/output.<ext>/<naam>: iframe (DS laadt de download in een verborgen iframe)
upgrade doc/<key>/c/            geen kop (localhost en LAN)
via 192.0.2.2 (http, geen veilige context): nergens Sec-Fetch-*, en geen service worker
```

**Betekenis.** Zoals ontwerp: de editorpagina accepteert `iframe`, `frame` of `empty`, en geen kop.
`empty` is echt nodig: een warme opening haalt `index.html` via de service worker. `document` wordt
geweigerd (stap 1, 404). De upgrade heeft nooit `Sec-Fetch-Dest`; de tunnel moet zonder die kop
werken en vertrouwt op `Origin` en het cookie. Een download heeft `iframe`; de klasse documentdata
kijkt niet naar die kop, en dat moet zo blijven.

## Stap 18. `inputLimits` voor ODF

**Wat.** Geeft een odt boven de grens een fout?

**Bewijs.** `make_big.py` maakt bestanden met veel alinea's. `conv_tests.py` zet ze om via
`/converter?shardkey=<key>` (`logs/conv_tests.json`, `recorded/convert.json`).

```
groot-60.odt   (271 KB zip, 63 MB XML) → {"error": -10} na 1 s
groot-60.docx  (224 KB zip, 63 MB XML) → {"error": -10} na 1 s
middel-40.odt  (184 KB zip, 42 MB XML) → {"error": -3} na 46.6 s;  docservice: x2t … exited with signal: SIGKILL (error -80)
middel-20.odt  ( 96 KB zip, 21 MB XML) → x2t SIGKILL (OOM)
middel-40.docx (150 KB zip, 42 MB XML) → HTTP 502 van de nginx van DS na 39.7 s;
     supervisord: "exited: docservice (terminated by SIGKILL; not expected)"; docker: OOMKilled=true
groot.docx     ( 38 KB zip, 2.6 MB XML, 401 pagina's) → PDF in 22 s
proef.odt, proef.ods (klein) → PDF
memory.events van de container: oom_kill 4
```

**Betekenis.** De grens werkt: boven 50 MB XML geeft DS meteen -10, voor odt (onze nieuwe regel) en
voor docx. Maar met 2 GB geheugen is 50 MB XML veel te hoog. Een odt van 21 MB XML laat x2t al
vastlopen. Een docx van 42 MB XML laat zelfs docservice zelf stoppen, en daarmee elke open sessie.
De grens van de invoercontrole (Keuze 23: 50 MB XML voor docx, pptx, odt en odp) moet dus omlaag,
of DS krijgt meer geheugen. De veilige grens is niet gemeten. Tussen 2,6 MB (goed) en 21 MB (fout)
ligt hij ergens; bij de integratie moet hij gezocht worden (10, 15, 20 MB). Tot dan is de veiligere
keuze 10 MB XML voor tekst- en presentatieformaten. xlsx (300 MB) en ods (150 MB) zijn niet getest.

### Stap 18 verder: de XML-grens bij 2 GB (fixer van O0)

**Opzet.** DS 10.0.0.1 (digest uit `e2e/office_ds.sh`) met `--memory 2g --memory-swap 2g`, na de
font-herstart. Elk bestand gaat via `POST /converter?shardkey=<key>` (`async: false`, naar PDF)
en wordt van een kleine http-server op de host gehaald. Per bestand: de tijd, het antwoord, de
piek van de cgroup (`memory.max_usage_in_bytes`, met de paginacache erbij, dus een bovengrens) en
`oom_kill` uit `memory.oom_control`. De bestanden komen uit de fixtures: een docx en odt met veel
korte alinea's, een xlsx met rijen van tien getallen, een docx met alleen pagina-einden, een pptx
met veel dia's, een ods met rijen van tien getallen.

```
bestand        XML      tijd   antwoord   piek     oom_kill
d3.docx        3 MB     23 s   PDF        1092 MB  0
d5.docx        5 MB     41 s   PDF        1284 MB  0
d7.docx        7 MB     56 s   PDF        1758 MB  0
d10.docx      10 MB     84 s   -3         2048 MB  1  (x2t)
o3.odt         3 MB     32 s   PDF        1221 MB  0
o5.odt         5 MB     57 s   PDF        1723 MB  0
o7.odt         7 MB     61 s   -3         2048 MB  1  (x2t)
o10.odt       10 MB     50 s   -3         2048 MB  1  (x2t)
x10.xlsx      10 MB     38 s   PDF        1273 MB  0
x20.xlsx      20 MB     75 s   PDF        2047 MB  0
x30.xlsx      30 MB     97 s   -3         2048 MB  1  (x2t)
x40.xlsx      40 MB    131 s   -3         2048 MB  1  (x2t)
pb200k.docx  0,2 MB      4 s   PDF         596 MB  0  (±4.800 pagina-einden)
pb1000k.docx   1 MB      9 s   PDF         886 MB  0  (±24.000)
pb4000k.docx 3,9 MB     33 s   PDF        1993 MB  0  (±95.000)
p350.pptx    1,2 MB      5 s   PDF         593 MB  0  (350 dia's)
p1400.pptx   4,5 MB     12 s   PDF         766 MB  0  (1.400 dia's)
s1.ods         1 MB      2 s   PDF         580 MB  0
s2.ods         2 MB      3 s   PDF         590 MB  0
s4.ods         4 MB      5 s   PDF         623 MB  0
```

Na elke OOM gaf `/healthcheck` nog `true`: de kernel koos x2t (het grootste proces), docservice
bleef. Dat is anders dan `middel-40.docx` hierboven, waar docservice zelf ging.

**Betekenis.** De invoercontrole krijgt de grenzen 4 MB XML voor OOXML-tekst en -presentaties,
2 MB voor elk ODF-document en 10 MB voor een echt OOXML-werkblad (`app/office/inspect.py`).
Dat is ruim onder de eerste fout (10, 7 en 30 MB) en onder de bestanden die al op 85 tot 100 %
zaten (d7, o5, x20). Twee dingen blijven open:

- de grootte van de XML is maar een maat. Een docx met 3,9 MB aan alleen pagina-einden kwam
  binnen de grens, maar op ±97 % van het geheugen. Wat dan helpt: de geheugengrens van de
  container (x2t gaat dood, docservice blijft), en de twee pogingen van een conversie (Keuze 25);
- pptx en ods kosten per MB veel minder dan docx en odt; ze hebben toch de strengere grens. odp
  en echte werkbladen met veel opmaak of formules zijn niet gemeten. De integratie meet opnieuw
  (§4.7).

## Stap 2, 5 en 11 opnieuw, via de kern in de app (O0-e2e)

**Opzet.** Nu niet de spike-proxy, maar de app zelf: `python -m app.serve` met office aan en
`PLATFORM_OFFICE_DEV_UNGATED=1` (de proxy en de tunnel van O0), gestart met het harnas van de e2e
(`e2e/office_harness.py`, `start_real_office`). DS 10.0.0.1 op digest uit `e2e/office_ds.sh`: met
de standaardconfig van het image (container `e2e-office-ds`), en voor stap 5 ook met de config
van O6 (`callbackBackoffOptions.retries: 8`, container `e2e-office-ds-cfg`, via
`OFFICE_DS_CONFIG`). Beide pas gebruikt na hun font-herstart.

De interne routes van de app zijn in O0 nog stubs (503 `office_off`). De DocStub van het harnas
speelt daarom de interne kant: hij serveert het document (met het download-JWT) en neemt de
callbacks aan (met het callback-JWT). Een opnemende TCP-relay vóór de poort van de app logt per
verzoek de methode, het pad, of het poort-cookie meeging, `Sec-Fetch-Dest` en of het een upgrade
was. Nooit een waarde van een cookie of query. In O0 geeft nog geen route het poort-cookie uit.
Het script maakte het daarom zelf met `gate.gate_token` (het app-geheim uit de datamap), en zette
het met de eigenschappen van `gate.issue` in de context (`HttpOnly`, `SameSite=Strict`,
`Path=/office/`). Het script staat niet in de repo; het gebruikt alleen het harnas.

### Stap 2: het cookie, nu via de app

```
127.0.0.1 (veilige context):  271 verzoeken naar /office/, alle 271 met het cookie
                              1 upgrade (/office/<V>/doc/<key>/c/), met het cookie, zonder Sec-Fetch-Dest
                              service worker /office/<V>/document_editor_service_worker.js actief
                              Sec-Fetch-Dest: empty (via de service worker), script, style, iframe
192.0.2.2 (LAN, gewone http): 269 verzoeken, alle met het cookie; nergens Sec-Fetch-*; geen service worker
na een herstart van de app:   de upgrade naar de nieuwe app had het cookie (in elke run van stap 5),
                              en het token is nog geldig (app.security._decode met het app-geheim:
                              sub = de gebruiker)
bewaking in de frames:        geen enkele CSP-schending of fout, ook niet onder /office/
```

**Betekenis.** Zoals in de spike: het cookie gaat mee bij elk verzoek, ook uit de service worker,
via het LAN en bij de upgrade. Nieuw ten opzichte van de spike-proxy: het cookie is nu een getekend
token van de app, en dat overleeft een herstart (de spike-proxy maakte bij elke start een nieuwe
waarde). De controle van het cookie zelf is van O1 (`gate.check` is in O0 een stub).

### Stap 5: de app herstarten terwijl iemand bewerkt

**Wat.** De editor open via de app. "Regel een" en "Regel twee" typen. De app op zijn PID stoppen
(SIGTERM). Tijdens de uitval "Regel drie" typen (alleen toetsen; de focus staat nog in het
document). Na de uitval de app op dezelfde poort weer starten, "Regel vier" typen en sluiten.
Variant A: de DocStub gaat mee uit en weer aan, zoals straks de interne luisteraar (die draait in
hetzelfde proces). Variant B: alleen de app; de DocStub blijft op.

| Config van DS | Variant | Uitval | Statussen tijdens de uitval | `onRequestRefreshFile` | In de opgeslagen docx |
|---|---|---|---|---|---|
| standaard (3 pogingen) | A | 20 s | 1 | ja | niets: status 2 ging na 3 pogingen naar de forgotten files (`commandSfcCallback backoff limit exceeded`, `storeForgotten`) |
| standaard | B | 20 s | 1, 2 | ja | Regel een, Regel twee |
| O6 (8 pogingen) | A | 20 s | 1 | ja | Regel een, Regel twee (status 2 kwam binnen net toen de stub terug was) |
| O6 | A | 26 s | 1 | nee | Regel een, Regel twee, Regel vier (status 1 bij het herverbinden, status 2 5,8 s na het sluiten) |
| O6 | B | 20 s | 1, 2 | ja | Regel een, Regel twee |

In elke run verbond de editor 0,11 tot 0,12 s na de start van de app opnieuw, via de nieuwe
tunnel en met het cookie. Geen `onError` en geen `onWarning`. De events van de editor:
`connected` → `disconnected` → `reconnecting` → `connected`. "Regel drie" staat nergens: tijdens
de uitval neemt de editor niets aan, zoals in de spike.

**Betekenis.**
- De kern doet wat hij moet: na een herstart verbindt de editor meteen opnieuw.
- Maar "niets kwijt en geen `onRequestRefreshFile`" (de spike) geldt alleen als de editor opnieuw
  verbindt **vóórdat** DS zijn status 2 kwijt kan. DS begint 5 s na de laatste verbinding met
  opslaan (`savetimeoutdelay`) en probeert de callback opnieuw na 1, 2, 4, 8, 16 … s (in de log
  van docservice: pogingen op +0, +1, +3, +7, +15 en +31 s, dus 5, 6, 8, 12, 20 en 36 s na de
  breuk). Lukt een poging eerder dan de
  herverbinding, dan is de sessie opgeslagen en dicht, en meldt de editor
  `onRequestRefreshFile`. Wat hij daarna typt, gaat nergens heen. Bij 20 s uitval viel de poging op
  ±20 s samen met de terugkeer. Bij 26 s kwam de editor eerst terug (tussen de pogingen op ±20 en
  ±36 s), en liep de sessie gewoon door. De spike zag alleen dat tweede geval (20 en 64 s). Welk
  van de twee gebeurt, hangt dus van de tijd af, niet van de app (afwijking A21).
- Gevolg: de editorpagina van O2 moet `onRequestRefreshFile` altijd afhandelen (Keuze 32 zegt dat
  al): een nieuwe sessie en `refreshFile`, of opnieuw laden. Wat was aangenomen, staat dan in de
  opgeslagen versie. De test "herstart van de app" (§4.5 punt 4) moet beide uitkomsten goed vinden.
- Met de standaardconfig van DS (3 pogingen, samen ±7 s) gaat de opslag na een uitval van meer dan
  ±12 s naar de forgotten files. Dan is het herstel van Keuze 21 de enige weg terug. De config van
  O6 (`retries: 8`, samen ±255 s) is dus nodig. De job `office` gebruikt hem zodra O6 hem levert
  (`OFFICE_DS_CONFIG` in `e2e/office_ds.sh`; §4.5 stap 2). Tot dan draait de job met de standaard.
- Variant B kan in productie alleen als de callback naar een andere node gaat dan waar de browser
  aan hangt. Daar sluit DS de sessie na 5 s, en komt `onRequestRefreshFile` altijd.

### Stap 11: tijden via de app

```
koud openen (nieuwe context):   docx 4.29, 4.65, 4.75 s; xlsx 4.18, 4.52 s; pptx 4.06 s  (één run, zeven na elkaar)
                                2.78, 3.05, 3.74, 3.79, 3.79, 4.05 s  (de runs van stap 2 en 5, docx)
pptx koud, om de beurt (n=17):  via de app 3.08 – 3.53 s (mediaan 3.25); rechtstreeks bij DS 2.79 – 3.15 s (mediaan 2.96)
warm openen (zelfde context):   2.41, 2.96, 3.17, 3.19 s
sluiten → status 2:             5.82, 5.82, 5.82, 5.82 s
```

**Betekenis.** Openen via de kern van de app duurt 2,8 tot 4,8 s; via de spike-proxy was dat 2,9
tot 4,1 s. Rechtstreeks bij DS is het ±0,3 s sneller (pptx: mediaan 2,96 tegen 3,25 s). De
uitschieters boven de 4,1 s van de proef zaten allemaal in één run, met zeven koude openingen
achter elkaar. Sluiten tot status 2 is gelijk aan de spike: 5,8 s, de wachttijd van DS.

**Nieuw: een pptx opent soms niet via de app (afwijking A22).** In 4 van de 22 koude openingen van
`proef.pptx` via de app kwam de editor niet klaar: geen `onAppReady`, en in de iframe
`Uncaught TypeError: Cannot read properties of undefined (reading 'extend')` in
`web-apps/apps/common/main/lib/component/ColorPaletteExt.js:47`. Rechtstreeks bij DS gebeurde dat
in 0 van 17 keer. Alle verzoeken via de proxy gaven 200. De oorzaak zit in DS:
`ColorPaletteExt.js` is een module `define([], …)` zonder afhankelijkheden, maar gebruikt meteen
`Common.UI.BaseView`. De presentatie-editor laadt die modules los (via `DocumentPreview.js`). Komt
`ColorPaletteExt.js` binnen vóór wat `BaseView` definieert, dan faalt hij. Via de proxy komen de
antwoorden in een andere volgorde binnen (meer wachttijd per verzoek), en dan gebeurt het vaker.
Bij docx en xlsx gebeurde het in geen enkele van de ±25 openingen. Gevolg: O1 kijkt naar de
wachttijd per verzoek in de proxy. De editorpagina van O2 herkent "geen `onAppReady` binnen 20 s",
laadt de iframe één keer opnieuw, en geeft een melding als dat ook niet lukt. De e2e met de echte
DS opent pptx met die herhaling.

---

## Extra bevindingen

1. **De herstart voor de fonts.** In elke **nieuwe** container stopt en start het startscript
   docservice en de converter opnieuw (`supervisorctl restart`), 2:37 tot 2:53 na de start
   (spike-ds 18:36:04 → 18:38:54, ds2 19:21:16 → 19:24:07, ds3 19:32:38 → 19:35:15,
   ds4 19:55:12 → 19:58:05; `logs/ds*-supervisord.log`). DS is daarvoor al gezond. Een
   sessie die dan open is, is weg: `info` geeft daarna `error 1`, `getForgottenList` is leeg, er komt
   geen status 2 of 4, en de editor krijgt na het herverbinden `onRequestRefreshFile`
   (`ds_restart.py`, `logs/dsr-restart.json`). `GENERATE_FONTS=false` (ds3) helpt alleen bij een
   herstart van dezelfde container (dan komt er geen tweede herstart), niet bij een nieuwe. Gevolg:
   de bewering "een nette stop slaat alles op" geldt voor `docker stop`, niet voor deze herstart. De
   app moet een nieuwe DS pas "klaar voor bewerken" noemen als die herstart voorbij is. Hoe de app
   dat ziet, is nog open.
2. **Een OOM van docservice** (stap 18) heeft hetzelfde gevolg: alle open sessies zijn weg. In de
   memory-runtime draait de converter in docservice. Een te groot bestand raakt dus iedereen.
3. **Het versiesegment `<V>`** (`10.0.0-<32 hex>`) is per container anders. Bij ds2 was het
   `ca295fc4…` direct na de start en `86a29d28…` daarna; de enige gebeurtenis ertussen was de
   font-herstart. Een herstart van alleen docservice (supervisorctl, of na de OOM van stap 18) liet
   het staan (spike-ds bleef `15700fb6…`). Een oude `<V>` werkt nog: de nginx van DS haalt het
   segment er gewoon af (een tab met de oude `<V>` verbond na de font-herstart opnieuw, 101).
   De proxy mag `<V>` dus nooit vastpinnen, alleen de vorm controleren.
4. **Stuur altijd `?shardkey=<key>` mee**, ook op `/converter`. Zonder werkt de conversie, maar
   in de spike gaf DS toen soms de `shardkey` van een **ander** verzoek terug in `fileUrl`
   (`shardkey=s14a-…` bij conversies met key `cv-…`, `logs/conv_tests.json`). Met `?shardkey=<key>`
   draagt `fileUrl` de juiste key. De download werkte in beide gevallen.
5. **`downloadas` draagt een sessietoken in de URL**: `cmd={"c":"save","id":…,"userid":…,
   "tokenSession":"<sessietoken>","outputformat":…,"title":…,…}`. `SECRET_PARAMS` moet `cmd`
   erbij krijgen, anders staat dat token in de log van de app.
6. **De callbacks dragen URL's van de browserkant**: `http://<host van de browser>/office/cache/
   files/data/<key>_<n>/output.docx/…?md5=…&expires=…&shardkey=…` (uit `X-Forwarded-*`). De app
   neemt alleen pad en query, haalt `/office` eraf en gaat naar `office_url` (zoals §3.9 #10 en
   `strip_prefix` al zeggen). `getForgotten` via `/command` geeft de host van DS zelf.
7. **DS logt opslaglinks.** Bij elke mislukte callback logt docservice de hele body, met `url` en
   `changesurl` (met `md5`), op niveau ERROR. Het volume met de logs van DS is dus gevoelig tot de
   links verlopen.
8. **Status 1 komt vaak.** Bij elke (her)verbinding stuurt DS een callback met status 1 en de
   huidige gebruikers. Status 1 is dus geen "nieuwe sessie".
9. **De callback zelf.** Body: `key`, `status`, `url`, `changesurl`, `history` (`serverVersion`,
   `changes` met `documentSha256`, `created`, `user`), `users`, `actions`, `lastsave`, `notmodified`,
   `filetype` en `token` (dezelfde velden plat, met `operation: callback`, 5 min). Header
   `Authorization: Bearer` met de velden onder `payload`. Zie `recorded/callbacks.json`.
10. **Een kapotte docx** (geen zip) zette DS zonder fout om naar een PDF van 640 KB (als tekst). De
    invoercontrole van de app (Keuze 23) moet zo'n bestand dus zelf weigeren.
11. **De font-herstart zien** (open punt 4, voor CI opgelost). `e2e/office_ds.sh wait` wacht tot
    `/healthcheck` `true` is en `supervisord.log` in de container `stopped: converter` noemt. In de
    runs van O0-e2e kwam dat 165 s en 713 s (een drukke sandbox) na de start. Voor de app zelf is
    het nog open.

---

## §3.10 definitief: padbeleid van `/office/`

Na normaliseren (prefix `/office` eraf, versiesegment `^\d+\.\d+\.\d+-[0-9a-f]{8,64}$` eraf).
"Gemeten" = gezien in de runs van stap 1; "niet gemeten" = afgeleid, nog niet gezien.

| Klasse | Paden | Methoden | Toegang | Bron |
|---|---|---|---|---|
| statisch | `web-apps/…`, `sdkjs/…`, `sdkjs-plugins/…`, `fonts/…`, `dictionaries/…`: alles behalve HTML (gezien: .js, .css, .json, .template, .svg, .png, .wasm, .bin, .aff, .dic, fonts zonder extensie); `document_editor_service_worker.js`; `web-apps/apps/api/documents/api.js` (ook zonder `<V>`) | GET, HEAD | geen | gemeten |
| editorpagina | precies `web-apps/apps/{documenteditor,spreadsheeteditor,presentationeditor}/main/index.html`; daarbij `…/mobile/index.html` voor `type: "mobile"` | GET, HEAD | cookie; `Sec-Fetch-Dest` `iframe`, `frame` of `empty` als de kop er is; volledige CSP | `main` gemeten; `mobile` niet gemeten (api.js maakt dat pad) |
| documentdata | `cache/files/data/<key>_<n>/…` (output van downloaden en afdrukken) **en** `cache/files/data/<key>/…` (`Editor.bin/Editor.bin`, `media/<bestand>/<bestand>`) | GET | cookie + lid + recht van nu | gemeten |
| sessie | `doc/<key>/c/…` | upgrade (tunnel); GET en POST alleen voor polling | cookie + lid + recht van nu; tunnel: ook de pakketten (stap 12) | upgrade gemeten; polling niet gezien |
| editorinstellingen | `themes.json`; `plugins.json` | GET | cookie + lid van een open sessie | `themes.json` gemeten; `plugins.json` niet gevraagd met `plugins: false` |
| editoracties | `downloadas/<key>` (downloaden en afdrukken; query `cmd` en `shardkey`; body tot ±250 KB); `upload/<key>` (afbeelding; `Authorization: Bearer <sessietoken>` door naar DS) | POST | cookie + lid + recht van nu; grens per gebruiker | gemeten |
| geweigerd (404) | al het andere, met name: `/`, `index.html`, `<V>/index.html`, `welcome/…`, `web-apps/apps/api/documents/preload.html` en `cache-scripts.html`, `printfile/…`, `savefile/…`, `downloadfile/…`, `command`, `coauthoring/…`, `converter`, `converter/from-file`, `ConvertService.ashx`, `docbuilder`, `baseurl`, `dummyCallback`, `info/…`, `internal/…`, `hosting/…`, `example/…`, `meta/…`, `healthcheck`, `version`, `ai-proxy`, `robots.txt`, `cache/files/forgotten/…`, `cache/files/data/conv_…`, `favicon.ico` | — | — | gemeten (DS en proxy) |

Toelichting:
- `printfile`, `savefile` en `downloadfile` bestaan in DS, maar de editor gebruikte ze in geen
  enkele actie. Ze blijven dicht; vraagt de integratie er toch om, dan beslist zij met een notitie.
- `Authorization` gaat alleen op `upload/<key>` door naar DS, en alleen als het een geldig
  sessietoken is (afgeleide sleutel) met `document.key` = de key van het pad. Op alle andere paden
  blijft het eruit.
- `cmd` en `shardkey` gaan nooit in een log (`SECRET_PARAMS`).

---

## Afwijkingen van het ontwerp

| # | Ontwerp zegt | De spike zag | Wat er moet veranderen |
|---|---|---|---|
| A1 | §3.9 #6: "elk JWT met het office-geheim" | het sessietoken is getekend met `HMAC-SHA256(office-geheim, "editor-session").hex` | `oo/stream.py` en `tokens.py`: het sessietoken met de afgeleide sleutel controleren |
| A2 | Keuze 9/§3.9: "DS ziet nooit `Authorization`" | `upload/<key>` geeft zonder `Authorization` 403 ("Het token voor de documentbeveiliging heeft niet de juiste indeling") | op `upload/<key>` alleen een geldig sessietoken doorgeven |
| A3 | §3.10: documentdata is `cache/files/data/<key>_<n>/…` | ook `cache/files/data/<key>/Editor.bin/…` en `…/<key>/media/…` | regel `(_\d+)?` |
| A4 | §3.10: editoracties "verwacht `printfile/<key>/…`" | afdrukken gaat via `downloadas/<key>` en `output.pdf`; `printfile` nooit gezien | `printfile` dicht |
| A5 | §3.10: editorpagina = "elke `*.html`, en een 302 die daarheen gaat" | alleen de drie `…/main/index.html`; geen 302; `/<V>/index.html` is een statuspagina van DS | vaste lijst; de rest 404 |
| A6 | Keuze 12: `SECRET_PARAMS` krijgt `md5` en `shardkey` | `downloadas?cmd=` bevat een sessietoken | ook `cmd` |
| A7 | Keuze 9: `'unsafe-eval'` gaat eruit als het zonder kan | zonder start de editor niet | `'unsafe-eval'` blijft |
| A8 | O2: status 3 (herstel uit `url`) | status 3 heeft geen `url` en geen herstelbestand | status 3 = dicht als verloren; nep-DS idem |
| A9 | het merk: `lastsave` hoger | `lastsave` heeft hele seconden; `history.changes` niet cumulatief | aannemen bij `>=`, gelijk merk → de sha beslist |
| A10 | M15: DS vernieuwt het sessietoken bij een levende verbinding | niet gezien in 5 min met `"2m"`; herverbinden na verloop: 4005 | geen `"2h"`; standaard blijft |
| A11 | Keuze 23 en O6: 50 MB XML voor docx, pptx, odt, odp | bij 2 GB: odt 21 MB XML → x2t OOM; docx 42 MB XML → docservice OOM, alle sessies weg; later gemeten: x2t OOM bij docx 10 MB, odt 7 MB, xlsx 30 MB | grens omlaag: 4 MB (OOXML-tekst en -presentaties), 2 MB (ODF), 10 MB (OOXML-werkblad), naar de inhoud (stap 18 verder) |
| A12 | Keuze 23: conversie wacht op een sessie; "openen gebruikt de converter waarschijnlijk ook" | bevestigd, en omgekeerd: openen wacht op een lopende conversie (24 s); `maxprocesscount` helpt niet | blijft; melding bij lang openen |
| A13 | §0.1, §5.1: "een nette stop slaat alles op" | de font-herstart van een nieuwe container (2:37–2:52) gooit open sessies weg zonder callback | pas bewerken als die herstart voorbij is |
| A14 | §3.10: `<V>` als vaste vorm | `<V>` is per container anders en wisselt bij de font-herstart | nooit vastpinnen; alleen de vorm |
| A15 | O2-config: `close.text` uit `user.locale` | DS kent zelf een tekst per `lang` | `text` weglaten |
| A16 | O2-config | "Functie voorstellen" en "Kopie opslaan als" staan standaard in het menu | `suggestFeature: false`; geen `onRequestSaveAs` |
| A17 | O4: lege docx zonder settings | download als docx geeft een melding over het nieuwe formaat | `word/settings.xml` met `compatibilityMode` 15 |
| A18 | §3.9 #12: `POST /converter` | zonder `?shardkey=` gaf DS soms de `shardkey` van een ander verzoek terug in `fileUrl` | ook op `/converter` altijd `?shardkey=<key>` (zoals #11 al doet) |
| A19 | — | `drop` op een onbekende key geeft ook `{"error": 0}` | het antwoord van `drop` zegt niets; de app telt zelf |
| A20 | §3.10: upgrade met `Sec-Fetch-Dest` | de upgrade heeft nooit `Sec-Fetch-Dest` | tunnel zonder die kop toestaan (`Origin` en cookie) |
| A21 | Spike stap 5: na een herstart van de app geen `onRequestRefreshFile`, niets kwijt | via de app: kan DS zijn status 2 kwijt vóór de herverbinding, dan is de sessie dicht en komt `onRequestRefreshFile`; met de standaardconfig (3 pogingen) na ±12 s uitval een forgotten file | O2: `onRequestRefreshFile` altijd afhandelen, en §4.5 punt 4 vindt beide uitkomsten goed; O6: `retries: 8` in de config, en de job `office` gebruikt die (`OFFICE_DS_CONFIG`) |
| A22 | Spike stap 11: via de proxy niet trager | ±0,3 s trager dan DS zelf; een pptx opent 4 van 22 keer niet (`ColorPaletteExt.js`: `define([])` zonder afhankelijkheid op `BaseView`); rechtstreeks 0 van 17 | O1: de wachttijd per verzoek in de proxy bekijken; O2: geen `onAppReady` binnen 20 s → de iframe één keer opnieuw laden, daarna een melding |

---

## Open punten

1. Neemt DS een herverbinding aan als het config-token (`jwtOpen`, `exp` 2 uur) verlopen is en het
   sessietoken nog geldig? Niet getest. Het zit in elke herverbinding. Belangrijk voor wie langer
   dan 2 uur bewerkt.
2. Neemt DS een CONNECT met het token van key A aan, gevolgd door een `auth`-bericht met het token
   van key B? Niet getest. De wacht eist daarom bij elk `auth`-bericht dezelfde key.
3. De XML-grens voor 2 GB is gemeten (stap 18 verder): 4, 2 en 10 MB. Open blijft een klein bestand
   met heel veel pagina's (3,9 MB pagina-einden: ±97 % van het geheugen), en odp en echte
   werkbladen met veel opmaak. De integratie meet opnieuw (§4.7).
4. Hoe de app ziet dat de font-herstart van een nieuwe DS voorbij is. Voorstel om te meten: wachten
   tot de `<V>` in `api.js` is gewisseld, of tot de container ±3 minuten draait en docservice één
   keer is herstart. Voor CI is het opgelost met de log van supervisord (extra bevinding 11); de
   app kan die log niet lezen.
5. `type: "mobile"` (`…/mobile/index.html`) is niet geopend. Paden en `Sec-Fetch-Dest` daarvan zijn
   niet gemeten.
6. Stap 15: status 2 kwam niet binnen door het harnas (404 op de download van DS2). Niet opnieuw
   gedaan.
7. Afdrukken via het menu in xlsx en pptx lukte in het script niet (klik hing); via de knop in de
   kopbalk wel. De paden zijn gelijk; het menu zelf is niet bewezen.
8. A22: hoe vaak een pptx niet opent via de app als O1 de proxy af heeft, en of één keer opnieuw
   laden altijd genoeg is. Niet gemeten.
9. A21 met de echte interne luisteraar (O2): de herhaling van stap 5 deed de interne kant met de
   DocStub van het harnas. Dat is hetzelfde gedrag voor DS (verbinding geweigerd), maar niet de
   echte callback van de app.

---

## Bestanden

Opnames, in `api/tests/fake_docserver/recorded/` (tokens vervangen door `<JWT>`, `md5` door `…`;
geen geheimen). De nep-DS en `tests/test_fake_docserver.py` gebruiken ze:
- `socket.json`: de eerste frames van een eerste verbinding en van een herverbinding, met
  gedecodeerde claims en de controle van de afgeleide sleutel (stap 12);
- `callbacks.json`: callbacks 1 (openen, sluiten, `info`), 6 (knop en commando), 2, 4 (ook na
  `drop`) en 3 (stap 10), elk met body, de claims van het body- en het headertoken en de koppen;
  plus de volgorde van één sessie;
- `download.json`: de GET van DS naar de interne luisteraar (koppen, claims, `Want-Repr-Digest`);
- `commands.json`: antwoorden van `/command` (version, license, info, forcesave, drop en meta; ook
  op een open key; zonder token, met het token in de header of in de body, met een fout geheim;
  getForgottenList, getForgotten en deleteForgotten voor O2) en de callback na een forcesave;
- `convert.json`: antwoorden van `/converter` (gewoon, digest goed en fout, grens -10, zonder token
  -8, token in de header, bron 404 → -4, async met polling), voor O3;
- `paths.json`: de padinventaris van stap 1, 2 en 17, voor O1.

De scripts van de spike (`proxy.py`, `stub.py`, `actions.py`, `analyze.py`, `reconnect.py`,
`save_tests.py`, `status3.py`, `step*.py`, `conv_tests.py` en andere) en de ruwe logs stonden in een
tijdelijke werkmap en zijn niet in de repo gekomen. De herhaling van O0-e2e gebruikte alleen het
harnas van de e2e (`e2e/office_harness.py`), een opnemende relay en `e2e/office_ds.sh`.
