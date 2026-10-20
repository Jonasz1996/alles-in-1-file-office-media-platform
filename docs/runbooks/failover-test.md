# Runbook: failover-test (fase 1, mijlpaal)

Doel uit het stappenplan: node 1 hard uitzetten geeft 0 verloren commits en 0 verloren
objecten, en de service is binnen 2 minuten terug.

## Voorbereiding

1. Alles groen in Argo CD en Grafana; geen actieve alerts.
2. Zoek de huidige PostgreSQL-primary:
   `kubectl -n database get cluster pg -o jsonpath='{.status.currentPrimary}'`
3. Start een schrijver die elke seconde een rij en een object schrijft en de tijd logt
   (script volgt in fase 1 deel 2, `infra/scripts/failover-schrijver.py`).

## Test

1. Zet in Proxmox de VM van de primary **hard uit** (Stop, niet Shutdown). Noteer de tijd.
2. Meet:
   - promotietijd: `kubectl -n database get cluster pg -w` tot `currentPrimary` wisselt
   - VIP: `ping <vip>` en `curl -sk https://<vip>/` vanaf een ander apparaat
   - S3-writes: blijft de schrijver objecten kwijt kunnen?
3. Controleer na afloop: het aantal rijen en objecten is gelijk aan wat de schrijver
   als bevestigd heeft gelogd (0 verloren).

## Herstel

1. Zet de VM weer aan.
2. CloudNativePG zet de oude primary terug als standby (pg_rewind); controleer met
   `kubectl -n database get pods -l cnpg.io/cluster=pg`.
3. SeaweedFS: vul ontbrekende kopieën aan met
   `kubectl -n storage exec seaweedfs-master-0 -- sh -c 'echo "lock; volume.fix.replication; unlock" | weed shell'`.
4. Leg de gemeten tijden vast in dit runbook.

## Resultaten

| Datum | Promotietijd | VIP-overname | Verloren commits | Verloren objecten | Opmerking |
|-------|--------------|--------------|------------------|-------------------|-----------|
| | | | | | |
