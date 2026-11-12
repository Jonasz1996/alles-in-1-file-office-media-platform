# PostgreSQL met Patroni: Patroni beheert replicatie en failover via etcd.
ARG BASE_IMAGE=postgres:17-bookworm
FROM ${BASE_IMAGE}
ARG PATRONI_VERSION=4.0.6
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv \
 && python3 -m venv /opt/patroni \
 && /opt/patroni/bin/pip install --no-cache-dir "patroni[etcd3,psycopg3]==${PATRONI_VERSION}" \
 && rm -rf /var/lib/apt/lists/*
USER postgres
ENTRYPOINT ["/opt/patroni/bin/patroni", "/etc/patroni/patroni.yml"]
