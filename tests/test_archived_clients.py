"""Archived clients on the dashboard payload.

Closing a client must not restate the book. The dashboard's header and its
analytics are DEFINED as the exact sum of the active cards, so the archived
ones ride along in their own list — visible to the UI's status filter, invisible
to every total on the page.
"""


def _archive(api, client_id):
    res = api.put(f"/api/v1/clients/{client_id}", json={"archived": True})
    assert res.status_code == 200
    return res.json()


def _dashboard(api):
    res = api.get("/api/dashboard")
    assert res.status_code == 200
    return res.json()


def _names(clients):
    return {c["name"] for c in clients}


def test_an_archived_client_moves_to_its_own_list(api):
    keep = api.post("/api/v1/clients", json={"name": "Ενεργός"}).json()["id"]
    close = api.post("/api/v1/clients", json={"name": "Κλειστός"}).json()["id"]
    assert keep != close

    _archive(api, close)
    payload = _dashboard(api)

    assert _names(payload["clients"]) == {"Ενεργός"}
    assert _names(payload["archived_clients"]) == {"Κλειστός"}
    assert payload["counts"]["active_clients"] == 1
    assert payload["counts"]["archived_clients"] == 1


def test_each_list_is_flagged_so_a_card_can_mark_itself(api):
    """The flag is per LIST, not per record — the UI reads it off the object
    it was handed, and must not have to remember which array it came from."""
    api.post("/api/v1/clients", json={"name": "Ενεργός"})
    close = api.post("/api/v1/clients", json={"name": "Κλειστός"}).json()["id"]
    _archive(api, close)

    payload = _dashboard(api)
    assert all(c["archived"] is False for c in payload["clients"])
    assert all(c["archived"] is True for c in payload["archived_clients"])


def test_an_archived_client_keeps_its_figures(api):
    """The panel exists so someone can look up what a client closed at. A card
    of zeros would answer the wrong question."""
    close = api.post("/api/v1/clients", json={"name": "Κλειστός"}).json()["id"]
    api.post("/api/transactions", json={
        "client": "Κλειστός", "amount": 1240, "type": "Έσοδο",
        "vat_rate": 0.24})
    _archive(api, close)

    card = _dashboard(api)["archived_clients"][0]
    assert card["metrics"]["gross_rev"] == 1240.0
    assert card["metrics"]["net_rev"] == 1000.0
    assert card["metrics"]["net_vat"] == 240.0


def test_archiving_does_not_restate_the_header(api):
    """The regression this guards: fold the archived clients into `clients` and
    every total on the dashboard silently changes."""
    api.post("/api/v1/clients", json={"name": "Ενεργός"})
    close = api.post("/api/v1/clients", json={"name": "Κλειστός"}).json()["id"]
    for name in ("Ενεργός", "Κλειστός"):
        api.post("/api/transactions", json={
            "client": name, "amount": 1240, "type": "Έσοδο", "vat_rate": 0.24})

    before = _dashboard(api)["header"]
    assert before["total_gross_rev"] == 2480.0

    _archive(api, close)
    after = _dashboard(api)["header"]

    assert after["total_gross_rev"] == 1240.0
    assert after["total_vat"] == 240.0
    # Still the exact sum of the ACTIVE cards, which is what the header claims
    # to be — the archived client's 240 is not hiding in it.
    payload = _dashboard(api)
    assert after["total_vat"] == round(
        sum(c["metrics"]["net_vat"] for c in payload["clients"]), 2)


def test_an_archived_clients_debt_still_alerts(api):
    """Already true of the alerts, asserted here against the payload the UI
    actually receives: the archived card and the alert row must agree."""
    close = api.post("/api/v1/clients", json={"name": "Κλειστός"}).json()["id"]
    api.post("/api/transactions", json={
        "client": "Κλειστός", "amount": 500, "type": "Χρεωστούμενο"})
    _archive(api, close)

    payload = _dashboard(api)
    alert = payload["debt_alerts"]["clients"][0]
    card = payload["archived_clients"][0]
    assert alert["name"] == "Κλειστός"
    assert alert["total"] == 500.0
    assert card["metrics"]["debt"] == 500.0
    # Same key on both sides, which is what lets the UI mark the card overdue.
    assert alert["key"] == card["key"]
