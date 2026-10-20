#!/usr/bin/env python3
"""Rendert alle Argo CD-applicaties onder infra/k8s/apps met helm en schrijft de
manifests naar één map, zodat kubeconform ze kan controleren.

Gebruik: python3 infra/scripts/valideer-k8s.py <uitvoermap>
Vereist helm in PATH. Controleert ook dat elke chartversie echt bestaat.
"""

import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
APPS = ROOT / "infra" / "k8s" / "apps"


def helm_template(app: dict, out: pathlib.Path) -> None:
    name = app["metadata"]["name"]
    src = app["spec"]["source"]
    ns = app["spec"]["destination"]["namespace"]
    values = src.get("helm", {}).get("valuesObject", {})
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as vf:
        yaml.safe_dump(values, vf)
    if "chart" in src:
        cmd = ["helm", "template", name, src["chart"], "--repo", src["repoURL"],
               "--version", str(src["targetRevision"])]
    else:
        cmd = ["helm", "template", name, str(ROOT / src["path"])]
    cmd += ["--namespace", ns, "--include-crds", "-f", vf.name]
    print("→", name, flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"helm template {name} faalde:\n{result.stderr}")
    (out / f"{name}.yaml").write_text(result.stdout)


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(APPS.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if doc and doc.get("kind") == "Application":
                helm_template(doc, out)


if __name__ == "__main__":
    main()
