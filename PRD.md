# Product Requirement Document (PRD)
## MySkillsFuture Course Scraper — Stage 1

---

## 1. Document Control & Metadata

| Field | Detail |
|---|---|
| **Document Title** | Course Scraper — Stage 1: Data Extraction & Local Storage |
| **Product Area** | Data Acquisition / Web Automation |
| **Target Platform** | MySkillsFuture (courses.myskillsfuture.gov.sg) |
| **Document Owner** | Senior Technical Product Manager |
| **Status** | Draft — v1.0 |
| **Version** | 1.0 |
| **Last Updated** | 2026-07-05 |
| **Stakeholders** | Engineering Lead, Data Engineering, QA/Automation, Compliance/Legal |
| **Related Docs** | Stage 2 PRD (Scheduling & Orchestration) — *Future*, Stage 3 PRD (Analytics Layer) — *Future* |

### 1.1 Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-05 | Product Team | Initial draft for Stage 1 scope |

---

## 2. Project Overview & Business Goals

### 2.1 Background

MySkillsFuture is a Singapore government portal that indexes government-subsidized training courses. There is currently no reliable, repeatable, and automated internal mechanism to extract structured course data (pricing, provider, duration, funding eligibility) at scale. The portal is protected by enterprise-grade anti-bot and Web Application Firewall (WAF) systems (e.g., Cloudflare/Akamai), which makes naive scraping approaches (basic HTTP requests, unmasked headless browsers) unreliable and prone to IP bans.

### 2.2 Problem Statement

Manual research of course listings is slow, error-prone, and does not scale across hundreds of keywords or thousands of course listings. Teams need a structured, queryable local dataset of course metadata to support downstream use cases (market research, competitive analysis, subsidy tracking) without violating site availability or triggering anti-bot defenses.

### 2.3 Goals & Objectives

- Build a **keyword-driven scraper** capable of discovering and extracting course listings reliably.
- Persist clean, de-duplicated structured data into a **local SQLite database**.
- Provide a simple, repeatable **CSV export utility** for downstream analysis.
- Ensure the scraper operates **stealthily and resiliently** against anti-bot systems without requiring manual intervention on every run.

### 2.4 Non-Goals (Out of Scope for Stage 1)

- Cloud deployment, scheduling, or orchestration (Stage 2).
- Real-time/streaming data pipelines or dashboards (Stage 3).
- Multi-region distributed scraping infrastructure.
- Data visualization or BI reporting layers.
- Automatic keyword generation/discovery (keywords are user-supplied in Stage 1).

### 2.5 Success Metrics (KPIs)

| Metric | Target |
|---|---|
| Scrape success rate (pages loaded without block) | ≥ 95% per run |
| Duplicate record rate in SQLite | 0% (enforced via schema constraint) |
| Data field completeness (per course record) | ≥ 98% for mandatory fields |
| Mean time between IP-ban incidents | No hard-block within a single run of ≤ 500 courses |
| Pipeline crash rate due to single-page failure | 0% (must degrade gracefully) |

---

## 3. Functional Specifications

### 3.1 High-Level User Flow

```
[User Input: Keyword(s)]
        │
        ▼
[1. Construct Search URL] ───► https://courses.myskillsfuture.gov.sg/search?q={keyword}&termOrigin=AUTOCOMPLETE
        │
        ▼
[2. Load Search Results Page] ──► Apply stealth/evasion layer
        │
        ▼
[3. Extract Course ID + Course URL from listing] ──► Repeat across all paginated result pages
        │
        ▼
[4. Queue unique Course URLs for detail scraping]
        │
        ▼
[5. Visit each Course Detail Page] ──► Apply jitter delay + stealth layer
        │
        ▼
[6. Extract metadata fields] ──► Title, Provider, Duration/Mode, Fees
        │
        ▼
[7. Upsert record into SQLite (Course ID = Primary Key)]
        │
        ▼
[8. On demand: Export utility] ──► SQLite → Clean CSV
```

### 3.2 Functional Requirement Details

#### FR-1: Keyword-Based Discovery

| ID | Requirement |
|---|---|
| FR-1.1 | System shall accept one or more user-defined keywords via CLI argument, config file, or input list. |
| FR-1.2 | System shall URL-encode keywords and construct valid search URLs matching the pattern: `https://courses.myskillsfuture.gov.sg/search?q={encoded_keyword}&termOrigin=AUTOCOMPLETE` |
| FR-1.3 | System shall detect the total number of result pages (via pagination controls or result count) and iterate through all pages sequentially. |
| FR-1.4 | System shall stop pagination gracefully when no further "next page" control is detected, or a configurable max-page-limit is reached (safety cap). |
| FR-1.5 | System shall support processing multiple keywords in a single run, treating each as an independent discovery job. |

#### FR-2: Two-Step Data Extraction

**Step 1 — Search Results Listing Extraction**

| ID | Requirement |
|---|---|
| FR-2.1 | System shall parse each search results page and extract, for every listed course: **Course ID** (e.g., `TGS-2024048217`) and **full Course Detail URL** (including `serpIdKey` query parameter). |
| FR-2.2 | System shall de-duplicate Course IDs within the discovery phase before queuing detail-page visits (avoid revisiting the same course twice in one run). |
| FR-2.3 | Extracted Course ID + URL pairs shall be temporarily queued/persisted (e.g., in-memory queue or staging table) prior to Step 2 execution, to support resumability on failure. |

**Step 2 — Course Detail Page Extraction**

| ID | Requirement |
|---|---|
| FR-2.4 | System shall navigate to each queued Course Detail URL and extract the fields defined in Section 3.3 (Data Field Dictionary). |
| FR-2.5 | System shall handle missing/optional fields gracefully (e.g., record `NULL` rather than failing the entire extraction). |
| FR-2.6 | System shall apply a randomized delay (jitter) before each detail-page navigation (see NFR-2). |

#### FR-3: Local Storage (SQLite)

| ID | Requirement |
|---|---|
| FR-3.1 | System shall persist all extracted fields into a local SQLite `.db` file. |
| FR-3.2 | The `Course ID` field shall be enforced as a **UNIQUE PRIMARY KEY** to prevent duplicate insertion across sequential or repeated runs. |
| FR-3.3 | On re-scraping an existing Course ID, system shall perform an **UPSERT** (update changed fields, e.g., fee changes, without creating a duplicate row) rather than insert a duplicate or fail silently. |
| FR-3.4 | System shall record a `last_scraped_at` timestamp on every insert/update for data-freshness tracking. |

#### FR-4: Data Export Utility

| ID | Requirement |
|---|---|
| FR-4.1 | System shall provide a standalone command/script (e.g., `export_csv.py` or CLI flag `--export`) to export all SQLite records into a CSV file. |
| FR-4.2 | Exported CSV shall include a header row matching database column names (human-readable). |
| FR-4.3 | Export shall support an optional filter (e.g., by keyword/search-tag or date range) — *stretch goal, not blocking for MVP*. |
| FR-4.4 | Export shall handle encoding correctly (UTF-8) to preserve special characters in course titles/provider names. |

### 3.3 Data Field Dictionary

| Field Name | Source Step | Type | Mandatory | Description |
|---|---|---|---|---|
| `course_id` | Step 1 | TEXT | Yes | Unique course reference, e.g., `TGS-2024048217` |
| `course_url` | Step 1 | TEXT | Yes | Full detail page URL including `serpIdKey` |
| `course_title` | Step 2 | TEXT | Yes | Full course name |
| `provider_name` | Step 2 | TEXT | Yes | Training provider / organization name |
| `training_duration` | Step 2 | TEXT | Yes | Duration value as displayed (e.g., "8 hours") |
| `training_mode` | Step 2 | TEXT | No | Delivery mode (e.g., Classroom, Online, Blended) |
| `fee_standard` | Step 2 | TEXT/DECIMAL | Yes | Full/standard course fee (pre-subsidy) |
| `fee_subsidized` | Step 2 | TEXT/DECIMAL | No | Nett fee after applicable subsidy, if displayed |
| `search_keyword` | Step 1 | TEXT | Yes | The keyword that surfaced this course (for traceability) |
| `scraped_at` | System | TIMESTAMP | Yes | First extraction timestamp |
| `last_scraped_at` | System | TIMESTAMP | Yes | Most recent successful update timestamp |
| `scrape_status` | System | TEXT | Yes | `SUCCESS`, `PARTIAL`, or `FAILED` |

---

## 4. Non-Functional Specifications

### 4.1 Anti-Bot & Evasion Architecture

| ID | Requirement | Detail |
|---|---|---|
| NFR-1.1 | **User-Agent Rotation** | Maintain a pool of realistic, current desktop/mobile User-Agent strings; rotate per session or per N requests. |
| NFR-1.2 | **TLS/JA3 Fingerprint Mimicry** | Use libraries/tools capable of mimicking real browser TLS handshakes (e.g., `curl_cffi`, `undetected-chromedriver`, or `playwright-stealth`) to avoid JA3-based fingerprinting detection. |
| NFR-1.3 | **Browser Property Masking** | Override automation-detectable properties, including `navigator.webdriver`, `navigator.plugins`, `navigator.languages`, WebGL vendor/renderer strings, and canvas fingerprint noise. |
| NFR-1.4 | **Headful/Headless Flexibility** | Support both headless (default, performance) and headful (debug/fallback) execution modes, since headless flags can be detected by advanced WAFs. |
| NFR-1.5 | **Session Persistence** | Reuse cookies/session tokens within a scraping session to appear as continuous, legitimate browsing rather than repeated fresh visits. |
| NFR-1.6 | **Viewport & Locale Randomization** | Randomize viewport dimensions and browser locale/timezone settings per session to reduce fingerprint uniformity. |

### 4.2 Behavioral Human Mimicry

| ID | Requirement | Detail |
|---|---|---|
| NFR-2.1 | **Randomized Jitter Delays** | Apply randomized delay (e.g., 2–8 seconds, configurable range) between page navigations and actions; avoid fixed/uniform intervals. |
| NFR-2.2 | **Simulated Interaction** | Simulate human-like mouse movement, scroll events, and variable typing speed where feasible, particularly on search input fields. |
| NFR-2.3 | **Request Pacing** | Cap maximum requests-per-minute per session/proxy to stay under detection thresholds. |
| NFR-2.4 | **Non-Linear Navigation Patterns** | Avoid perfectly sequential, robotic page traversal patterns; introduce minor randomized variance in navigation order where pagination allows. |

### 4.3 Proxy Integration

| ID | Requirement | Detail |
|---|---|---|
| NFR-3.1 | **Rotating Residential Proxies** | Support integration with third-party rotating residential proxy providers via configurable proxy pool/endpoint. |
| NFR-3.2 | **Proxy Health Checks** | Validate proxy responsiveness before use; automatically exclude dead/blocked proxies from rotation for the remainder of the run. |
| NFR-3.3 | **Sticky Sessions (Optional)** | Support sticky IP sessions for the duration of a single course-detail scrape sequence to reduce mid-session fingerprint mismatch. |
| NFR-3.4 | **Proxy Failover** | On proxy failure/timeout, automatically retry with an alternate proxy from the pool (bounded retry count). |

### 4.4 Error Handling & Resiliency

| ID | Requirement | Detail |
|---|---|---|
| NFR-4.1 | **Graceful Page-Level Failure** | A single failed page (timeout, WAF block, malformed DOM) shall be logged and skipped — it must **not** terminate the overall pipeline run. |
| NFR-4.2 | **Retry Logic** | Implement bounded exponential backoff retry (e.g., 3 attempts) for transient failures (timeouts, 5xx errors, connection resets). |
| NFR-4.3 | **Structured Logging** | All failures logged with timestamp, URL, error type, and retry count to a local log file (e.g., `scraper.log`) for post-run auditing. |
| NFR-4.4 | **Block Detection & Circuit Breaker** | Detect anti-bot challenge pages (CAPTCHA, "Access Denied," Cloudflare interstitial) explicitly; on repeated detection, pause/back off the affected proxy/session rather than repeatedly hammering the block. |
| NFR-4.5 | **Resumability** | System shall support resuming a run from the last successfully processed Course ID/page in the event of a crash or manual interruption. |
| NFR-4.6 | **Data Integrity on Failure** | Partial or failed extractions shall be marked with `scrape_status = 'PARTIAL'` or `'FAILED'` rather than silently discarded or inserted with corrupt data. |

### 4.5 Performance & Configurability

| ID | Requirement |
|---|---|
| NFR-5.1 | All timing, retry, proxy, and concurrency parameters shall be externally configurable (e.g., via `config.yaml` or `.env`), not hardcoded. |
| NFR-5.2 | System shall support a configurable concurrency limit (default: low concurrency, e.g., 1–3 parallel sessions) to balance throughput against detection risk. |

### 4.6 Compliance Consideration

- Scraping activity shall respect a configurable rate ceiling to minimize load impact on the target government platform.
- Document should note that legal/compliance review of `robots.txt` and Terms of Use is a prerequisite to production operation (tracked as an action item outside engineering scope).

---

## 5. Database Schema Definition

### 5.1 Table: `courses`

```sql
CREATE TABLE IF NOT EXISTS courses (
    course_id           TEXT PRIMARY KEY NOT NULL,   -- e.g., 'TGS-2024048217'
    course_url          TEXT NOT NULL,
    course_title        TEXT NOT NULL,
    provider_name       TEXT NOT NULL,
    training_duration   TEXT,
    training_mode       TEXT,
    fee_standard        TEXT,
    fee_subsidized      TEXT,
    search_keyword      TEXT NOT NULL,
    scrape_status       TEXT NOT NULL DEFAULT 'SUCCESS'
                         CHECK (scrape_status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    scraped_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scraped_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 Table: `scrape_logs` (Supporting Audit Table)

```sql
CREATE TABLE IF NOT EXISTS scrape_logs (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    url             TEXT NOT NULL,
    error_type      TEXT,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    logged_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 Indexing Strategy

| Index | Table | Column(s) | Purpose |
|---|---|---|---|
| `idx_search_keyword` | `courses` | `search_keyword` | Speed up filtered exports/queries by keyword |
| `idx_scrape_status` | `courses` | `scrape_status` | Quickly identify failed/partial records for re-run |
| `idx_run_id` | `scrape_logs` | `run_id` | Group logs per execution run for debugging |

### 5.4 Upsert Logic (Reference)

```sql
INSERT INTO courses (course_id, course_url, course_title, provider_name,
                      training_duration, training_mode, fee_standard,
                      fee_subsidized, search_keyword, scrape_status, last_scraped_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(course_id) DO UPDATE SET
    course_title      = excluded.course_title,
    provider_name      = excluded.provider_name,
    training_duration  = excluded.training_duration,
    training_mode      = excluded.training_mode,
    fee_standard       = excluded.fee_standard,
    fee_subsidized     = excluded.fee_subsidized,
    scrape_status      = excluded.scrape_status,
    last_scraped_at    = CURRENT_TIMESTAMP;
```

---

## 6. Acceptance Criteria (Definition of Done)

### 6.1 Functional Acceptance Criteria

- [ ] Given a keyword input, the system successfully constructs a valid search URL and retrieves results across **all** available pagination pages.
- [ ] All Course IDs and Course URLs are correctly extracted from search listing pages with **zero parsing errors** on well-formed pages.
- [ ] For each unique Course ID, the system successfully navigates to and extracts all mandatory fields from the detail page (Title, Provider, Duration, Fees).
- [ ] Running the scraper twice on the same keyword produces **no duplicate rows** in the `courses` table (verified via `course_id` uniqueness constraint).
- [ ] Re-running on previously scraped courses correctly **updates** existing rows (e.g., changed fee) rather than duplicating or ignoring them.
- [ ] The CSV export utility runs independently of the scraper and produces a valid, correctly encoded CSV matching the SQLite table contents.

### 6.2 Non-Functional Acceptance Criteria

- [ ] The scraper completes a test run of ≥ 100 courses without triggering a full IP ban (validated via monitoring of block/challenge page occurrences).
- [ ] `navigator.webdriver` and other standard automation fingerprints are verifiably masked (validated via a bot-detection test page, e.g., `bot.sannysoft.com` or equivalent).
- [ ] Randomized jitter delay is confirmed present between consecutive requests (validated via request timestamp logs showing non-uniform intervals).
- [ ] Proxy rotation is functional and verifiable (validated via logging of distinct outbound IPs across a test run).
- [ ] Simulating a single page failure (e.g., forced timeout) does **not** crash the overall run; the pipeline logs the failure and continues to the next item.
- [ ] All failures are captured in `scrape_logs` with sufficient detail (URL, error type, retry count) to diagnose without re-running the scraper.
- [ ] All anti-bot and delay parameters are configurable via an external config file without requiring code changes.

### 6.3 Out-of-Scope Confirmation

- [ ] Confirmed: No scheduling/cron automation is included in Stage 1 (manual/CLI-triggered execution only).
- [ ] Confirmed: No dashboard/UI layer is included; interaction is via CLI/config only.

---

*End of Document — Stage 1 PRD*
