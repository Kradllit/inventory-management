"""
Tests for the restocking recommendations, restocking orders and purchase orders.
"""
import pytest

import main


@pytest.fixture(autouse=True)
def clean_orders(monkeypatch):
    """Each test starts with no restocking orders or purchase orders placed."""
    monkeypatch.setattr(main, "restock_orders", [])
    monkeypatch.setattr(main, "purchase_orders", [])


class TestRestockRecommendations:
    def test_without_budget_everything_recommended_is_included(self, client):
        data = client.get("/api/restocking/recommendations").json()
        assert data["budget"] == data["max_budget"]
        assert data["total_cost"] == pytest.approx(data["max_budget"])
        assert all(i["quantity"] == i["recommended_quantity"] for i in data["items"])

    def test_only_forecasted_inventory_items_that_need_stock(self, client):
        data = client.get("/api/restocking/recommendations").json()
        inventory = {i["sku"]: i for i in main.inventory_items}
        forecasts = {f["item_sku"]: f for f in main.demand_forecasts}
        for item in data["items"]:
            assert item["sku"] in inventory and item["sku"] in forecasts
            inv, fc = inventory[item["sku"]], forecasts[item["sku"]]
            assert item["recommended_quantity"] == inv["reorder_point"] + fc["forecasted_demand"] - inv["quantity_on_hand"]
            assert item["recommended_quantity"] > 0

    def test_items_below_reorder_point_come_first(self, client):
        items = client.get("/api/restocking/recommendations").json()["items"]
        flags = [i["below_reorder_point"] for i in items]
        assert flags == sorted(flags, reverse=True)
        assert flags[0] is True

    @pytest.mark.parametrize("budget", [0, 1000, 25000, 50000, 120000])
    def test_never_spends_more_than_the_budget(self, client, budget):
        data = client.get(f"/api/restocking/recommendations?budget={budget}").json()
        assert data["total_cost"] <= budget
        assert data["remaining_budget"] == pytest.approx(budget - data["total_cost"])
        assert all(0 <= i["quantity"] <= i["recommended_quantity"] for i in data["items"])

    def test_zero_budget_selects_nothing(self, client):
        data = client.get("/api/restocking/recommendations?budget=0").json()
        assert data["selected_count"] == 0
        assert data["lead_time_days"] == 0

    def test_negative_budget_is_rejected(self, client):
        assert client.get("/api/restocking/recommendations?budget=-5").status_code == 422

    def test_warehouse_filter(self, client):
        items = client.get("/api/restocking/recommendations?warehouse=Tokyo").json()["items"]
        assert items and all(i["warehouse"] == "Tokyo" for i in items)


class TestRestockOrders:
    def test_place_order_prices_from_inventory(self, client):
        response = client.post("/api/restocking/orders", json={
            "items": [{"sku": "TMP-201", "quantity": 10}, {"sku": "SRV-301", "quantity": 2}]
        })
        assert response.status_code == 201
        order = response.json()
        assert order["status"] == "Submitted"
        assert order["total_cost"] == pytest.approx(10 * 89.5 + 2 * 445.0)
        # Sensors take 7 days and actuators 14: the order waits for the slowest line
        assert order["lead_time_days"] == 14
        assert order["order_number"].startswith("RST-")

    def test_placed_orders_are_listed_newest_first(self, client):
        client.post("/api/restocking/orders", json={"items": [{"sku": "TMP-201", "quantity": 1}]})
        client.post("/api/restocking/orders", json={"items": [{"sku": "PSU-508", "quantity": 1}]})
        orders = client.get("/api/restocking/orders").json()
        assert [o["items"][0]["sku"] for o in orders] == ["PSU-508", "TMP-201"]

    def test_ordered_units_shrink_the_next_recommendation(self, client):
        def recommended(sku):
            items = client.get("/api/restocking/recommendations").json()["items"]
            return next((i["recommended_quantity"] for i in items if i["sku"] == sku), 0)

        before = recommended("TMP-201")
        client.post("/api/restocking/orders", json={"items": [{"sku": "TMP-201", "quantity": 50}]})
        assert recommended("TMP-201") == before - 50
        client.post("/api/restocking/orders", json={"items": [{"sku": "TMP-201", "quantity": before}]})
        assert recommended("TMP-201") == 0

    def test_unknown_sku_is_rejected(self, client):
        response = client.post("/api/restocking/orders", json={"items": [{"sku": "NOPE-1", "quantity": 1}]})
        assert response.status_code == 404

    @pytest.mark.parametrize("payload", [
        {"items": []},
        {"items": [{"sku": "TMP-201", "quantity": 0}]},
        {"items": [{"sku": "TMP-201", "quantity": -3}]},
    ])
    def test_empty_or_non_positive_orders_are_rejected(self, client, payload):
        assert client.post("/api/restocking/orders", json=payload).status_code == 422

    def test_order_over_budget_is_rejected(self, client):
        response = client.post("/api/restocking/orders", json={
            "items": [{"sku": "SRV-302", "quantity": 10}], "budget": 1000
        })
        assert response.status_code == 422
        assert client.get("/api/restocking/orders").json() == []


class TestPurchaseOrders:
    payload = {
        "backlog_item_id": "1",
        "supplier_name": "Acme Parts",
        "quantity": 100,
        "unit_cost": 12.5,
        "expected_delivery_date": "2026-12-01",
    }

    def test_create_and_fetch_by_backlog_item(self, client):
        backlog_id = main.backlog_items[0]["id"]
        created = client.post("/api/purchase-orders", json={**self.payload, "backlog_item_id": backlog_id})
        assert created.status_code == 201
        fetched = client.get(f"/api/purchase-orders/{backlog_id}").json()
        assert fetched["id"] == created.json()["id"]

        backlog = {b["id"]: b for b in client.get("/api/backlog").json()}
        assert backlog[backlog_id]["has_purchase_order"] is True
        assert backlog[backlog_id]["purchase_order_id"] == created.json()["id"]

    def test_one_purchase_order_per_backlog_item(self, client):
        backlog_id = main.backlog_items[0]["id"]
        client.post("/api/purchase-orders", json={**self.payload, "backlog_item_id": backlog_id})
        again = client.post("/api/purchase-orders", json={**self.payload, "backlog_item_id": backlog_id})
        assert again.status_code == 409

    def test_unknown_backlog_item(self, client):
        assert client.post("/api/purchase-orders", json={**self.payload, "backlog_item_id": "999"}).status_code == 404
        assert client.get("/api/purchase-orders/999").status_code == 404
