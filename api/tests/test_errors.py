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
        "params": None,
        "node": None,
    }
    same = errors.item_of(node_id, errors.fail(403, "read_only", "Je mag hier alleen kijken"))
    assert same == item
    assert errors.message_of(errors.fail(404, "not_found", "Weg")) == "Weg"


def test_item_fail_en_item_of_met_params():
    """Een tekst met {owner} of {max} moet in een andere taal ingevuld kunnen worden."""
    node_id = uuid.uuid4()
    item = errors.item_fail(node_id, "too_many", "Te veel tegelijk (meer dan 500)", max=500)
    assert (item.code, item.params) == ("too_many", {"max": 500})
    same = errors.item_of(
        node_id, errors.fail(413, "no_room_owner", "Anna heeft geen ruimte", owner="Anna")
    )
    assert same.params == {"owner": "Anna"}


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
async def test_validatiefout_van_een_eigen_validator_houdt_de_tekst(client, admin):
    """De Nederlandse tekst van een field_validator is de message (zonder "Value error, "),
    met dezelfde code en hetzelfde veld."""
    headers, _ = admin
    r = await client.post("/api/v1/tags", json={"name": "Werk", "color": "rood"}, headers=headers)
    assert r.status_code == 422
    assert message_of(r) == "Kleur moet leeg zijn of zoals #1d5fd1"
    assert code_of(r) == "invalid_input"
    assert params_of(r) == {"field": "body.color"}
    assert r.json()["detail"]["errors"][0]["type"] == "value_error"

    r = await client.put("/api/v1/admin/https", json={"domain": "1.2.3.4"}, headers=headers)
    assert r.status_code == 422
    assert message_of(r).startswith("Vul een domeinnaam in")
    assert code_of(r) == "invalid_input"


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
