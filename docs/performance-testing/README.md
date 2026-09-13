# Performance Testing Framework — roboshop on OpenShift

> **Note:** `Jenkinsfile` here is kept alongside the test plan for reference. To actually run this in Jenkins via a Multibranch Pipeline job, copy or symlink it to the repository root, since that's where Jenkins looks by default.

**Goal:** validate roboshop's response time, throughput, and error-rate behavior under a modest concurrent load (10 virtual users), using JMeter, with results automated through Jenkins and gated by pass/fail quality criteria.

**Scope note:** the Developer Sandbox is a shared, quota-limited environment (recall Phase 2/3: `compute-deploy` quota caps at `30` vCPU / `30Gi` memory *shared across everyone on the cluster*, and individual pods here run with small `limits.cpu`/`limits.memory` — e.g. mysql's `cpu: 1`, `memory: 1000Mi`). 10 concurrent users is a deliberately conservative load appropriate for this environment — this is a **functional performance baseline**, not a production capacity test. Don't extrapolate these numbers to a real production sizing exercise.

---

## 1. Non-Functional Requirements (NFRs)

**Load profile:** 10 concurrent virtual users, ramp-up over 10 seconds (1 user/sec), 5 iterations per user (50 total iterations per transaction), no think-time pauses removed (realistic pacing retained — see `.jmx` for exact timers).

| # | Transaction | Maps to | Avg response time | 95th percentile | Error rate |
|---|---|---|---|---|---|
| TC01 | Browse home page | `GET /` (frontend) | < 1500 ms | < 2500 ms | < 1% |
| TC02 | Browse catalogue | `GET /api/catalogue/products` | < 800 ms | < 1500 ms | < 1% |
| TC03 | View product detail | `GET /api/catalogue/product/{sku}` | < 600 ms | < 1200 ms | < 1% |
| TC04 | Register user | `POST /api/user/register` | < 1000 ms | < 2000 ms | < 1% |
| TC05 | Login | `POST /api/user/login` | < 700 ms | < 1500 ms | < 1% |
| TC06 | Get anonymous/unique ID | `GET /api/user/uniqueid` | < 500 ms | < 1000 ms | < 1% |
| TC07 | Add to cart | `GET /api/cart/add/{user}/{sku}/{qty}` | < 700 ms | < 1500 ms | < 1% |
| TC08 | View cart | `GET /api/cart/{user}` | < 500 ms | < 1000 ms | < 1% |
| TC09 | Shipping lookup | `GET /api/shipping/...` | < 1000 ms | < 2000 ms | < 1% |
| TC10 | Checkout / payment | `POST /api/payment/pay/{user}` | < 1200 ms | < 2500 ms | < 1% |

**Overall (all transactions combined):**
- **Throughput:** ≥ 8 transactions/sec sustained at 10 concurrent users
- **Overall error rate:** < 1%
- **No transaction's 95th percentile exceeds 2x its average target** (a proxy check for erratic/unstable response times, not just slow-on-average)

These are starting targets, calibrated for a small Sandbox deployment. Treat the *first successful test run* as your real baseline, then tighten or relax these numbers based on actual observed behavior — don't treat the table above as gospel before you've run it once.

---

## 2. Test design: UI-flow vs. API/microservice transactions

Given the frontend is an Nginx reverse proxy (not a JS SPA needing a real browser), **HTTP Request samplers are used for both categories** — the distinction is *what each transaction represents*, not the sampler type:

- **"Frontend UI" transactions (TC01-TC03):** hit the routes a real user's browser would call while browsing — the static page and the catalogue/product API calls the frontend's own JS makes on page load. This exercises `frontend → catalogue → mongodb`.
- **"API/microservice" transactions (TC04-TC10):** hit backend endpoints directly, simulating registration, login, cart, shipping, and payment flows. Endpoints TC06 and TC07 are **confirmed exact paths**, taken directly from real browser Network-tab traffic captured during manual testing earlier in this project (`GET /api/user/uniqueid`, `GET /api/cart/add/{user}/{sku}/{qty}`). TC04, TC05, TC09, TC10 use standard roboshop microservice endpoint conventions — **verify these against your own browser DevTools Network tab before your first real run**, since exact paths weren't independently confirmed from live traffic the way TC06/TC07 were.

If you want genuine full-browser UI testing (real page rendering, JS execution, click simulation) rather than API-level simulation of the UI flow, that requires JMeter's WebDriver Sampler plugin plus a real/headless browser — a heavier, separate setup not included here; flag if you want that as a follow-up.

---

## 3. Files in this framework

| File | Purpose |
|---|---|
| `roboshop-performance-test.jmx` | The JMeter test plan — 10 transactions, response/duration assertions per the NFR table, Summary and Aggregate Report listeners |
| `Jenkinsfile` | Declarative pipeline: checkout → run JMeter → generate HTML dashboard → evaluate quality gate → archive artifacts |
| `quality_gate.py` | Parses the `.jtl` results and fails the build if any NFR threshold is breached |

---

## 4. Running the test plan locally (CLI)

**Install JMeter** (if not already):
```bash
sudo apt install -y default-jre
cd /opt
sudo wget https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-5.6.3.tgz
sudo tar -xzf apache-jmeter-5.6.3.tgz
export PATH="/opt/apache-jmeter-5.6.3/bin:$PATH"
```

**Edit the target host** before running — open `roboshop-performance-test.jmx` and update the `HOST` User Defined Variable to your actual Route hostname (e.g. `frontend-bsathi2020-dev.apps.rm3.7wse.p1.openshiftapps.com`), or override it on the command line without editing the file:
```bash
jmeter -n -t roboshop-performance-test.jmx \
  -JHOST=frontend-bsathi2020-dev.apps.rm3.7wse.p1.openshiftapps.com \
  -l results.jtl \
  -e -o report-html/
```
- `-n` — non-GUI (CLI) mode, required for real load generation (never load-test from the GUI)
- `-l results.jtl` — raw results file
- `-e -o report-html/` — generate the interactive HTML dashboard directly from this run, into `report-html/`

Open `report-html/index.html` in a browser — this is JMeter's built-in interactive dashboard (response time graphs, throughput over time, percentile tables, per-transaction breakdowns).

**To generate the dashboard separately, from an existing `.jtl`** (e.g., one produced by Jenkins), without re-running the test:
```bash
jmeter -g results.jtl -o report-html/
```

**GUI mode** (for authoring/debugging the test plan only — never for actual load generation):
```bash
jmeter -t roboshop-performance-test.jmx
```

---

## 5. Quality gate logic (used by both the CLI and Jenkins)

`quality_gate.py` reads `results.jtl`, computes per-transaction average and 95th-percentile response time plus overall error rate and throughput, compares each against the NFR table above, and exits non-zero (failing the build) if any threshold is breached. Run it standalone any time:
```bash
python3 quality_gate.py results.jtl
```
It prints a pass/fail table per transaction plus the overall summary — the same table Jenkins will use to gate a deployment.
