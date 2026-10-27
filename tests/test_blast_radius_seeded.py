"""
Tests for blast_radius() against the real seeded database. Expected values
below were derived by hand from seed/seed.sql's foreign keys, supplied by
the project owner — they are NOT derived from the function's own output.
If a test here fails, that is a mismatch to report, not something to paper
over by adjusting the expected values to match whatever the code produces.
"""

from app.tools.blast_radius import blast_radius


def _direct_by_table(result):
    return {d["table"]: set(d["columns"]) for d in result["direct_dependents"]}


def _transitive_by_table(result):
    return {d["table"]: d for d in result["transitive_dependents"]}


def test_blast_radius_customers():
    result = blast_radius(table="customers")

    assert _direct_by_table(result) == {
        "orders": {"customer_id"},
        "shipping_addresses": {"customer_id"},
    }

    transitive = _transitive_by_table(result)
    assert set(transitive.keys()) == {
        "orders",
        "shipping_addresses",
        "order_items",
        "payments",
    }
    assert transitive["orders"]["depth"] == 1
    assert transitive["shipping_addresses"]["depth"] == 1
    assert transitive["order_items"]["depth"] == 2
    assert transitive["order_items"]["path"] == ["customers", "orders", "order_items"]
    assert transitive["payments"]["depth"] == 2
    assert transitive["payments"]["path"] == ["customers", "orders", "payments"]

    assert result["total_dependents"] == 4


def test_blast_radius_orders():
    result = blast_radius(table="orders")

    assert _direct_by_table(result) == {
        "order_items": {"order_id"},
        "payments": {"order_id"},
    }

    transitive = _transitive_by_table(result)
    assert set(transitive.keys()) == {"order_items", "payments"}
    assert all(d["depth"] == 1 for d in transitive.values())

    assert result["total_dependents"] == 2


def test_blast_radius_categories():
    result = blast_radius(table="categories")

    assert _direct_by_table(result) == {"products": {"category_id"}}

    transitive = _transitive_by_table(result)
    assert set(transitive.keys()) == {"products", "order_items"}
    assert transitive["products"]["depth"] == 1
    assert transitive["order_items"]["depth"] == 2
    assert transitive["order_items"]["path"] == [
        "categories",
        "products",
        "order_items",
    ]

    assert result["total_dependents"] == 2


def test_blast_radius_legacy_notes_has_no_dependents():
    result = blast_radius(table="legacy_notes")

    assert result["direct_dependents"] == []
    assert result["transitive_dependents"] == []
    assert result["total_dependents"] == 0
