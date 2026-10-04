"""The extracted pieces of `run_case`: input resolution and verdict counting.

`run_case` was 206 lines because three separable concerns were inline. Two of
them are pure — validating the run's inputs and folding N verdicts into a
summary — which is what makes them worth testing directly rather than through a
browser session.

The behaviours pinned here are the ones that decide whether a gate means
anything:

* a **flaky** cell is counted separately *and* fails the variant — green but
  not trustworthy is not the same as green;
* a variant is `ok` only if every one of its cells passed;
* `top_n`-style truncation is not involved, but the row-count cap *is* enforced
  on caller-supplied rows, not only on rows loaded from a dataset — otherwise a
  caller could bypass it;
* the matrix size guard is expressed in **cells**, because cells are what
  consume a browser session.
"""
from __future__ import annotations

import pytest

from backend.modules import qa_cases
from backend.modules.qa_cases import (
    MATRIX_OPTION_KEYS,
    _build_variants,
    _resolve_rows,
    _summarise_matrix,
    _summarise_rows,
)


def ok(run_id="r1", **over):
    cell = {"ok": True, "run_id": run_id, "status": "passed"}
    cell.update(over)
    return cell


def bad(run_id="r2", **over):
    cell = {"ok": False, "run_id": run_id, "status": "failed"}
    cell.update(over)
    return cell


# ---------------------------------------------------------------------------
# _resolve_rows
# ---------------------------------------------------------------------------

class TestResolveRows:
    def test_no_rows_and_no_dataset_means_no_matrix(self):
        assert _resolve_rows({}, None) is None

    def test_explicit_rows_win(self):
        rows = [{"id": 1}]
        assert _resolve_rows({"dataset_id": 7}, rows) is rows

    def test_empty_list_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _resolve_rows({}, [])

    def test_non_list_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            _resolve_rows({}, {"not": "a list"})

    def test_caller_supplied_rows_still_hit_the_cap(self, monkeypatch):
        """The cap must not be bypassable by passing rows directly."""
        import backend.modules.qa_datasets as datasets

        monkeypatch.setattr(datasets, "MAX_DATASET_ROWS", 2, raising=False)
        with pytest.raises(ValueError, match="max 2 rows"):
            _resolve_rows({}, [{"i": 1}, {"i": 2}, {"i": 3}])

    def test_missing_dataset_is_a_clear_error(self, monkeypatch):
        import backend.modules.qa_datasets as datasets

        monkeypatch.setattr(datasets, "get_dataset", lambda _id: None, raising=False)
        with pytest.raises(ValueError, match="not found"):
            _resolve_rows({"dataset_id": 42}, None)

    def test_dataset_rows_are_loaded_when_not_supplied(self, monkeypatch):
        import backend.modules.qa_datasets as datasets

        rows = [{"a": 1}]
        monkeypatch.setattr(datasets, "get_dataset",
                            lambda _id: {"rows": rows}, raising=False)
        assert _resolve_rows({"dataset_id": 1}, None) is rows


# ---------------------------------------------------------------------------
# _build_variants
# ---------------------------------------------------------------------------

class TestBuildVariants:
    def test_no_matrix_is_no_variants(self):
        assert _build_variants(None, row_count=0) == []
        assert _build_variants([], row_count=3) == []

    def test_names_are_defaulted_positionally(self):
        variants = _build_variants([{"viewport": {"width": 100}},
                                    {"viewport": {"width": 200}}],
                                   row_count=1)
        assert [v["name"] for v in variants] == ["variant-1", "variant-2"]

    def test_explicit_names_are_kept_and_coerced_to_str(self):
        variants = _build_variants([{"name": 7}], row_count=1)
        assert variants[0]["name"] == "7"

    def test_only_known_context_options_are_forwarded(self):
        variants = _build_variants(
            [{"name": "phone", "viewport": {"width": 390}, "evil_key": "x"}],
            row_count=1,
        )
        assert variants[0]["context_options"] == {"viewport": {"width": 390}}

    def test_none_valued_options_are_dropped(self):
        variants = _build_variants([{"locale": None, "timezone_id": "UTC"}],
                                   row_count=1)
        assert variants[0]["context_options"] == {"timezone_id": "UTC"}

    def test_non_list_matrix_is_rejected(self):
        with pytest.raises(ValueError, match="must be a list"):
            _build_variants({"viewport": {}}, row_count=1)

    def test_non_dict_entry_is_rejected(self):
        with pytest.raises(ValueError, match="must be objects"):
            _build_variants(["desktop"], row_count=1)

    def test_cell_count_guard_fires_on_the_cross_product(self, monkeypatch):
        """Cells = variants × rows; a big cross product opens sessions at once."""
        import backend.modules.browser_agent as browser_agent

        class FakeService:
            max_sessions = 1

        monkeypatch.setattr(browser_agent, "BrowserAgentService", FakeService,
                            raising=False)
        variants = [{"name": f"v{i}"} for i in range(3)]
        # 3 variants × 10 rows = 30 cells > 1 session × 16.
        with pytest.raises(ValueError, match="matrix too large"):
            _build_variants(variants, row_count=10)

    def test_guard_allows_the_limit_exactly(self, monkeypatch):
        import backend.modules.browser_agent as browser_agent

        class FakeService:
            max_sessions = 2

        monkeypatch.setattr(browser_agent, "BrowserAgentService", FakeService,
                            raising=False)
        variants = _build_variants([{"name": "a"}, {"name": "b"}], row_count=16)
        assert len(variants) == 2          # 32 cells == the cap, not over it

    def test_matrix_option_keys_are_the_run_matrix_vocabulary(self):
        assert "viewport" in MATRIX_OPTION_KEYS
        assert "device_scale_factor" in MATRIX_OPTION_KEYS
        assert "is_mobile" in MATRIX_OPTION_KEYS


# ---------------------------------------------------------------------------
# _summarise_matrix / _summarise_rows
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def stub_health(monkeypatch):
    """Health is a DB read; the summaries only need it present."""
    monkeypatch.setattr(qa_cases, "_update_case_health",
                        lambda case_id: {"quarantined": False})
    monkeypatch.setattr(qa_cases, "get_run", lambda run_id: {"id": run_id},
                        raising=False)


class TestSummariseMatrix:
    def test_all_passed(self):
        summary = _summarise_matrix(1, "case", "qa-team",
                                    [ok("a"), ok("b")], ["v", "v"],
                                    row_count=2, variant_count=1)
        assert summary["ok"] is True
        assert summary["status"] == "passed"
        assert summary["cells"] == 2
        assert summary["passed_cells"] == 2
        assert summary["failed_cells"] == 0

    def test_one_failure_fails_the_whole_run(self):
        summary = _summarise_matrix(1, "case", "qa-team",
                                    [ok("a"), bad("b")], ["v", "v"],
                                    row_count=2, variant_count=1)
        assert summary["ok"] is False
        assert summary["status"] == "failed"
        assert summary["failed_cells"] == 1

    def test_flaky_counts_separately_and_still_fails(self):
        """Green-but-flaky must not be counted as green."""
        summary = _summarise_matrix(1, "case", "qa-team",
                                    [ok("a", flaky=True)], ["v"],
                                    row_count=1, variant_count=1)
        assert summary["flaky_cells"] == 1
        bucket = summary["by_variant"][0]
        assert bucket["flaky"] == 1
        assert bucket["passed"] == 1        # the cell itself succeeded

    def test_per_variant_buckets(self):
        summary = _summarise_matrix(1, "case", "qa-team",
                                    [ok("a"), bad("b"), ok("c")],
                                    ["mobile", "mobile", "desktop"],
                                    row_count=3, variant_count=2)
        buckets = {b["variant"]: b for b in summary["by_variant"]}
        assert buckets["mobile"]["cells"] == 2
        assert buckets["mobile"]["ok"] is False
        assert buckets["desktop"]["cells"] == 1
        assert buckets["desktop"]["ok"] is True

    def test_run_ids_are_preserved_in_order(self):
        summary = _summarise_matrix(1, "case", "qa-team",
                                    [ok("a"), ok("b")], ["v", "v"],
                                    row_count=2, variant_count=1)
        assert summary["run_ids"] == ["a", "b"]

    def test_health_is_included(self):
        summary = _summarise_matrix(1, "case", "qa-team", [ok()], ["v"],
                                    row_count=1, variant_count=1)
        assert summary["health"] == {"quarantined": False}

    def test_the_matrix_flag_is_set(self):
        """Callers branch on it to tell a grid run from a single run."""
        assert _summarise_matrix(1, "c", "a", [ok()], ["v"],
                                 row_count=1, variant_count=1)["matrix"] is True


class TestSummariseRows:
    def test_counts_passed_and_failed(self):
        summary = _summarise_rows(1, "qa-team", [ok("a"), bad("b"), bad("c")])
        assert summary["rows"] == 3
        assert summary["passed_rows"] == 1
        assert summary["failed_rows"] == 2
        assert summary["ok"] is False

    def test_flaky_rows_are_counted(self):
        summary = _summarise_rows(1, "qa-team", [ok("a", flaky=True)])
        assert summary["flaky_rows"] == 1

    def test_all_passed(self):
        summary = _summarise_rows(1, "qa-team", [ok("a")])
        assert summary["ok"] is True
        assert summary["status"] == "passed"

    def test_actor_and_health_are_carried_through(self):
        summary = _summarise_rows(7, "alice", [ok()])
        assert summary["actor"] == "alice"
        assert summary["case_id"] == 7
        assert summary["health"] == {"quarantined": False}