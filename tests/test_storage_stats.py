from app.tools.storage_stats import get_storage_stats


def test_tables_sorted_descending_by_total_size():
    result = get_storage_stats("public")
    sizes = [t["total_size_bytes"] for t in result["tables"]]
    assert sizes == sorted(sizes, reverse=True)


def test_all_seeded_tables_present_with_positive_sizes():
    result = get_storage_stats("public")
    names = {t["table_name"] for t in result["tables"]}
    assert "orders" in names
    for t in result["tables"]:
        assert t["table_size_bytes"] >= 0
        assert t["index_size_bytes"] >= 0
        assert t["total_size_bytes"] >= t["table_size_bytes"]


def test_ratio_threshold_is_configurable_not_hardcoded():
    strict = get_storage_stats("public", ratio_threshold=0.01)
    lenient = get_storage_stats("public", ratio_threshold=1000.0)

    strict_flagged = {t["table_name"] for t in strict["tables"] if t["flagged"]}
    lenient_flagged = {t["table_name"] for t in lenient["tables"] if t["flagged"]}

    assert len(strict_flagged) > 0
    assert len(lenient_flagged) < len(strict_flagged)


def test_flagged_entries_carry_a_reason_not_just_a_boolean():
    """Flags are a signal with evidence, not a verdict."""
    result = get_storage_stats("public", ratio_threshold=0.01)
    flagged = [t for t in result["tables"] if t["flagged"]]
    assert len(flagged) > 0
    for t in flagged:
        assert isinstance(t["reason"], str) and len(t["reason"]) > 0
        assert isinstance(t["index_to_table_ratio"], (float, type(None)))


def test_unflagged_entries_have_no_reason():
    result = get_storage_stats("public", ratio_threshold=1000.0)
    for t in result["tables"]:
        if not t["flagged"]:
            assert t["reason"] is None
