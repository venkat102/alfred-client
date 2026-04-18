# Alfred frontend smoke tests

Playwright-based end-to-end coverage for the Alfred chat UI. Four
spec files, each scoped to one flow that's expensive to validate by
hand on every UI change.

## What's here

| Spec | What it checks | Needs LLM? | Destructive? |
|------|----------------|------------|--------------|
| `send-prompt.spec.ts` | golden path: greeting -> chat reply (fast-path, no crew) | no | no |
| `mode-switcher.spec.ts` | mode selection persists across page reload | no | no |
| `preview-approve.spec.ts` | dev prompt -> preview -> approve -> deploy success | yes | yes |
| `rollback.spec.ts` | deployed changeset -> rollback -> status "Rolled Back" | yes | yes |

The last two are gated behind `ALFRED_RUN_SLOW_TESTS=1` because they
write to the live site and can take several minutes per run.

## Setup

```bash
cd alfred_client/frontend-tests
npm install
npx playwright install chromium
```

## Running

```bash
# Fast tests only (default - the two non-destructive specs):
npm test

# Everything including the slow + destructive specs:
ALFRED_RUN_SLOW_TESTS=1 npm test

# Headed mode (watch the browser do its thing):
npm run test:headed

# Debug a single test:
npx playwright test send-prompt --debug

# Generate selectors by recording interaction:
npm run codegen
```

## Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `ALFRED_BASE_URL` | `http://localhost:8000` | bench URL |
| `ALFRED_USER` | `Administrator` | Frappe login |
| `ALFRED_PASSWORD` | `admin` | Frappe login |
| `ALFRED_HEADLESS` | `true` | set `false` to watch |
| `ALFRED_TIMEOUT_MS` | `60000` | per-test cap |
| `ALFRED_RUN_SLOW_TESTS` | unset | set `1` to enable dev/rollback specs |

Slow tests also need:

- The processing app running (`uvicorn alfred.main:app`).
- Ollama reachable at the URL in Alfred Settings.
- At least one working LLM model pulled.

## Adding more tests

- Selectors live in `tests/fixtures.ts`. Prefer adding helpers there
  over duplicating selectors across specs.
- Today's selectors use CSS classes (`.alfred-*`) because the Vue
  components don't carry `data-testid` attributes yet. Moving to
  testids is the recommended next step - one touch per component.
- Keep fast tests fast. Anything that needs an LLM round-trip or
  writes to the DB belongs behind `ALFRED_RUN_SLOW_TESTS`.

## Known limitations

- Tests run serially (`workers: 1`, `fullyParallel: false`) because
  the Frappe site is stateful. Don't change this without separating
  the data per test.
- No auto-cleanup between tests. `preview-approve` and `rollback` are
  intentionally chained; running one without the other will either
  leak state or fail on missing preconditions.
- `data-testid` scaffolding is a pending improvement - class-based
  selectors will break on CSS refactors.
