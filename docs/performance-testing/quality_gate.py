#!/usr/bin/env python3
"""
quality_gate.py — parses a JMeter .jtl (CSV format) results file and evaluates
each transaction against the NFR thresholds defined in NFR_THRESHOLDS below.

Exit code 0  = all thresholds passed (deployment may proceed)
Exit code 1  = one or more thresholds breached (deployment should be blocked)

Usage:
    python3 quality_gate.py results.jtl
"""

import sys
import csv
import math
from collections import defaultdict

# NFR thresholds per transaction — must match performance-testing-README.md's table.
# avg_ms / p95_ms are upper bounds (fail if measured value exceeds this).
# max_error_pct is the maximum acceptable error rate for that transaction, as a percentage.
NFR_THRESHOLDS = {
    "TC01_Browse_Home":        {"avg_ms": 1500, "p95_ms": 2500, "max_error_pct": 1.0},
    "TC02_Browse_Catalogue":   {"avg_ms": 800,  "p95_ms": 1500, "max_error_pct": 1.0},
    "TC03_View_Product_Detail":{"avg_ms": 600,  "p95_ms": 1200, "max_error_pct": 1.0},
    "TC04_Register_User":      {"avg_ms": 1000, "p95_ms": 2000, "max_error_pct": 1.0},
    "TC05_Login_User":         {"avg_ms": 700,  "p95_ms": 1500, "max_error_pct": 1.0},
    "TC06_Get_UniqueId":       {"avg_ms": 500,  "p95_ms": 1000, "max_error_pct": 1.0},
    "TC07_Add_To_Cart":        {"avg_ms": 700,  "p95_ms": 1500, "max_error_pct": 1.0},
    "TC08_View_Cart":          {"avg_ms": 500,  "p95_ms": 1000, "max_error_pct": 1.0},
    "TC09_Shipping_Lookup":    {"avg_ms": 1000, "p95_ms": 2000, "max_error_pct": 1.0},
    "TC09a_Shipping_Match":    {"avg_ms": 1000, "p95_ms": 2000, "max_error_pct": 1.0},
    "TC09b_Shipping_Calc_Pittsburgh": {"avg_ms": 1000, "p95_ms": 2000, "max_error_pct": 1.0},
    "TC09c_Shipping_Confirm":  {"avg_ms": 1000, "p95_ms": 2000, "max_error_pct": 1.0},
    "TC10_Checkout_Payment":   {"avg_ms": 1200, "p95_ms": 2500, "max_error_pct": 1.0},
}

OVERALL_MIN_THROUGHPUT_TPS = 8.0
OVERALL_MAX_ERROR_PCT = 1.0
FORBID_HTTP_500 = True   # hard gate: any single HTTP 500 anywhere fails the build, regardless of overall error rate


def percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


def load_jtl(path):
    """Reads a JMeter CSV-format .jtl file into per-label lists of (elapsed_ms, success, timeStamp, responseCode)."""
    data = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = row.get("label")
            if not label:
                continue
            try:
                elapsed = float(row["elapsed"])
            except (KeyError, ValueError):
                continue
            success = row.get("success", "true").strip().lower() == "true"
            try:
                ts = int(row.get("timeStamp", 0))
            except ValueError:
                ts = 0
            response_code = str(row.get("responseCode", "")).strip()
            data[label].append((elapsed, success, ts, response_code))
    return data


def evaluate(data):
    results = []
    overall_total = 0
    overall_errors = 0
    overall_500_count = 0
    all_timestamps = []

    for label, samples in data.items():
        thresholds = NFR_THRESHOLDS.get(label)
        elapsed_values = sorted(s[0] for s in samples)
        successes = sum(1 for s in samples if s[1])
        total = len(samples)
        errors = total - successes
        error_pct = (errors / total * 100.0) if total else 0.0
        avg_ms = sum(elapsed_values) / total if total else 0.0
        p95_ms = percentile(elapsed_values, 95)
        count_500 = sum(1 for s in samples if s[3] == "500")

        overall_total += total
        overall_errors += errors
        overall_500_count += count_500
        all_timestamps.extend(s[2] for s in samples)

        if thresholds is None:
            results.append({
                "label": label, "total": total, "avg_ms": avg_ms, "p95_ms": p95_ms,
                "error_pct": error_pct, "count_500": count_500, "status": "SKIPPED (no NFR defined)",
            })
            continue

        fail_reasons = []
        if avg_ms > thresholds["avg_ms"]:
            fail_reasons.append(f"avg {avg_ms:.0f}ms > {thresholds['avg_ms']}ms")
        if p95_ms > thresholds["p95_ms"]:
            fail_reasons.append(f"p95 {p95_ms:.0f}ms > {thresholds['p95_ms']}ms")
        if error_pct > thresholds["max_error_pct"]:
            fail_reasons.append(f"errors {error_pct:.2f}% > {thresholds['max_error_pct']}%")
        if FORBID_HTTP_500 and count_500 > 0:
            fail_reasons.append(f"{count_500} HTTP 500 response(s) - zero tolerated")

        results.append({
            "label": label, "total": total, "avg_ms": avg_ms, "p95_ms": p95_ms,
            "error_pct": error_pct, "count_500": count_500,
            "status": "PASS" if not fail_reasons else "FAIL (" + "; ".join(fail_reasons) + ")",
        })

    # Overall throughput: total samples / test duration in seconds
    duration_sec = 0.0
    if len(all_timestamps) >= 2:
        duration_sec = (max(all_timestamps) - min(all_timestamps)) / 1000.0
    overall_tps = (overall_total / duration_sec) if duration_sec > 0 else 0.0
    overall_error_pct = (overall_errors / overall_total * 100.0) if overall_total else 0.0

    overall_fail_reasons = []
    if overall_tps < OVERALL_MIN_THROUGHPUT_TPS:
        overall_fail_reasons.append(f"throughput {overall_tps:.2f} tps < {OVERALL_MIN_THROUGHPUT_TPS} tps")
    if overall_error_pct > OVERALL_MAX_ERROR_PCT:
        overall_fail_reasons.append(f"error rate {overall_error_pct:.2f}% > {OVERALL_MAX_ERROR_PCT}%")
    if FORBID_HTTP_500 and overall_500_count > 0:
        overall_fail_reasons.append(f"{overall_500_count} HTTP 500 response(s) across all transactions - zero tolerated")

    overall = {
        "total_samples": overall_total,
        "duration_sec": duration_sec,
        "throughput_tps": overall_tps,
        "error_pct": overall_error_pct,
        "count_500": overall_500_count,
        "status": "PASS" if not overall_fail_reasons else "FAIL (" + "; ".join(overall_fail_reasons) + ")",
    }
    return results, overall


def print_report(results, overall):
    print("\n" + "=" * 100)
    print(" PERFORMANCE QUALITY GATE REPORT")
    print("=" * 100)
    header = f"{'Transaction':<28}{'Count':>7}{'Avg(ms)':>10}{'P95(ms)':>10}{'Err%':>8}{'HTTP500':>9}   Status"
    print(header)
    print("-" * 100)
    any_fail = False
    for r in sorted(results, key=lambda x: x["label"]):
        print(f"{r['label']:<28}{r['total']:>7}{r['avg_ms']:>10.0f}{r['p95_ms']:>10.0f}{r['error_pct']:>7.2f}%{r.get('count_500', 0):>9}   {r['status']}")
        if r["status"].startswith("FAIL"):
            any_fail = True

    print("-" * 100)
    print(f"{'OVERALL':<28}{overall['total_samples']:>7}{'':>10}{'':>10}{overall['error_pct']:>7.2f}%{overall.get('count_500', 0):>9}   {overall['status']}")
    print(f"  Throughput: {overall['throughput_tps']:.2f} tps  |  Duration: {overall['duration_sec']:.1f}s  |  Total HTTP 500s: {overall.get('count_500', 0)}")
    print("=" * 100 + "\n")

    if overall["status"].startswith("FAIL"):
        any_fail = True
    return any_fail


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 quality_gate.py <results.jtl>")
        sys.exit(2)

    jtl_path = sys.argv[1]
    try:
        data = load_jtl(jtl_path)
    except FileNotFoundError:
        print(f"ERROR: results file not found: {jtl_path}")
        sys.exit(2)

    if not data:
        print(f"ERROR: no samples found in {jtl_path} — check the file is a valid JMeter CSV .jtl")
        sys.exit(2)

    results, overall = evaluate(data)
    failed = print_report(results, overall)

    if failed:
        print("QUALITY GATE: FAILED — one or more NFR thresholds breached. Blocking deployment.")
        sys.exit(1)
    else:
        print("QUALITY GATE: PASSED — all NFR thresholds met.")
        sys.exit(0)


if __name__ == "__main__":
    main()
