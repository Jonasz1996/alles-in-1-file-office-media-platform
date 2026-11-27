"""De container analyse: gezichten (X2) en zoeken op beeld (X3). Fase 6, §3.11.

wire.py (het protocol), lock.py (models.lock), client.py en monitor.py (de stand, Keuze 59).
De app importeert hier nooit numpy of onnxruntime (test_no_numpy_in_app.py); die staan alleen
in /opt/analyse, voor de kinderen van de worker (app/analyse/worker/, X2a).
"""
