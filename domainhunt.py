#!/usr/bin/env python3
"""
domainhunt.py - find expired / previously used domains obtainable for under a budget.
Standard library only (Python 3.8+).
"""
import argparse
import csv
import itertools
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

TLD_PRICE = {
    "com": 11, "net": 13, "org": 12, "io": 40, "co": 28, "ai": 80, "app": 15,
    "dev": 13, "xyz": 3, "info": 4, "biz": 15, "us": 7, "me": 17, "tv": 35,
    "site": 3, "online": 4, "tech": 8, "store": 4, "club": 4, "pro": 8,
}
UA = {"User-Agent": "domainhunt/1.0 (research tool)"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")


def http_json(url, timeout=15, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            if e.code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return e.code, None
        except Exception:
            time.sleep(1 + attempt)
    return None, None


def rdap(domain):
    code, data = http_json(f"https://rdap.org/domain/{domain}")
    if code == 404:
        return {"state": "available", "registered": None, "expires": None, "statuses": []}
    if not data:
        return {"state": "unknown", "registered": None, "expires": None, "statuses": []}
    statuses = [s.lower().replace(" ", "") for s in data.get("status", [])]
    reg = exp = None
    for ev in data.get("events", []):
        act, date = ev.get("eventAction", ""), ev.get("eventDate", "")
        if act == "registration":
            reg = date
        elif act == "expiration":
            exp = date

    def parse(d):
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return None

    exp_dt = parse(exp) if exp else None
    now = datetime.now(timezone.utc)
    if any("pendingdelete" in s for s in statuses):
        state = "dropping"
    elif any("redemption" in s for s in statuses):
        state = "dropping"
    elif exp_dt and exp_dt < now:
        state = "expired"
    else:
        state = "registered"
    return {"state": state, "registered": reg, "expires": exp, "statuses": statuses}


def wayback(domain):
    url = ("https://web.archive.org/cdx/search/cdx?url=" + domain +
           "&output=json&fl=timestamp&collapse=timestamp:6&limit=1000")
    code, data = http_json(url, timeout=25)
    if not data or len(data) < 2:
        return 0, None, None
    stamps = [row[0] for row in data[1:]]
    return len(stamps), int(stamps[0][:4]), int(stamps[-1][:4])


def age_years(reg):
    if not reg:
        return None
    try:
        d = datetime.fromisoformat(reg.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - d).days / 365.25, 1)
    except Exception:
        return None


def score(row):
    s = 0.0
    s += min(row["snapshots"], 120) * 0.5
    s += min(row["age_years"] or (row["last_year"] and row["first_year"] and
                                  (row["last_year"] - row["first_year"])) or 0, 20) * 2
    name = row["domain"].split(".")[0]
    s += max(0, 12 - len(name)) * 1.5
    s -= name.count("-") * 6 + sum(c.isdigit() for c in name) * 3
    s += {"com": 15, "net": 6, "org": 6, "io": 8, "co": 4}.get(row["domain"].split(".")[-1], 0)
    s += {"available": 0, "dropping": 8, "expired": 5}.get(row["state"], 0)
    return round(s, 1)


def analyze(domain, given_price, backorder_fee):
    tld = domain.rsplit(".", 1)[-1]
    info = rdap(domain)
    n, first, last = wayback(domain)
    est = None
    if given_price is not None:
        est = given_price
    elif info["state"] == "available":
        est = TLD_PRICE.get(tld)
    elif info["state"] in ("dropping", "expired"):
        base = TLD_PRICE.get(tld)
        est = None if base is None else base + backorder_fee
    row = {
        "domain": domain, "state": info["state"], "est_price": est,
        "age_years": age_years(info["registered"]), "expires": (info["expires"] or "")[:10],
        "snapshots": n, "first_year": first, "last_year": last,
        "used_before": "yes" if n >= 3 else "no",
    }
    row["score"] = score(row)
    return row


def load_candidates(path):
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        if "," in sample or "\t" in sample:
            rows = list(csv.reader(f, dialect=csv.Sniffer().sniff(sample, ",\t;")))
            head = [h.strip().lower() for h in rows[0]]
            has_head = any(h in ("domain", "name", "domain name") for h in head)
            di = next((i for i, h in enumerate(head) if h in ("domain", "name", "domain name")), 0)
            pi = next((i for i, h in enumerate(head) if "price" in h or "bid" in h), None)
            for r in rows[1 if has_head else 0:]:
                if not r:
                    continue
                d = r[di].strip().lower()
                p = None
                if pi is not None and pi < len(r):
                    m = re.search(r"[\d.]+", r[pi].replace(",", ""))
                    p = float(m.group()) if m else None
                out[d] = p
        else:
            for line in f:
                d = line.strip().lower()
                if d:
                    out[d] = None
    return out


def generate(keywords, tlds, combine):
    names = list(keywords)
    if combine:
        names += [a + b for a, b in itertools.permutations(keywords, 2)]
    return {f"{n}.{t}": None for n in names for t in tlds}


def main():
    ap = argparse.ArgumentParser(description="Find expired/used domains under a budget.")
    ap.add_argument("--file")
    ap.add_argument("--keywords", nargs="*", default=[])
    ap.add_argument("--tlds", nargs="*", default=["com"])
    ap.add_argument("--combine", action="store_true")
    ap.add_argument("--max-price", type=float, default=50.0)
    ap.add_argument("--backorder-fee", type=float, default=20.0)
    ap.add_argument("--min-snapshots", type=int, default=0)
    ap.add_argument("--states", nargs="*", default=["available", "dropping", "expired", "registered"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="domain_hits.csv")
    a = ap.parse_args()

    cands = {}
    if a.file:
        cands.update(load_candidates(a.file))
    if a.keywords:
        cands.update(generate([k.lower() for k in a.keywords], a.tlds, a.combine))
    cands = {d: p for d, p in cands.items() if DOMAIN_RE.match(d)}
    if not cands:
        sys.exit("No valid candidates. Use --file or --keywords.")

    print(f"Checking {len(cands)} domains...", file=sys.stderr)
    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(analyze, d, p, a.backorder_fee): d for d, p in cands.items()}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"  ! {futs[fut]}: {e}", file=sys.stderr)
            if i % 10 == 0:
                print(f"  {i}/{len(cands)}", file=sys.stderr)

    hits = [r for r in results
            if r["state"] in a.states
            and r["est_price"] is not None and r["est_price"] <= a.max_price
            and r["snapshots"] >= a.min_snapshots]
    hits = [r for r in hits if r["state"] != "registered" or cands.get(r["domain"]) is not None]
    hits.sort(key=lambda r: r["score"], reverse=True)

    cols = ["domain", "state", "est_price", "score", "used_before", "snapshots",
            "first_year", "last_year", "age_years", "expires"]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r[c] for c in cols} for r in hits)

    print(f"\n{'DOMAIN':<28}{'STATE':<11}{'$':>6}{'SCORE':>7}{'SNAPS':>7}  YEARS")
    for r in hits[:40]:
        yrs = f"{r['first_year']}-{r['last_year']}" if r["first_year"] else "-"
        print(f"{r['domain']:<28}{r['state']:<11}{r['est_price']:>6.0f}{r['score']:>7}{r['snapshots']:>7}  {yrs}")
    print(f"\n{len(hits)} hit(s) under ${a.max_price:.0f} -> {a.out}")
    print("Reminder: vet for spam/blacklist history before buying. Prices are estimates.")


if __name__ == "__main__":
    main()
