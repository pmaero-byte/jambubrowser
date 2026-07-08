# Jambubrowser — canonical test + run targets
# ===========================================
#
# The test surface is organised in three tiers:
#
#   Tier 1: fast, mock-only, no engine required
#     - make test           unit tests (backend pytest + browser vitest)
#     - make test-harness   HarnessX smoke (7 stages, mock)
#     - make test-mcp       MCP server stdio smoke (21 tools)
#     - make test-browser   Browser vitest + typecheck + lint
#
#   Tier 2: integration, engine required (boots + tears down)
#     - make test-council   The council: every gate in one command
#     - make test-council-mock  Same, but force mock provider (no real LLM)
#     - make test-e2e       Thin wrapper over the council (legacy entry)
#
#   Tier 3: one-off probes
#     - make engine         Boot the engine in the foreground
#     - make bench-browser  Browser size/lint/test/build/dev benchmarks
#     - make bench-harness  HarnessX efficiency benchmark
#     - make report         Print tests/.artifacts/council.json
#
# Most CI runs `make test` then `make test-council` (with a real provider
# configured in the CI secret store).

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT   := $(shell pwd)
PYTHON      := $(REPO_ROOT)/mlx-venv/bin/python3
BROWSER_APP := $(REPO_ROOT)/browser-app
TESTS       := $(REPO_ROOT)/tests

# Required for tests so they can find backend.* and use the test key.
export JAMBU_DB_PATH      ?= :memory:
export JAMBU_VAULT_KEY    ?= test-key-do-not-use-in-production-32bytes!
export JAMBU_LLM_PROVIDER  ?= mock
export PYTHONPATH         := $(REPO_ROOT)

# ---------------------------------------------------------------------------
# Tier 1: fast, mock-only
# ---------------------------------------------------------------------------

# Core unit/integration tests — kept in sync with .github/workflows/test.yml
# (the backend job). These are the honest green set.
CORE_TESTS := tests/test_backend.py tests/test_llm_layer.py tests/test_memory_system.py tests/test_agent_loop.py

.PHONY: test
test: test-backend test-browser
	@echo "==> tier 1: all unit tests passed"

.PHONY: test-backend
test-backend:
	@echo "==> backend pytest (core unit tests, mock)"
	cd $(REPO_ROOT) && $(PYTHON) -m pytest $(CORE_TESTS) -q

.PHONY: ci
ci: test-backend test-browser
	@echo "==> ci: backend core tests + frontend typecheck/lint/test (matches GitHub Actions)"
	@echo "==> run 'make test-council' separately for full integration gates"

.PHONY: test-harness
test-harness:
	@echo "==> HarnessX smoke (7 stages, mock)"
	cd $(REPO_ROOT) && $(PYTHON) tests/smoke_harnessx_e2e.py

.PHONY: test-mcp
test-mcp:
	@echo "==> MCP server stdio smoke (21 tools)"
	cd $(REPO_ROOT) && $(PYTHON) -m pytest tests/test_mcp_server.py -q

.PHONY: test-browser
test-browser:
	@echo "==> browser typecheck + lint + vitest"
	cd $(BROWSER_APP) && npm run typecheck
	cd $(BROWSER_APP) && npm run lint
	cd $(BROWSER_APP) && npm test --silent

# ---------------------------------------------------------------------------
# Tier 2: integration, engine required
# ---------------------------------------------------------------------------

.PHONY: test-council
test-council:
	@echo "==> test council (engine + every gate)"
	cd $(REPO_ROOT) && $(PYTHON) tests/council.py

.PHONY: test-council-mock
test-council-mock:
	@echo "==> test council (force mock provider)"
	cd $(REPO_ROOT) && $(PYTHON) tests/council.py --mock

.PHONY: test-e2e
test-e2e:
	@echo "==> full e2e (thin wrapper over the council)"
	cd $(REPO_ROOT) && $(PYTHON) tests/full_e2e_real_llm.py

# ---------------------------------------------------------------------------
# Tier 3: probes
# ---------------------------------------------------------------------------

.PHONY: engine
engine:
	@echo "==> booting engine on :8001 (Ctrl-C to stop)"
	cd $(REPO_ROOT) && $(PYTHON) -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001 --reload

.PHONY: bench-browser
bench-browser:
	@echo "==> browser efficiency benchmark"
	cd $(REPO_ROOT) && $(PYTHON) tests/bench_browser.py

.PHONY: bench-harness
bench-harness:
	@echo "==> harnessX efficiency benchmark"
	cd $(REPO_ROOT) && $(PYTHON) tests/bench_harness_efficiency.py

.PHONY: report
report:
	@if [ -f $(TESTS)/.artifacts/council.json ]; then \
		cat $(TESTS)/.artifacts/council.json | $(PYTHON) -m json.tool; \
	else \
		echo "no council report at $(TESTS)/.artifacts/council.json — run 'make test-council' first"; \
	fi

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:
	@echo "Jambubrowser Makefile"
	@echo ""
	@echo "Tier 1 (fast, no engine):"
	@echo "  make test            all unit tests"
	@echo "  make test-backend    backend core unit tests (mock)"
	@echo "  make ci              backend core tests + frontend typecheck/lint/test (== CI)"
	@echo "  make test-harness    HarnessX smoke"
	@echo "  make test-mcp        MCP stdio smoke"
	@echo "  make test-browser    browser typecheck + lint + vitest"
	@echo ""
	@echo "Tier 2 (integration, engine required):"
	@echo "  make test-council          full council (uses JAMBU_LLM_PROVIDER)"
	@echo "  make test-council-mock     same, but force mock"
	@echo "  make test-e2e              thin wrapper (legacy entry)"
	@echo ""
	@echo "Tier 3 (probes):"
	@echo "  make engine               boot engine in foreground"
	@echo "  make bench-browser        browser efficiency benchmark"
	@echo "  make bench-harness        harnessX efficiency benchmark"
	@echo "  make report               print last council report"
