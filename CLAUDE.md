# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Overview

"Mini Brain" practice project: unsupervised anomaly detection on synthetic EV warranty-claim data (Level 1 = rules + statistics, Level 2 = Isolation Forest). Level 3 is [agent.py](agent.py): an LLM agent (OpenAI SDK pointed at OpenRouter) that answers Vietnamese questions by calling tools. `.env` holds `OPENROUTER_API_KEY`, `OPENROUTER_URL`, `MODEL` and is not in `.gitignore`. Never print or commit `.env`.

Code comments, console output, evidence strings, and [README.md](README.md) are written in Vietnamese; keep new text consistent with that. The README is a detailed tutorial (problem, data, methods, glossary) — read it before changing detection logic.

## Commands

Uses `uv` with Python 3.11 (`.python-version`, `.venv`). There are no tests or linter configured.

```bash
uv sync                      # install deps from pyproject.toml / uv.lock
uv run generate_data.py      # regenerate data/warranty_claims.csv (seed=42, deterministic)
uv run main.py               # run the full pipeline; writes outputs/findings.json and outputs/claims_by_month.png
uv run agent.py ["câu hỏi"]  # Level 3 LLM agent; no args = 3 demo questions (calls the paid/remote API)
```

`main.py` auto-generates the CSV if it is missing. `requirements.txt` is an unpinned duplicate of the dependencies; `pyproject.toml`/`uv.lock` are authoritative.

## Architecture

Two files; `main.py` imports `DATA_PATH` and `generate` from `generate_data.py`.

- **[generate_data.py](generate_data.py)** builds normal claims (per-part base cost × lognormal noise, random dealer/date/odometer), then injects four known anomalies — the "answer key" the detectors are expected to rediscover:
  1. inverter/SUP_E: 600 extra claims in Jun–Jul → volume spike
  2. DL07: brake cost ×2.5 → cost anomaly
  3. DL13: 80 duplicated claims (`claim_id` suffixed `_D`) → duplicates
  4. 30 rows with `odometer_km = -1` → invalid values

  If you change an injected anomaly, update the README's "đáp án" table and the hardcoded highlight in `plot_monthly` (`inverter/SUP_E`).

- **[main.py](main.py)** pipeline, in order: `check_data_quality` (hard rules) → `check_cost_by_dealer` (robust z-score of per-dealer mean cost, computed within each part) → `check_volume_spike` (robust z-score of monthly counts per part/supplier against its own history), which calls `drill_down` for root cause (contribution analysis over `lot` and `dealer`) → `check_isolation_forest` (PyOD `IForest` on per-(dealer, part, month) aggregates with features normalized by per-part median).

Key conventions:
- All Level 1 detectors return the same `finding(...)` dict (`type`, `scope`, `metric`, `expected`, `actual`, `evidence`) via the `finding()` helper. This shape is the contract for the future LLM layer — new detectors should reuse it. The Isolation Forest result is a DataFrame that is only printed, not written to `findings.json`.
- `robust_zscore` (median/MAD, 0.6745 scaling) with `Z_THRESHOLD = 3.5` is the shared statistical primitive; comparisons are always made within a part (cost) or within a part/supplier series (volume) so differently scaled groups aren't mixed.
- Matplotlib uses the `Agg` backend (file output only, no windows).

### Level 3 (agent.py)

`ask()` runs a tool-calling loop (max `MAX_STEPS`) over `Toolbox`, whose methods are the tools exposed to the LLM via the `TOOLS` schema: `detect_anomalies` (reuses the `main.py` detectors, cached; `kind`/`month` filters are applied in the tool, not left to the LLM), `run_sql` (DuckDB over a `claims` view; SELECT/WITH only, single statement, capped at `MAX_ROWS`) and `drill_down`. Tool errors are returned to the LLM as `{"error": ...}` so it can self-correct. When adding a tool, add both a `Toolbox` method and a `TOOLS` entry. `.env` is parsed by a small `load_env()` (no python-dotenv). A `:free` OpenRouter model may return persistent 429s; `chat()` retries with backoff, otherwise change `MODEL` in `.env`.
