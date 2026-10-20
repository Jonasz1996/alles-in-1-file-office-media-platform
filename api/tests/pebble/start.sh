#!/usr/bin/env bash
# Start Pebble (de test-CA van Let's Encrypt) en een DNS-server die elk domein naar
# 127.0.0.1 laat wijzen, voor tests/test_acme_pebble.py. Pebble controleert de
# HTTP-01-challenge echt, op poort 5002 van 127.0.0.1.
#
#   eval "$(tests/pebble/start.sh)"   # zet PEBBLE_DIRECTORY en PEBBLE_CA
#
# Pebble's eigen https-poort krijgt een kort geldig certificaat voor localhost van een
# wegwerp-CA die dit script zelf maakt; het testcertificaat uit de Pebble-release
# verloopt op een vaste datum.
set -euo pipefail
version=v2.10.1
bin=$(go env GOPATH)/bin
for tool in pebble pebble-challtestsrv; do
  [ -x "$bin/$tool" ] || go install "github.com/letsencrypt/pebble/v2/cmd/$tool@$version" >&2
done
out=${PEBBLE_OUT:-${TMPDIR:-/tmp}/pebble}
mkdir -p "$out"

# Nieuw certificaat als er nog geen is of het binnen een uur verloopt. Zo blijft een
# Pebble die nog van een vorige keer draait bij hetzelfde certificaat.
if ! openssl x509 -checkend 3600 -noout -in "$out/localhost.pem" >/dev/null 2>&1; then
  cat > "$out/ca.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = Pebble test-CA (wegwerp)
[ext]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
CNF
  cat > "$out/localhost.cnf" <<CNF
[ext]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
CNF
  ec=(-newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes)
  openssl req -x509 "${ec[@]}" -config "$out/ca.cnf" -days 2 \
    -keyout "$out/ca.key" -out "$out/ca.pem" >&2
  openssl req -new "${ec[@]}" -subj /CN=localhost \
    -keyout "$out/localhost.key" -out "$out/localhost.csr" >&2
  openssl x509 -req -in "$out/localhost.csr" -CA "$out/ca.pem" -CAkey "$out/ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days 1 \
    -extfile "$out/localhost.cnf" -extensions ext -out "$out/localhost.pem" >&2
  rm -f "$out/ca.key" "$out/localhost.csr"
fi

cat > "$out/config.json" <<JSON
{
  "pebble": {
    "listenAddress": "127.0.0.1:14000",
    "managementListenAddress": "127.0.0.1:15000",
    "certificate": "$out/localhost.pem",
    "privateKey": "$out/localhost.key",
    "httpPort": 5002,
    "tlsPort": 5001,
    "ocspResponderURL": "",
    "externalAccountBindingRequired": false,
    "retryAfter": {"authz": 1, "order": 1},
    "keyAlgorithm": "ecdsa",
    "profiles": {"default": {"description": "test", "validityPeriod": 7776000}}
  }
}
JSON
"$bin/pebble-challtestsrv" -defaultIPv4 127.0.0.1 -defaultIPv6 "" -dnsserver 127.0.0.1:8053 \
  -http01 "" -https01 "" -tlsalpn01 "" -doh "" -management 127.0.0.1:8055 \
  > "$out/challtestsrv.log" 2>&1 &
PEBBLE_VA_NOSLEEP=1 "$bin/pebble" -config "$out/config.json" -dnsserver 127.0.0.1:8053 \
  > "$out/pebble.log" 2>&1 &
for _ in $(seq 1 50); do
  if curl -fs --cacert "$out/ca.pem" -o /dev/null https://localhost:14000/dir; then
    echo "export PEBBLE_DIRECTORY=https://localhost:14000/dir"
    echo "export PEBBLE_CA=$out/ca.pem"
    exit 0
  fi
  sleep 0.2
done
echo "Pebble start niet; zie $out/pebble.log. Draait er nog een oude Pebble (met een" >&2
echo "ander certificaat)? Stop die eerst: pkill -f pebble" >&2
exit 1
