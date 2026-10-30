"""De nep-DS van OnlyOffice Docs 10.0 voor tests (§3.6): create_app(FakeConfig(...)).

In pytest via httpx.ASGITransport (of als echte server voor de socket); in de e2e als proces:
python -m tests.fake_docserver --port P --secret-file F --link-secret-file G --time-scale S.
De nep is geen vervanging van de echte DS; die draait in de job office (§4.5).
"""

from .app import Conn, Doc, FakeConfig, FakeDocServer, create_app

__all__ = ["Conn", "Doc", "FakeConfig", "FakeDocServer", "create_app"]
