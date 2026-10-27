from app.tools.schema_introspect import introspect_schema


def _table(result, name):
    for t in result["tables"]:
        if t["table_name"] == name:
            return t
    raise AssertionError(f"table '{name}' not found in introspection result")


def test_lists_seeded_tables():
    result = introspect_schema("public")
    names = {t["table_name"] for t in result["tables"]}
    for expected in (
        "categories",
        "products",
        "customers",
        "orders",
        "order_items",
        "payments",
        "shipping_addresses",
        "legacy_notes",
    ):
        assert expected in names


def test_columns_have_expected_shape():
    result = introspect_schema("public")
    customers = _table(result, "customers")
    columns_by_name = {c["column_name"]: c for c in customers["columns"]}

    assert "email" in columns_by_name
    assert columns_by_name["email"]["nullable"] is False
    assert columns_by_name["id"]["column_default"].startswith("nextval(")


def test_primary_key_detected():
    result = introspect_schema("public")
    orders = _table(result, "orders")
    assert orders["primary_key"] == ["id"]


def test_foreign_keys_detected():
    result = introspect_schema("public")
    orders = _table(result, "orders")
    fk_columns = {fk["column_name"]: fk for fk in orders["foreign_keys"]}

    assert "customer_id" in fk_columns
    assert fk_columns["customer_id"]["references_table"] == "customers"
    assert fk_columns["customer_id"]["references_column"] == "id"

    order_items = _table(result, "order_items")
    fk_columns = {fk["column_name"]: fk for fk in order_items["foreign_keys"]}
    assert fk_columns["order_id"]["references_table"] == "orders"
    assert fk_columns["product_id"]["references_table"] == "products"


def test_indexes_detected():
    result = introspect_schema("public")
    orders = _table(result, "orders")
    index_names = {ix["indexname"] for ix in orders["indexes"]}
    assert "orders_customer_id_idx" in index_names
    assert "orders_customer_id_idx2" in index_names


def test_estimated_row_count_is_present_and_non_negative():
    result = introspect_schema("public")
    for t in result["tables"]:
        assert t["estimated_row_count"] >= 0
