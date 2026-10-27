# Collabora als terugval (Keuze 3)

Fase 4 gebruikt OnlyOffice Docs (Vraag 1, besloten door Jonas). Collabora Online (CODE) blijft de
terugval, maar alleen **klaargezet**: de naad `app/office/engine.py`, de motor-neutrale
contracttests (§4.4) en dit document. Er is in fase 4 geen WOPI-code (Vraag 5). Omschakelen kost
naar schatting **±1 week** (onderaan).

De bronnen staan erbij zoals in het bouwontwerp: **[doc]** komt uit de documentatie van
Collabora of WOPI, **[gemeten]** uit het onderzoek van fase 4 met de echte CODE. In O6 is niets
opnieuw gemeten.

## Wat we weten

- **CODE noemt zichzelf "not recommended for production"** [doc, gemeten]. Voor echt gebruik
  hoort er een abonnement bij; CODE is de ontwikkelversie.
- **Een welkomstvenster bij elke start, dat niet uit kan** [gemeten]. Elke e2e moet het eerst
  sluiten, net als een gebruiker. De nep-Collabora van de contracttests doet hetzelfde.
- **Collabora spreekt alleen WOPI** [doc]. De editor haalt het bestand bij de app (GetFile) en
  zet het terug (PutFile); er zijn geen callbacks met statussen zoals bij OnlyOffice.
- **Een conversie-eindpunt zonder authenticatie: `/cool/convert-to`** [doc, gemeten]. Wie het
  bereikt, kan elk bestand laten omzetten. Het mag dus **nooit** van buiten bereikbaar zijn,
  net als **`/cool/adminws`** (de beheer-websocket: sessies zien en sluiten). De app roept
  `convert-to` alleen zelf aan, over het interne netwerk.
- **De proxy houdt het prefix** [doc]. Collabora draait met `net.service_root=/office` en
  verwacht dus `/office/...` binnen te krijgen. Voor OnlyOffice haalt de proxy `/office` er af;
  daarvoor is `Engine.strip_prefix` (OnlyOffice: ja, Collabora: nee).
- **TLS eindigt bij de app**: `ssl.enable=false` en `ssl.termination=true` [doc]. Collabora maakt
  dan zelf `https://`-adressen, terwijl hij intern http praat.
- **`net.proto=IPv4`** [doc, gemeten]: zonder deze instelling probeert CODE ook IPv6 en logt hij
  fouten in een container zonder IPv6.
- **`aliasgroup1`** [doc]: het publieke adres van de app (`https://<domein>:443`). Alleen een
  WOPI-host uit die lijst mag de editor gebruiken. Een LAN-adres naast het domein is een tweede
  waarde in dezelfde groep.

## Wat een WOPI-host moet kunnen

Alles onder één prefix, bijvoorbeeld `/internal/wopi/files/<id>` op de interne luisteraar (zoals
`/internal/office/` nu), met `access_token` in de query [doc]:

| Verzoek | Wat de app doet |
|---|---|
| **CheckFileInfo**: `GET /wopi/files/<id>` | JSON met `BaseFileName`, `Size`, `Version` (onze sha), `OwnerId`, `UserId`, `UserFriendlyName`, `UserCanWrite`, `LastModifiedTime` en de knoppen die we willen (`DisablePrint`, `HidePrintOption`, …). Hier controleert de app ook de toegang (de rechten van Keuze 22, via `app/access.py`) |
| **GetFile**: `GET /wopi/files/<id>/contents` | de bytes, alleen na `inspect` (Keuze 23), net als nu bij `/internal/office/doc` |
| **PutFile**: `POST /wopi/files/<id>/contents` met `X-WOPI-Override: PUT` | de opslag. Met de kop **`X-COOL-WOPI-Timestamp`** zegt Collabora welke `LastModifiedTime` hij kende. Is het bestand intussen veranderd: **409** met `{"COOLStatusCode": 1010}`; dan vraagt Collabora de gebruiker wat er moet gebeuren [doc]. `X-COOL-WOPI-IsAutosave` en `X-COOL-WOPI-IsExitSave` zeggen of het een tussenstand of de laatste opslag is [doc] |
| **Locks** (`LOCK`, `UNLOCK`, `REFRESH_LOCK`, `GET_LOCK`) | alleen als CheckFileInfo `SupportsLocks: true` zegt. Collabora werkt zonder [doc]; OnlyOffice via WOPI eist ze wel. Voorstel: zonder locks, want Collabora regelt samen bewerken zelf per document |

Daarnaast:

- **`access_token` en `access_token_ttl`** gaan met een formulier (POST) naar de editor-URL uit
  `/hosting/discovery` [doc]. Het token is van ons: een getekend token met de key van de sessie
  en de gebruiker, net als de grants nu (Keuze 13).
- **Proof keys** (`X-WOPI-Proof`): Collabora tekent elk verzoek met de sleutel uit
  `/hosting/discovery` [doc]. Controleren is een extra laag naast het token; voorstel: ja, het
  is één functie.
- **Discovery**: `GET /hosting/discovery` geeft per extensie en actie de `urlsrc` van de editor,
  en de sleutel voor de proof keys [doc]. `GET /hosting/capabilities` geeft de versie.

## Wat een Collabora-adapter vervangt

De kern blijft: `sessions.py`, `saving.py` (het recept van Keuze 16 en de versies van Keuze 17),
`policy.py`, `sweep.py`, `gate.py`, `proxy.py`, `tunnel.py`, `inspect.py`, `convert.py` (de
taak), `monitor.py`, `keys.py`, `grants.py` en de routers voor openen, nieuw en beheer. Wat
OnlyOffice-specifiek is, staat in `app/office/oo/`; een tweede map `app/office/cool/` krijgt
dezelfde rol. Per methode van `Engine`:

| `Engine` | OnlyOffice (`oo/`) | Collabora (`cool/`) |
|---|---|---|
| `classify`, `session_key` | `oo/paths.py`: het padbeleid van `/office/` | `cool/paths.py`: `/office/browser/<hash>/cool.html`, de websocket `/office/cool/<WOPISrc>/ws`, statische bestanden; altijd geweigerd: `/cool/convert-to`, `/cool/adminws`, `/browser/dist/admin/` |
| `stream_guard` | `oo/stream.py`: het eerste socketpakket, en daarna elk nieuw `auth`-bericht (o1.md, afwijking 18) | het eerste bericht op de websocket noemt de WOPISrc; die moet bij de key van de sessie horen. De wacht krijgt de hele stroom: kan een later bericht een ander document openen, dan controleert hij dat ook |
| `action_authorization` | uploads van de editor (`upload/<key>`) | geen: alles gaat via WOPI |
| `editor_config` | `oo/editor.py`: de getekende config | de `urlsrc` uit discovery plus `access_token` en `access_token_ttl` (een formulier, geen JWT) |
| `read_callback`, `callback_reply` | de callbacks van DS | geen callbacks: een nieuwe router `routers/wopi.py` roept bij PutFile het recept van `saving.py` aan. `callback_reply` wordt de vertaling naar HTTP: 200, 409 met 1010, 413 |
| `check_download` | het download-token van DS | het `access_token` bij GetFile |
| `health`, `enforces_tokens` | `/healthcheck`, `version`, en de controle dat DS tokens eist | `/hosting/discovery` en `/hosting/capabilities`. In plaats van "eist tokens": de controle dat `convert-to` van buiten niet bereikbaar is |
| `present` | het commando `info` | geen equivalent buiten `adminws`. De app telt zelf: open websockets in de tunnel en CheckFileInfo per gebruiker |
| `checkpoint` | `forcesave` | geen commando; Collabora slaat zelf tussentijds op (`IsAutosave`). Tussentijds opslaan bij een update wordt: wachten op de volgende PutFile, met een grens |
| `drop` | het commando `drop` | geen commando; het token intrekken, zodat de volgende WOPI-aanroep 401 krijgt, en de websocket in de tunnel sluiten |
| `convert` | `oo/convert.py`: `/converter` | `/cool/convert-to`, alleen over het interne netwerk |
| `forgotten`, `fetch_forgotten`, `delete_forgotten` | de forgotten files van DS | Collabora kent ze niet: een lege lijst. Een mislukte PutFile blijft in de editor staan tot hij lukt of de gebruiker sluit |

Ook: `oo/tokens.py` (de JWT-vormen van OnlyOffice) en `oo/admin.py` vallen weg; `oo/client.py`
blijft de enige weg naar de editor, met een ander adres. De contracttests krijgen een tweede
`Driver` (een nep-Collabora), met dezelfde scenario's.

## De installatie

Zoals `compose.office.yaml` nu, met dezelfde afscherming (geen poort, intern netwerk,
`cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`). CODE vraagt wel andere capabilities
(onder meer `SYS_CHROOT` en `MKNOD` voor zijn jails, of `--o:mount_jail_tree=false`) [doc]; dat
moet in de sandbox gemeten worden vóór het in compose komt. De instellingen:

```
aliasgroup1=https://<domein>:443
extra_params=--o:ssl.enable=false --o:ssl.termination=true --o:net.service_root=/office --o:net.proto=IPv4
```

## Schatting: ±1 week

| Werk | Dagen |
|---|---|
| WOPI-routes (CheckFileInfo, GetFile, PutFile met 409/1010), tokens en proof keys | 1,5 |
| Padbeleid, de websocket in de tunnel, de proxy met prefix | 1 |
| De editorpagina: formulier naar `urlsrc`, het welkomstvenster | 0,5 |
| Conversie via `convert-to` (alleen intern) | 0,5 |
| Nep-Collabora en de tweede `Driver` voor de contracttests | 1 |
| De echte CODE in de sandbox: e2e, capabilities meten, compose | 1 |

De risico's: CODE is niet bedoeld voor productie; zonder forcesave en zonder `info` zijn de
versies (Keuze 17) en het tellen van verbindingen (O5) minder precies dan nu.
