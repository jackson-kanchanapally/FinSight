from decimal import Decimal

from tests.conftest import create_company, post_tx


def test_esds_expected_result(client):
    create_company(client)
    assert post_tx(client, "BUY", 34, 429, "2026-09-01").status_code == 201
    assert post_tx(client, "SELL", 24, 1191, "2026-09-10").status_code == 201
    data = client.get("/api/portfolio/ESDS").json()
    assert Decimal(data["quantity"]) == Decimal("10.0000")
    assert Decimal(data["average_purchase_price"]) == Decimal("429.0000")
    assert Decimal(data["cost_basis"]) == Decimal("4290.0000")
    assert Decimal(data["realised_pnl"]) == Decimal("18288.0000")


def test_weighted_average_buy_partial_and_full_sell(client):
    create_company(client)
    post_tx(client, "BUY", 10, 100, "2026-09-01")
    post_tx(client, "BUY", 10, 200, "2026-09-02")
    post_tx(client, "SELL", 5, 250, "2026-09-03")
    held = client.get("/api/portfolio/ESDS").json()
    assert Decimal(held["quantity"]) == 15
    assert Decimal(held["average_purchase_price"]) == 150
    assert Decimal(held["cost_basis"]) == 2250
    assert Decimal(held["realised_pnl"]) == 500
    assert post_tx(client, "SELL", 15, 150, "2026-09-04").status_code == 201
    assert client.get("/api/portfolio/ESDS").status_code == 200
    closed = client.get("/api/portfolio/ESDS").json()
    assert Decimal(closed["quantity"]) == 0
    assert Decimal(closed["cost_basis"]) == 0


def test_oversell_unknown_company_and_bad_inputs(client):
    assert post_tx(client, "BUY", 1, 5, "2026-09-01").status_code == 404
    create_company(client)
    assert post_tx(client, "SELL", 1, 5, "2026-09-01").status_code == 422
    assert post_tx(client, "BUY", 0, 5, "2026-09-01").status_code == 422
    assert post_tx(client, "BUY", -1, 5, "2026-09-01").status_code == 422
    assert post_tx(client, "BUY", 1, 0, "2026-09-01").status_code == 422
    assert post_tx(client, "BUY", 1, -5, "2026-09-01").status_code == 422
    assert post_tx(client, "BUY", 1, 5, "2026-09-01", "-1").status_code == 422


def test_company_symbol_normalization_and_history(client):
    created = create_company(client, "esds")
    assert created["symbol"] == "ESDS"
    post_tx(client, "BUY", 2, 10, "2026-09-01")
    assert client.get("/api/companies/esds").json()["symbol"] == "ESDS"
    assert len(client.get("/api/transactions/esds").json()) == 1


def test_charges_are_in_cost_basis_and_reduce_sale_proceeds(client):
    create_company(client)
    post_tx(client, "BUY", 2, 100, "2026-09-01", "2")
    post_tx(client, "SELL", 1, 150, "2026-09-02", "1")
    data = client.get("/api/portfolio/ESDS").json()
    assert Decimal(data["average_purchase_price"]) == 101
    assert Decimal(data["cost_basis"]) == 101
    assert Decimal(data["realised_pnl"]) == 48
