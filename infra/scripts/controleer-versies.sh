#!/usr/bin/env bash
# Controleert of de versies die Ansible installeert echt bestaan (draait in CI).
set -euo pipefail
cd "$(dirname "$0")/../ansible"

var() { python3 -c "import yaml,sys; print(yaml.safe_load(open('group_vars/all.yml'))[sys.argv[1]])" "$1"; }

k3s=$(var k3s_version)
kube_vip=$(var kube_vip_version)
argocd=$(var argocd_chart_version)
pg_image=$(python3 -c "import yaml; print(yaml.safe_load(open('../k8s/platform/values.yaml'))['postgres']['image'])")

echo "k3s $k3s"
curl -fsSIL -o /dev/null "https://github.com/k3s-io/k3s/releases/download/${k3s//+/%2B}/k3s"

echo "kube-vip $kube_vip"
docker manifest inspect "ghcr.io/kube-vip/kube-vip:$kube_vip" >/dev/null

echo "postgres $pg_image"
docker manifest inspect "$pg_image" >/dev/null

echo "argo-cd chart $argocd"
helm template argocd argo-cd --repo https://argoproj.github.io/argo-helm --version "$argocd" >/dev/null

echo "alle versies bestaan"
