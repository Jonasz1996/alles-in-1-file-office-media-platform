"""De worker van de container analyse (fase 6, §3.11, Keuze 56–59; X2a).

    python -I -c runtime.BOOT /srv --socket /run/analyse/worker.sock --models /models

- __main__.py: de start (de sleutel uit de omgeving, PR_SET_DUMPABLE 0, de zelftest);
- server.py: pid 1 (de socket, de handdruk, de rij van één per kind, de deadline, cancel);
- child.py: de kinderen werk en zoeken (pid 1 zonder numpy; het kind met);
- caps.py: caps (de modellen van models.lock, de opdrachten, de grenzen van de cgroup);
- selftest.py: de afscherming, en RefusingWorker als die faalt;
- models.py en ops/: wat een kind doet (X2a: faces; X2b group, X3 beeld).

pid 1 laadt nooit numpy of onnxruntime: alleen een kind zet /opt/analyse in sys.path.
"""
