"""De taal van een account: PATCH /api/v1/auth/me {locale}."""

import pytest

from tests.factories import code_of


@pytest.mark.anyio
async def test_taal_kiezen_en_terug_naar_het_apparaat(client, admin):
    headers, tokens = admin
    assert tokens["user"]["locale"] is None
    assert tokens["user"]["disabled"] is False

    r = await client.patch("/api/v1/auth/me", json={"locale": "en"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["locale"] == "en"
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["locale"] == "en"

    # Een leeg verzoek verandert niets
    r = await client.patch("/api/v1/auth/me", json={}, headers=headers)
    assert r.json()["locale"] == "en"

    r = await client.patch("/api/v1/auth/me", json={"locale": None}, headers=headers)
    assert r.json()["locale"] is None
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["locale"] is None


@pytest.mark.anyio
async def test_alleen_nl_en_en(client, admin):
    headers, _ = admin
    r = await client.patch("/api/v1/auth/me", json={"locale": "de"}, headers=headers)
    assert r.status_code == 422
    assert code_of(r) == "invalid_input"
    assert r.json()["detail"]["params"]["field"] == "body.locale"


@pytest.mark.anyio
async def test_niet_zonder_inloggen(client, admin):
    r = await client.patch("/api/v1/auth/me", json={"locale": "nl"})
    assert r.status_code == 401
