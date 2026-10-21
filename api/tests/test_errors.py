"""De vorm van fouten: errors.fail, item_fail, item_of en de 422 van de validatie."""

import uuid

import pytest

from app import errors
from tests.factories import code_of, message_of, params_of
from tests.test_files import root


def test_fail_zonder_params():
    e = errors.fail(404, "not_found", "Niet gevonden")
    assert e.status_code == 404
    assert e.detail == {"message": "Niet gevonden", "code": "not_found"}
    assert e.headers is None


def test_fail_met_params_en_headers():
    e = errors.fail(429, "rate_limited", "Te veel", headers={"Retry-After": "3"}, seconds=3)
    assert e.detail == {"message": "Te veel", "code": "rate_limited", "params": {"seconds": 3}}
    assert e.headers == {"Retry-After": "3"}


def test_item_fail_en_item_of():
    node_id = uuid.uuid4()
    item = errors.item_fail(node_id, "read_only", "Je mag hier alleen kijken")
    assert item.model_dump() == {
        "id": node_id,
        "ok": False,
        "code": "read_only",
        "message": "Je mag hier alleen kijken",
        "node": None,
    }
    same = errors.item_of(node_id, errors.fail(403, "read_only", "Je mag hier alleen kijken"))
    assert same == item
    assert errors.message_of(errors.fail(404, "not_found", "Weg")) == "Weg"


@pytest.mark.anyio
async def test_validatiefout_heeft_code_en_veld(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    r = await client.post(f"/api/v1/files/{root_id}/folders", json={}, headers=headers)
    assert r.status_code == 422
    assert message_of(r) == "Ongeldige invoer"
    assert code_of(r) == "invalid_input"
    assert params_of(r) == {"field": "body.name"}
    found = r.json()["detail"]["errors"]
    assert found and found[0]["loc"] == ["body", "name"]


@pytest.mark.anyio
async def test_validatiefout_in_het_pad(client, admin):
    headers, _ = admin
    r = await client.get("/api/v1/files/geen-uuid", headers=headers)
    assert r.status_code == 422
    assert code_of(r) == "invalid_input"
    assert params_of(r) == {"field": "path.node_id"}


@pytest.mark.anyio
async def test_gewone_fout_heeft_code(client, admin):
    headers, _ = admin
    r = await client.get(f"/api/v1/files/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert r.json() == {"detail": {"message": "Niet gevonden", "code": "not_found"}}
