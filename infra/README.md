# infra

Infrastructuur voor de 3 nodes (fase 1). Ansible zet de VM's en k3s neer; daarna
rolt Argo CD alles onder `k8s/` uit vanuit git. Alles is van buiten bereikbaar via
de webinterface over HTTPS; er is geen VPN.

```
ansible/            VM-basis, hardening, ZFS-datadisk, k3s + kube-vip, Argo CD
k8s/apps/           Argo CD-applicaties (cert-manager, CloudNativePG, SeaweedFS, monitoring)
k8s/platform/       eigen chart: TLS-uitgevers, PostgreSQL-cluster, ingresses, beveiliging, alerts
scripts/            hardware-inventaris en CI-controles
```

## Wat er per laag draait

| Laag | Onderdeel | Replicatie |
|------|-----------|------------|
| Edge | Traefik op elke node, VIP via kube-vip | 3 replica's; VIP springt binnen seconden over |
| TLS | cert-manager met Let's Encrypt (HTTP-01) | |
| Database | CloudNativePG: 1 primary + 2 standbys, synchroon ANY 1, PgBouncer | RPO 0, automatische failover |
| Objectopslag | SeaweedFS: 3 masters, 3 volume servers, 3 filers, S3-gateway | replicatie 001 (2 kopieën) |
| Monitoring | Prometheus, Alertmanager, Grafana, Loki, Alloy | |
| Snapshots | ZFS + sanoid op elke node: elk uur, 24 u / 7 d / 4 w | |

Nog niet in deze stap (fase 1 deel 2): offsite-backups (restic, barman), Redis Sentinel,
Typesense en de failover-test zelf.

## Uitrollen

Wat je zelf moet doen of beslissen staat vetgedrukt.

1. **Maak 3 VM's** (Ubuntu 24.04, één per Proxmox-host) met een OS-disk en de 700 GB datadisk,
   een vast IP in hetzelfde subnet en een gebruiker `beheer` met jouw SSH-sleutel en sudo.
2. **Kies een vrij IP als VIP** en **zet in je router poort 80 en 443 (TCP, en 443 UDP voor HTTP/3)
   door naar dat VIP.** Stuur nooit 22 of 6443 door.
3. **Maak DNS-records** (A naar je publieke IP) voor `argocd.<domein>` en `grafana.<domein>`,
   later ook `app.<domein>`.
4. Vul de instellingen in:
   ```bash
   cd infra/ansible
   cp inventory/hosts.example.yml inventory/hosts.yml   # IP's van de nodes
   $EDITOR group_vars/all.yml                            # VIP, subnet, datadisk, interface
   # clustergeheim versleuteld opslaan:
   echo "k3s_token: $(openssl rand -hex 32)" > group_vars/all.local.yml
   ansible-vault encrypt group_vars/all.local.yml
   ```
   `data_disk` is het `/dev/disk/by-id/...`-pad van de 700 GB-disk (`ls -l /dev/disk/by-id/` op de VM).
   **Alles op die disk wordt gewist.**
5. Zet je domein en e-mailadres in `infra/k8s/platform/values.yaml` en in
   `grafana.ini.server.root_url` in `infra/k8s/apps/kube-prometheus-stack.yaml`, commit en merge naar `main`.
6. Rol uit:
   ```bash
   pip install ansible
   ansible-playbook site.yml -e @group_vars/all.local.yml --ask-vault-pass
   ```
   Daarna synchroniseert Argo CD de rest uit git; dat duurt een paar minuten.
7. Inloggen via de browser:
   - Argo CD op `https://argocd.<domein>`, gebruiker `admin`, wachtwoord:
     `sudo k3s kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`
   - Grafana op `https://grafana.<domein>`, gebruiker `admin`, wachtwoord:
     `sudo k3s kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d`
   Wijzig beide wachtwoorden na de eerste login.
8. Werkt alles met het staging-certificaat (browser waarschuwt nog), zet dan
   `acme.issuer: letsencrypt-prod` in `infra/k8s/platform/values.yaml`.

Is het repository privé, voeg het dan in Argo CD toe onder *Settings → Repositories*
(met een read-only deploy key) voordat Argo CD kan synchroniseren.

## Lokaal controleren

```bash
yamllint -s .
cd infra/ansible && ansible-lint site.yml
python3 infra/scripts/valideer-k8s.py /tmp/manifests   # vereist helm
```
