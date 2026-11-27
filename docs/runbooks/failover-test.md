# Runbook: failover-test (fase 1, mijlpaal)

Doel uit het stappenplan: node 1 hard uitzetten geeft 0 verloren commits en 0 verloren
objecten, en de service is binnen 2 minuten terug.

## Voorbereiding

1. `https://app.<domein>/beheer` toont "Alles in orde".
2. Zoek de huidige PostgreSQL-primary (staat ook op de beheerpagina):
   `docker compose -f /opt/platform/compose.yaml exec postgres patronictl -c /etc/patroni/patroni.yml list`
3. Start een schrijver die elke seconde een rij en een object schrijft en de tijd logt
   (script volgt in fase 1 deel 2, `infra/scripts/failover-schrijver.py`).

## Test

1. Zet in Proxmox de container van de primary **hard uit** (Stop, niet Shutdown). Noteer de tijd.
2. Meet:
   - promotietijd: tot de beheerpagina een nieuwe primary toont (of `patronictl list` op een andere node)
   - VIP: `ping <vip>` en `curl -sk https://<vip>/` vanaf een ander apparaat
   - S3-writes: blijft de schrijver objecten kwijt kunnen?
3. Controleer na afloop: het aantal rijen en objecten is gelijk aan wat de schrijver
   als bevestigd heeft gelogd (0 verloren).

## Herstel

1. Zet de container weer aan.
2. Patroni zet de oude primary terug als standby (pg_rewind); controleer met `patronictl list`.
3. SeaweedFS: vul ontbrekende kopieën aan met
   `echo "lock; volume.fix.replication; unlock" | docker compose -f /opt/platform/compose.yaml exec -T seaweed-master weed shell`.
4. Leg de gemeten tijden vast in dit runbook.

## Resultaten

| Datum | Promotietijd | VIP-overname | Verloren commits | Verloren objecten | Opmerking |
|-------|--------------|--------------|------------------|-------------------|-----------|
| | | | | | |
