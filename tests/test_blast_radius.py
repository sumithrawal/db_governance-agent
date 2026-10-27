"""
Unit tests for the pure compute_blast_radius() function — no database, just
hand-written table lists shaped like introspect_schema()["tables"] (only
the fields compute_blast_radius actually reads: table_name, foreign_keys).
"""

import pytest

from app.tools.blast_radius import compute_blast_radius


def _table(name, foreign_keys=None):
    return {"table_name": name, "foreign_keys": foreign_keys or []}


def _fk(column_name, references_table, references_column="id"):
    return {
        "column_name": column_name,
        "references_table": references_table,
        "references_column": references_column,
        "constraint_name": f"{column_name}_fkey",
    }


def test_unknown_table_raises_value_error():
    tables = [_table("a")]
    with pytest.raises(ValueError):
        compute_blast_radius(tables, "does_not_exist")


def test_table_with_no_dependents():
    # a <- b (b depends on a); c has no dependents at all.
    tables = [
        _table("a"),
        _table("b", [_fk("a_id", "a")]),
        _table("c"),
    ]
    result = compute_blast_radius(tables, "c")

    assert result["table"] == "c"
    assert result["direct_dependents"] == []
    assert result["transitive_dependents"] == []
    assert result["total_dependents"] == 0
    assert "foreign-key" in result["limitations"].lower()
    assert "safe to drop" not in result["limitations"].lower()


def test_direct_dependents_grouped_by_table_with_columns():
    # orders references customers twice (billing + shipping), both should
    # land under one direct_dependents entry with both column names.
    tables = [
        _table("customers"),
        _table(
            "orders",
            [
                _fk("billing_customer_id", "customers"),
                _fk("shipping_customer_id", "customers"),
            ],
        ),
    ]
    result = compute_blast_radius(tables, "customers")

    assert result["direct_dependents"] == [
        {"table": "orders", "columns": ["billing_customer_id", "shipping_customer_id"]}
    ]
    assert result["total_dependents"] == 1


def test_transitive_chain_has_correct_depth_and_path():
    # a <- b <- c <- d, each one hop deeper.
    tables = [
        _table("a"),
        _table("b", [_fk("a_id", "a")]),
        _table("c", [_fk("b_id", "b")]),
        _table("d", [_fk("c_id", "c")]),
    ]
    result = compute_blast_radius(tables, "a")

    by_table = {d["table"]: d for d in result["transitive_dependents"]}
    assert by_table["b"]["depth"] == 1
    assert by_table["b"]["path"] == ["a", "b"]
    assert by_table["c"]["depth"] == 2
    assert by_table["c"]["path"] == ["a", "b", "c"]
    assert by_table["d"]["depth"] == 3
    assert by_table["d"]["path"] == ["a", "b", "c", "d"]
    assert result["total_dependents"] == 3


def test_self_reference_is_ignored():
    # employees.manager_id -> employees.id is a self-reference and must not
    # make employees its own dependent.
    tables = [_table("employees", [_fk("manager_id", "employees")])]
    result = compute_blast_radius(tables, "employees")

    assert result["direct_dependents"] == []
    assert result["transitive_dependents"] == []
    assert result["total_dependents"] == 0


def test_cycle_terminates_and_does_not_revisit_start():
    # a <- b <- c <- a: a cycle. Starting from a, b and c are dependents,
    # but the walk must not loop forever or report a as its own dependent.
    tables = [
        _table("a", [_fk("c_id", "c")]),
        _table("b", [_fk("a_id", "a")]),
        _table("c", [_fk("b_id", "b")]),
    ]
    result = compute_blast_radius(tables, "a")

    dependent_names = {d["table"] for d in result["transitive_dependents"]}
    assert dependent_names == {"b", "c"}
    assert "a" not in dependent_names
    assert result["total_dependents"] == 2

    by_table = {d["table"]: d for d in result["transitive_dependents"]}
    assert by_table["b"]["depth"] == 1
    assert by_table["c"]["depth"] == 2


def test_diamond_shaped_dependents_counted_once():
    # a <- b, a <- c, b <- d, c <- d: d depends on a through two paths but
    # must only be counted/visited once.
    tables = [
        _table("a"),
        _table("b", [_fk("a_id", "a")]),
        _table("c", [_fk("a_id", "a")]),
        _table("d", [_fk("b_id", "b"), _fk("c_id", "c")]),
    ]
    result = compute_blast_radius(tables, "a")

    dependent_names = [d["table"] for d in result["transitive_dependents"]]
    assert sorted(dependent_names) == ["b", "c", "d"]
    assert len(dependent_names) == len(set(dependent_names))
    assert result["total_dependents"] == 3
