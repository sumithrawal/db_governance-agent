from app.tools.unused_objects import STATUS_ACTIVE, find_unused_objects


def _table(result, name):
    for t in result["tables"]:
        if t["table_name"] == name:
            return t
    raise AssertionError(f"table '{name}' not found in unused_objects result")


def test_returns_evidence_not_a_verdict():
    """Every table gets a status + confidence + non-empty reason string —
    never a bare boolean "is_unused" flag."""
    result = find_unused_objects("public", days=90)
    for t in result["tables"]:
        assert "status" in t
        assert "confidence" in t
        assert isinstance(t["reason"], str) and len(t["reason"]) > 0
        assert "is_unused" not in t


def test_queried_tables_are_active():
    result = find_unused_objects("public", days=90)
    for name in ("products", "customers", "orders"):
        t = _table(result, name)
        assert t["status"] == STATUS_ACTIVE
        assert t["seq_scan"] > 0 or t["idx_scan"] > 0


def test_never_queried_table_is_flagged_as_a_candidate():
    """legacy_notes is never queried by seed.sql, so it must not come back
    as 'active', and must carry a specific reason rather than a bare flag."""
    result = find_unused_objects("public", days=90)
    legacy = _table(result, "legacy_notes")
    assert legacy["status"] != STATUS_ACTIVE
    assert legacy["seq_scan"] == 0
    # idx_scan is NULL (not 0) in pg_stat_user_tables when a table has no
    # indexes at all, which legacy_notes deliberately doesn't.
    assert (legacy["idx_scan"] or 0) == 0
    assert len(legacy["reason"]) > 0
    assert any(c["table_name"] == "legacy_notes" for c in result["unused_candidates"])


def test_days_parameter_is_explicit_not_hardcoded():
    result_90 = find_unused_objects("public", days=90)
    result_0 = find_unused_objects("public", days=0)
    assert result_90["window_days"] == 90
    assert result_0["window_days"] == 0


def test_duplicate_index_detected_on_orders():
    result = find_unused_objects("public", days=90)
    dupes = result["duplicate_indexes"]
    orders_dupe = next((d for d in dupes if d["table_name"] == "orders"), None)
    assert orders_dupe is not None
    assert set(orders_dupe["index_names"]) == {
        "orders_customer_id_idx",
        "orders_customer_id_idx2",
    }
    assert orders_dupe["confidence"] == "high"
    assert len(orders_dupe["reason"]) > 0


def test_duplicate_index_is_reported_regardless_of_window():
    """Duplicate-index findings don't depend on the stats-window caveat."""
    result_0 = find_unused_objects("public", days=0)
    result_365 = find_unused_objects("public", days=365)
    dupes_0 = {tuple(d["index_names"]) for d in result_0["duplicate_indexes"]}
    dupes_365 = {tuple(d["index_names"]) for d in result_365["duplicate_indexes"]}
    assert dupes_0 == dupes_365
    assert len(dupes_0) > 0


def test_blast_radius_summary_absent_when_flag_off():
    result = find_unused_objects("public", days=90, include_blast_radius=False)
    assert len(result["unused_candidates"]) > 0  # otherwise this test proves nothing
    for t in result["tables"]:
        assert "blast_radius_summary" not in t


def test_blast_radius_summary_present_on_every_non_active_finding_when_flag_on():
    result = find_unused_objects("public", days=90, include_blast_radius=True)
    assert len(result["unused_candidates"]) > 0  # otherwise this test proves nothing
    for t in result["tables"]:
        if t["status"] != STATUS_ACTIVE:
            assert "blast_radius_summary" in t
            summary = t["blast_radius_summary"]
            assert "direct_dependent_count" in summary
            assert "transitive_dependent_count" in summary
        else:
            assert "blast_radius_summary" not in t
