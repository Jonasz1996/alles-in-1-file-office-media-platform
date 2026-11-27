"""Live tv (IPTV): de kanalen uit een m3u, de gids uit XMLTV en de live stroom via de worker.

- m3u.py: de kanaallijst lezen (tvg-id, tvg-name, tvg-logo, tvg-chno, group-title);
- xmltv.py: de gids lezen (channel id = tvg-id van de m3u);
- conf.py: de instellingen (tabel setting, rij `iptv_settings`; de m3u versleuteld);
- refresh.py: lijst en gids ophalen en opslaan (taak `iptv.refresh`, elke paar uur vanzelf);
- upstream.py: de stroom van de aanbieder lezen (MPEG-TS, of HLS met TS-stukken);
- fmp4.py: de uitvoer van de worker in init en fragmenten knippen, en de codecs eruit lezen;
- tuner.py: één tuner per kanaal, gedeeld door wie kijkt, met een grens op het aantal;
- logos.py: de logo's via de app (ondertekend adres, met cache).

De logins van de aanbieder (in de m3u-URL en in de stroomadressen) staan alleen versleuteld in
de database, gaan nooit naar een client, nooit in een log en nooit naar de worker.
"""
