# Alfred Benchmarking Guide

Alfred ships with an in-process benchmark harness that runs a fixed set of
prompts through the full pipeline and captures per-run metrics. Use it to:

- Quantify the impact of a change (prompt tweak, new phase, tool
  consolidation, model swap).
- Establish a baseline on your hardware before enabling optional phases.
- Gate CI against regressions on tokens, latency, or first-try accuracy.

This guide covers how to run the harness, how to read the output, how to
compare two runs, and how to extend the prompt set.

## Files

- `alfred_processing/scripts/benchmark_pipeline.py` - the harness.
- `alfred_processing/scripts/compare_benchmarks.py` - side-by-side comparison
  + regression gate.
- `alfred_processing/benchmarks/*.json` - saved report files (git-ignored on
  most checkouts; keep your baseline in source control if you want a shared
  reference point).

## What it measures

Per prompt:

| Metric | What it captures |
|---|---|
| `wall_clock_seconds` | End-to-end latency from prompt submission to changeset delivery |
| `llm_completion_count` | Number of LiteLLM completion calls across the whole pipeline (enhancer + clarifier + crew agents + rescue + reflection) |
| `llm_prompt_tokens` | Sum of prompt tokens across all LLM calls |
| `llm_completion_tokens` | Sum of completion tokens |
| `llm_total_tokens` | `prompt + completion` |
| `mcp_tool_calls` | Total MCP invocations |
| `mcp_tool_calls_by_name` | Per-tool breakdown |
| `first_try_extraction` | `True` if `_extract_changes` produced non-empty on first pass, before any rescue |
| `rescue_triggered` | `True` if `_rescue_regenerate_changeset` ran |
| `dry_run_retries` | Count of dry-run self-heal retries (0 when the first pass was clean) |
| `changeset_items` | Item count in the final changeset |
| `changeset_valid` | `True` if the final dry-run passed |
| `dedup_hits` | Count of MCP calls the Phase 1 dedup cache short-circuited |
| `error` | String message if the run crashed, else `None` |

Aggregated across prompts:

| Metric | How it's computed |
|---|---|
| `avg_wall_clock_seconds` | Mean of per-prompt `wall_clock_seconds` |
| `avg_llm_total_tokens` | Mean of per-prompt token totals |
| `avg_llm_completion_count` | Mean number of completion calls |
| `avg_mcp_tool_calls` | Mean MCP calls per prompt |
| `first_try_success_rate` | Fraction of prompts that extracted cleanly without rescue |

## What it stubs out

The harness runs the pipeline in-process against mock services. It **does
not** need a running Frappe site or a live WebSocket - everything below is
faked so the only real work is LLM calls + pipeline logic.

- **WebSocket** - `FakeWebSocket` captures outbound messages for verification.
- **MCPClient** - canned responses for common tool calls (`get_site_info`,
  `get_doctype_schema`, `check_permission`, `dry_run_changeset`, etc.), plus
  `lookup_doctype` and `lookup_pattern` for the consolidated tools. Unknown
  tool calls return `{"error": "not_found", "message": "tool X not stubbed in benchmark"}`
  which the tool wrappers surface as a tool failure without crashing.
- **State store** - no Redis; task-state persistence is a no-op.
- **Admin portal plan check** - not configured, so the phase is skipped.
- **conn.ask_human** - auto-replies "proceed with sensible defaults" so the
  clarifier doesn't block waiting for a real user response.

## What it actually exercises

Real components:

- Prompt sanitizer
- `enhance_prompt` (real litellm call)
- `_clarify_requirements` (real litellm call; answers auto-generated)
- CrewAI full crew (real LLM calls for every agent)
- `_extract_changes` / `_rescue_regenerate_changeset`
- `_dry_run_with_retry` (against the mock MCP, so validation is
  shape-checking only, not savepoint-based)
- Phase 1 MCP tracking state (budget cap, dedup, failure counter)
- Phase 2 handoff condenser (attached to crew tasks)
- Phase 3 tracing (if enabled) and reflection (if enabled)

## Running a benchmark

```bash
cd alfred_processing

# Default: 1 run per prompt, all 6 prompts, tagged with today's date
.venv/bin/python scripts/benchmark_pipeline.py --tag baseline

# Remote LLM override (the harness respects env vars the same way the
# live pipeline does)
FALLBACK_LLM_MODEL="ollama/qwen2.5-coder:32b" \
FALLBACK_LLM_BASE_URL="http://10.243.88.140:11434" \
.venv/bin/python scripts/benchmark_pipeline.py --tag phase3 --runs 1

# Run just prompts 1 and 3
.venv/bin/python scripts/benchmark_pipeline.py --tag quicktest --prompts 1 3

# Multiple runs per prompt (for variance analysis)
.venv/bin/python scripts/benchmark_pipeline.py --tag baseline --runs 3
```

Output goes to `benchmarks/<tag>_YYYY-MM-DD.json`. Live progress is printed
to stdout: each prompt shows `wall=... tokens=... llm_calls=... mcp_calls=...
first_try=True rescue=False items=N valid=True` when it finishes.

One full run of all 6 prompts against qwen2.5-coder:32b on local Ollama
takes ~25-30 minutes. Run it in the background or under `nohup` for
unattended runs.

## Reading the JSON report

```jsonc
{
  "tag": "phase3",
  "timestamp": "2026-04-13T22:27:55",
  "model": "ollama/qwen2.5-coder:32b",
  "base_url": "http://10.243.88.140:11434",
  "runs_per_prompt": 1,
  "results": [
    {
      "prompt_id": 1,
      "prompt_name": "notification_approval_flow",
      "wall_clock_seconds": 216.3,
      "llm_completion_count": 23,
      "llm_prompt_tokens": 52400,
      "llm_completion_tokens": 5311,
      "llm_total_tokens": 57711,
      "mcp_tool_calls": 7,
      "mcp_tool_calls_by_name": {
        "lookup_doctype": 3,
        "lookup_pattern": 2,
        "check_permission": 1,
        "dry_run_changeset": 1
      },
      "first_try_extraction": true,
      "rescue_triggered": false,
      "dry_run_retries": 0,
      "changeset_items": 1,
      "changeset_valid": true,
      "dedup_hits": 2,
      "error": null
    },
    ...
  ],
  "summary": {
    "avg_wall_clock_seconds": 223.8,
    "avg_llm_total_tokens": 57326.67,
    "avg_llm_completion_count": 23.0,
    "avg_mcp_tool_calls": 8.33,
    "first_try_success_rate": 1.0,
    "rescue_rate": 0.0
  }
}
```

### What to look at

- **`first_try_success_rate`** should be `1.0` on the stock prompt set. If
  it drops, the agent is drifting - check the crew's final output for
  `<|im_start|>` chat template leakage or repeated JSON arrays.
- **`avg_llm_total_tokens`** is the main cost metric. Phase 1+2 got this
  from ~76k down to ~57k per prompt; further reductions are harder without
  touching the crew architecture.
- **`avg_wall_clock_seconds`** depends heavily on your LLM hardware. A
  local qwen2.5-coder:32b on a M-series Mac runs ~200-250s per prompt. A
  remote GPU can run this faster; a CPU-only box runs it slower.
- **`dedup_hits`** tells you how much the Phase 1 cache is saving. If
  this is `0` on every prompt, the agent isn't repeating tool calls (rare)
  or the cache isn't wired up (check `init_run_state` is being called).

## Comparing two runs

`compare_benchmarks.py` diffs per-prompt and summary metrics, flags
regressions in red and wins in green, and exits 1 if the AFTER run
regressed on tokens (>2%), latency (>10%), or first-try accuracy when the
`--gate` flag is set.

```bash
.venv/bin/python scripts/compare_benchmarks.py \
    benchmarks/baseline_2026-04-13.json \
    benchmarks/phase2_2026-04-13.json

# CI-style gate: exit 1 on regression
.venv/bin/python scripts/compare_benchmarks.py \
    benchmarks/phase1_2026-04-13.json \
    benchmarks/phase2_2026-04-13.json \
    --gate
```

Sample output:

```
BEFORE: benchmarks/phase1_2026-04-13.json
  model: ollama/qwen2.5-coder:32b
  timestamp: 2026-04-13T17:49:44

AFTER:  benchmarks/phase2_2026-04-13.json
  model: ollama/qwen2.5-coder:32b
  timestamp: 2026-04-13T22:27:55

==========================================================================================
Prompt                              Metric          Before       After        Delta
==========================================================================================
notification_approval_flow          tokens          58061.0 -> 57711.0 (-0.6%)
                                    latency_s       203.0 -> 216.3 (+6.5%)
                                    ...
new_doctype_basic                   tokens          71359.0 -> 58884.0 (-17.5%)
                                    latency_s       217.0 -> 224.4 (+3.4%)
                                    ...

==========================================================================================
SUMMARY
==========================================================================================
  Avg wall-clock (s)           236.8 -> 223.8 (-5.5%)
  Avg LLM tokens               64140.2 -> 57326.7 (-10.6%)
  Avg LLM calls                25.0 -> 23.0 (-8.0%)
  Avg MCP calls                7.2 -> 8.3 (+16.2%)
  First-try accuracy           1.0 -> 1.0 (+0.0%)

No regressions. Phase 1 is cleared.
```

Green means improvement (lower is better for tokens/latency/calls, higher is
better for accuracy). Red means regression.

## Gate thresholds

The `--gate` flag exits 1 when the AFTER run regresses on any of:

- **Tokens**: `avg_llm_total_tokens > 1.02 * before_tokens` (>2% regression)
- **Latency**: `avg_wall_clock_seconds > 1.10 * before_latency` (>10%)
- **Accuracy**: `first_try_success_rate < before_accuracy` (any drop)

The thresholds are deliberately forgiving on latency (LLM variance is high
on shared hardware) and strict on tokens (token cost is deterministic) and
accuracy (any drop is a real regression).

MCP call count is **not** gated. A phase change can reasonably cause MCP
calls to go up (e.g., the reflection step makes more `check_permission`
calls) or down (dedup hits reduce them). We report it but don't block.

## Baseline + per-phase comparisons

The recommended workflow for testing a new phase or optimization:

1. **Capture the baseline** on whatever main is:
   ```bash
   git checkout main
   .venv/bin/python scripts/benchmark_pipeline.py --tag baseline
   ```

2. **Implement your change** on a branch.

3. **Run the benchmark again** with a new tag:
   ```bash
   git checkout my-branch
   .venv/bin/python scripts/benchmark_pipeline.py --tag my-change
   ```

4. **Compare with the gate**:
   ```bash
   .venv/bin/python scripts/compare_benchmarks.py \
       benchmarks/baseline_2026-04-13.json \
       benchmarks/my-change_2026-04-13.json \
       --gate
   ```

If the gate passes AND you see the per-prompt improvement you expected, ship
the change. If the gate passes but improvements are flat, either your
change doesn't do what you thought, or the prompt set isn't exercising the
code path you optimized - add a targeted prompt (see "Extending" below).

## Extending the prompt set

Prompts live in `BENCHMARK_PROMPTS` at the top of `benchmark_pipeline.py`:

```python
BENCHMARK_PROMPTS = [
    {
        "id": 1,
        "name": "notification_approval_flow",
        "prompt": "Create a notification that emails the expense approver ...",
        "notes": "Tests the approval notification pattern. ...",
    },
    ...
]
```

When adding a prompt:

1. **Pick a tightly scoped request** that exercises one customization type
   (Notification, Custom Field, Server Script, Workflow, etc.). Avoid
   "build a complete HR module"-style prompts - they're high-variance and
   drown out the signal you're trying to measure.
2. **Give it a unique short name** (`name` field) so you can grep for it
   in logs.
3. **Make sure the mock MCP has canned responses** for any DocTypes the
   prompt references. If not, add entries to `_CANNED_DOCTYPE_SCHEMAS` so
   `get_doctype_schema` returns a meaningful shape. Unknown doctypes
   produce a tool failure which still lets the benchmark complete, but the
   run won't be representative of production.
4. **Reword to avoid the prompt sanitizer** - the `check_prompt` function
   has a keyword-based intent classifier. If a prompt doesn't match
   `create_notification` / `create_script` / `create_doctype` /
   `add_field`, it gets blocked. Typical rewording: prepend "Create a
   server script that..." instead of "Validate that...".
5. **Run it once** to verify it completes and captures the metrics you
   expected, before checking the prompt in.

The existing 6 prompts were chosen to cover:

| Prompt | Coverage |
|---|---|
| `notification_approval_flow` | Notification pattern, approval event selection (known qwen drift case) |
| `custom_field_simple` | Single Custom Field (smallest possible changeset) |
| `new_doctype_basic` | New DocType with explicit field spec |
| `notification_different_domain` | Different domain from `#1` - catches hardcoded example bias |
| `server_script_validation` | Server Script with permission check + Python compile |
| `audit_log` | Multi-item changeset (DocType + Server Script together) |

Keep this coverage spread when adding new prompts.

## Reproducibility notes

- LLM output is non-deterministic even at `temperature=0`. Expect ±10%
  variance on tokens and ±15% on wall-clock between runs of the same
  prompt with the same model. For rigorous comparison, run with `--runs 3`
  and eyeball the spread.
- `litellm.success_callback` accounts tokens per streaming chunk; the
  harness dedupes by `response.id` to avoid double-counting. If you see
  suspiciously huge token counts (e.g., >100k per prompt), check that the
  dedup is working and that your LLM provider is returning `id` on every
  chunk.
- The harness silences CrewAI telemetry (`CREWAI_DISABLE_TELEMETRY=true`,
  `OTEL_SDK_DISABLED=true`) to prevent external network calls from
  slowing runs down or leaking data.
