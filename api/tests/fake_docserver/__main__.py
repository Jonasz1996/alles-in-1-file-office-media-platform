"""De nep-DS als proces (voor de e2e):

    python -m tests.fake_docserver --port P --secret-file F [--link-secret-file G]
        [--time-scale S] [--save-delay D] [--host H]

Geheimen alleen uit bestanden, nooit op de opdrachtregel of in de log. Stoppen op PID.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

from .app import FakeConfig, create_app


def _read(path: str | None) -> str | None:
    if not path:
        return None
    value = Path(path).read_text().strip()
    return value or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.fake_docserver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--secret-file", required=True, help="het office-geheim")
    parser.add_argument("--link-secret-file", help="vast linkgeheim (anders nieuw bij restart)")
    parser.add_argument("--time-scale", type=float, default=1.0, help="maal de pauzes")
    parser.add_argument("--save-delay", type=float, default=5.0, help="s tot status 2")
    args = parser.parse_args(argv)
    secret = _read(args.secret_file)
    if not secret:
        print("Geen office-geheim in --secret-file", file=sys.stderr)
        return 2
    config = FakeConfig(
        secret=secret,
        link_secret=_read(args.link_secret_file),
        time_scale=args.time_scale,
        save_delay=args.save_delay,
    )
    uvicorn.run(
        create_app(config),
        host=args.host,
        port=args.port,
        ws="websockets-sansio",
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
