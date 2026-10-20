# infra

Fase 1: drie unprivileged LXC-containers op Proxmox, elk met Docker Compose, uitgerold
met Ansible. Alles is van buiten bereikbaar via de webinterface over HTTPS; er is geen VPN.

## Wat er per node draait

Elke node draait hetzelfde (`ansible/roles/platform/templates/compose.yaml.j2`):

| Dienst | Rol | Failover |
|--------|-----|----------|
| etcd | stemt af wie de database-primary is | meerderheid van 3 |
| PostgreSQL 17 + Patroni | 1 primary, 2 standbys, synchroon | automatisch, 0 verloren commits |
| SeaweedFS master, volume, filer + S3 | bestanden, 2 kopieën op verschillende nodes | blijft werken met 2 van 3 nodes |
| Traefik | HTTPS, Let's Encrypt, HTTP/3, security-headers, rate limiting | op elke node |
| keepalived (op de container zelf) | houdt de VIP vast | VIP springt binnen seconden over |
| platform-api en platform-web | het platform zelf, met beheer op `/beheer` | Traefik slaat uitgevallen nodes over |

Hergebruikt wordt alleen wat we niet zelf kunnen winnen; beheer en monitoring zitten in de
eigen web-app (zie `docs/fase-0/beslissingen.md`, B14).

## 1. Proxmox (via de webinterface)

Per Proxmox-host één container, **Create CT**:

- Template: Ubuntu 24.04, **Unprivileged container aangevinkt**
- Cores 4, Memory 16384 MB, **Swap 0**
- Root disk 64 GB op je ZFS-opslag
- Netwerk: vast IP, alle drie in hetzelfde subnet; houd één extra vrij IP apart voor de VIP
- Na het aanmaken:
  - **Options → Features:** `nesting` en `keyctl` aan (nodig voor Docker)
  - **Resources → Add → Mount Point:** 700 GB op je ZFS-opslag, pad `/tank`, *Backup* uit,
    *Skip replication* aan (het platform repliceert zelf)
  - **Options → Start at boot:** aan
- In de container: maak een gebruiker `beheer` met jouw SSH-sleutel en sudo.

Proxmox 8.1 of nieuwer is nodig, zodat Docker zijn snelle opslagdriver (overlay2) op ZFS kan gebruiken.

**Firewall** (Datacenter → Firewall aan, en per container → Firewall):

| Richting | Bron | Poort | Waarom |
|----------|------|-------|--------|
| IN accept | overal | TCP 80, TCP 443, UDP 443 | het platform |
| IN accept | subnet van de nodes | alles | onderlinge replicatie |
| IN accept | jouw beheer-pc of LAN | TCP 22 | Ansible |
| IN accept | subnet van de nodes | VRRP (protocol 112) | keepalived |
| Input policy | | DROP | |

**Router:** stuur alleen TCP 80, TCP 443 en UDP 443 door naar het VIP. **DNS:** een A-record
`app.<domein>` naar je publieke IP.

**Snapshots** (elk uur, bewaar 24 uur, 7 dagen, 4 weken): in de shell van elke Proxmox-host,
met de dataset van het mount point (`zfs list | grep subvol-<CT-ID>`):

```bash
apt install sanoid
cat >> /etc/sanoid/sanoid.conf <<'SAN'
[rpool/data/subvol-101-disk-1]
    use_template = platform

[template_platform]
    hourly = 24
    daily = 7
    weekly = 4
    monthly = 0
    autosnap = yes
    autoprune = yes
SAN
systemctl enable --now sanoid.timer
```

## 2. Instellingen

```bash
cd infra/ansible
cp inventory/hosts.example.yml inventory/hosts.yml   # IP's van de 3 containers
$EDITOR group_vars/all.yml                            # VIP, subnet, domein, e-mail

# geheimen genereren en versleutelen
for k in postgres_superuser_password postgres_replication_password postgres_rewind_password \
         seaweedfs_s3_access_key seaweedfs_s3_secret_key keepalived_auth_pass platform_admin_token; do
  echo "$k: $(openssl rand -hex 24)"
done > group_vars/all.local.yml
ansible-vault encrypt group_vars/all.local.yml
```

## 3. Uitrollen

```bash
pip install ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml -e @group_vars/all.local.yml --ask-vault-pass
```

De eerste keer bouwt elke node de images; daarna draait alles. Open
`https://app.<domein>/beheer` en log in met `platform_admin_token`
(`ansible-vault view group_vars/all.local.yml`). Zolang `acme_staging: true` staat, waarschuwt
de browser over het certificaat; zet het op `false` en draai het playbook opnieuw zodra alles werkt.

## Lokaal controleren

```bash
yamllint -s .
cd infra/ansible && ansible-lint site.yml
ansible-playbook -i inventory/hosts.example.yml tests/render.yml -e render_dir=/tmp/render
docker compose -f /tmp/render/node1/compose.yaml config -q
```

Nog niet in deze stap (fase 1 deel 2): offsite-backups (pgBackRest en restic), Redis en
zoeken, alerts per e-mail of push, en de failover-test zelf.
