"""
Tests for the report endpoints and the global filters they honour.
"""
import pytest

import main


class TestQuarterlyReports:
    """Test suite for /api/reports/quarterly."""

    def test_unfiltered_counts_every_order(self, client):
        data = client.get("/api/reports/quarterly").json()
        assert sum(q["total_orders"] for q in data) == len(main.orders)

    def test_warehouse_filter_narrows_the_report(self, client):
        all_orders = sum(q["total_orders"] for q in client.get("/api/reports/quarterly").json())
        tokyo = client.get("/api/reports/quarterly?warehouse=Tokyo").json()
        expected = len([o for o in main.orders if o["warehouse"] == "Tokyo"])
        assert sum(q["total_orders"] for q in tokyo) == expected
        assert 0 < expected < all_orders

    def test_month_filter_keeps_only_that_quarter(self, client):
        data = client.get("/api/reports/quarterly?month=2025-01").json()
        assert [q["quarter"] for q in data] == ["Q1-2025"]

    def test_quarters_from_another_year_are_counted(self, client, monkeypatch):
        extra = dict(main.orders[0], id="x", order_date="2026-02-10T09:00:00")
        monkeypatch.setattr(main, "orders", main.orders + [extra])
        data = client.get("/api/reports/quarterly").json()
        quarters = [q["quarter"] for q in data]
        assert "Q1-2026" in quarters
        # Sorted by year first, so the 2026 quarter comes last
        assert quarters[-1] == "Q1-2026"


class TestMonthlyTrends:
    """Test suite for /api/reports/monthly-trends."""

    def test_status_filter_narrows_the_trend(self, client):
        data = client.get("/api/reports/monthly-trends?status=Delivered").json()
        expected = len([o for o in main.orders if o["status"] == "Delivered"])
        assert sum(m["order_count"] for m in data) == expected
        assert all(m["delivered_count"] == m["order_count"] for m in data)

    def test_category_filter_is_case_insensitive(self, client):
        lower = client.get("/api/reports/monthly-trends?category=sensors").json()
        title = client.get("/api/reports/monthly-trends?category=Sensors").json()
        assert lower == title
        assert len(lower) > 0

    @pytest.mark.parametrize("query", ["", "?warehouse=all&category=all&status=all&month=all"])
    def test_all_means_unfiltered(self, client, query):
        data = client.get(f"/api/reports/monthly-trends{query}").json()
        assert sum(m["order_count"] for m in data) == len(main.orders)
