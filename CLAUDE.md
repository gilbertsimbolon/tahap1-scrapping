# CLAUDE.md

Reference guide for Claude Code when working in this repository.

## Project Summary

Stage 1 course scraper for MySkillsFuture (`courses.myskillsfuture.gov.sg`). Discovers courses by keyword, extracts metadata from listing + detail pages, stores results in SQLite, and exports to CSV. The target site runs Cloudflare/Akamai-grade anti-bot protection — every scraping action must go through the stealth/evasion layer described below. Do not bypass it for "quick" fixes.

---

## 1. Development Commands

### Environment Setup
```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Running the Scraper
```bash
# Single keyword
python -m scraper.main --keyword "artificial intelligence"

# Multiple keywords
python -m scraper.main --keywords "data analytics,project management"

# Headful mode (debugging anti-bot blocks)
python -m scraper.main --keyword "cybersecurity" --headful

# Resume a previously interrupted run
python -m scraper.main --resume --run-id <run_id>
```

### Exporting Data
```bash
# Export full database to CSV
python -m scraper.export --output data/courses_export.csv

# Export filtered by keyword
python -m scraper.export --keyword "artificial intelligence" --output data/ai_courses.csv
```

### Testing
```bash
# Run full test suite
pytest

# Run a specific test module
pytest tests/test_extraction.py -v

# Run with coverage
pytest --cov=scraper --cov-report=term-missing
```

### Linting / Formatting
```bash
ruff check scraper/
ruff format scraper/
```

---

## 2. Code Style & Architecture Guidelines

### Modular Structure

Keep responsibilities strictly separated. Do not mix scraping logic, storage logic, and anti-bot config in the same module.

```
scraper/
├── main.py            # CLI entrypoint, orchestration only
├── config.py           # All tunables: delays, proxy pool, UA list, retry counts
├── browser/
│   ├── stealth.py      # Playwright-Stealth setup, fingerprint masking
│   └── session.py      # Browser/context lifecycle, proxy assignment
├── discovery.py         # Step 1: search URL construction, pagination, listing parse
├── extraction.py         # Step 2: detail page field extraction
├── storage/
│   ├── db.py           # SQLite connection, schema, upsert logic
│   └── export.py        # CSV export utility
├── resilience.py        # Retry/backoff, block detection, circuit breaker
└── logger.py           # Structured logging setup
tests/
data/                    # SQLite .db file + CSV exports (gitignored)
```

### Anti-Bot & Delay Principles (Non-Negotiable)

- **No hardcoded delays.** All jitter ranges, timeouts, and retry counts live in `config.py`, never inline in scraping logic.
- **Every navigation gets jitter.** Any `page.goto()` call must be preceded by a randomized delay drawn from the configured range (default 2–8s). Never use fixed `time.sleep(n)`.
- **No sequential, robotic pacing.** Detail page visits must not process the queue in a perfectly uniform loop with identical timing — introduce controlled randomness in order and timing.
- **Fingerprint masking is mandatory, not optional.** Every new browser context must pass through `browser/stealth.py` before any page interaction. Never instantiate a raw Playwright context directly in scraping code.
- **Respect concurrency limits.** Default concurrency is low (1–3 sessions). Do not raise this in code — it's a config value for a reason.
- **Treat proxy config as required infrastructure**, not optional. New scraping code paths must accept a proxy from `browser/session.py`'s rotation pool, never a hardcoded/direct connection.
- **Detect blocks explicitly.** Any new page-parsing code must check for known block/challenge signatures (CAPTCHA, "Access Denied", Cloudflare interstitial) before attempting field extraction, and hand off to `resilience.py`'s circuit breaker on detection.

### Error Handling & Logging

- **Never let a single page failure crash the run.** Wrap all per-page/per-course extraction in try/except; log and continue to the next item.
- **Log structurally, not with print().** Use `logger.py`'s configured logger. Every failure log must include: URL, error type, retry count, timestamp.
- **Classify outcomes explicitly.** Every extraction attempt resolves to one of `SUCCESS`, `PARTIAL`, or `FAILED` — never silently drop a record or leave a status ambiguous.
- **Retries are bounded.** Use exponential backoff with a max retry count (default 3) from `config.py`. Do not add unbounded retry loops.
- **Resumability matters.** New pipeline stages must be checkpointable — persist enough state (e.g., last processed Course ID) that a crashed run can resume without re-scraping completed work.

### General Conventions

- Type-hint all function signatures.
- One extraction function = one field group (don't build monolithic "extract everything" functions — keep field parsers independently testable).
- Database writes always go through the upsert path in `storage/db.py` — never raw `INSERT` elsewhere in the codebase.
- Config changes (delay ranges, proxy lists, UA pools) should never require a code change or redeploy.

---

## 3. Core Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Core implementation |
| Browser Automation | Playwright | Headless/headful browser control, navigation |
| Stealth Layer | playwright-stealth | `navigator.webdriver` masking, fingerprint evasion |
| Storage | SQLite | Local structured storage (`course_id` as unique PK) |
| Export | Python `csv` / `pandas` | SQLite → CSV export utility |
| Proxy Layer | Rotating residential proxy provider (configurable) | IP rotation, avoid rate-limiting |
| Logging | Python `logging` (structured) | Failure tracking, audit trail |
| Testing | `pytest` | Unit/integration tests |
| Linting | `ruff` | Formatting and style enforcement |

---

## Notes for Claude Code

- Before modifying anything under `browser/` or `resilience.py`, re-read Section 2 — these modules encode hard anti-detection requirements, not stylistic preferences.
- When adding new extracted fields, update: `extraction.py`, `storage/db.py` schema, and `storage/export.py` together in the same change — do not let them drift out of sync.
- Do not add code that removes or shortens jitter delays "for speed" — this is a deliberate tradeoff against IP bans, not an oversight.
