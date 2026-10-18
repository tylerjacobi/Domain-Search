name: Domain Hunt

on:
  workflow_dispatch:
    inputs:
      keywords:
        description: "Keywords, space separated (blank = candidates.txt only)"
        default: "solar vault drone"
      tlds:
        description: "TLDs, space separated"
        default: "com net io"
      max_price:
        description: "Max price in USD"
        default: "50"
      min_snapshots:
        description: "Min Wayback months (proves past use)"
        default: "0"
      combine:
        description: "Also try keyword pairs"
        type: boolean
        default: true

permissions:
  contents: write

jobs:
  hunt:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run domainhunt
        env:
          KEYWORDS: ${{ inputs.keywords || '' }}
          TLDS: ${{ inputs.tlds || 'com' }}
          MAX_PRICE: ${{ inputs.max_price || '50' }}
          MIN_SNAPS: ${{ inputs.min_snapshots || '0' }}
          COMBINE: ${{ inputs.combine || 'false' }}
        run: |
          ARGS="--max-price $MAX_PRICE --min-snapshots $MIN_SNAPS --out results.csv"
          if [ -n "$KEYWORDS" ]; then ARGS="$ARGS --keywords $KEYWORDS --tlds $TLDS"; fi
          if [ "$COMBINE" = "true" ]; then ARGS="$ARGS --combine"; fi
          if [ -f candidates.txt ]; then ARGS="$ARGS --file candidates.txt"; fi
          python domainhunt.py $ARGS 2>&1 | tee run.log

      - name: Show results on the run summary page
        if: always()
        run: |
          python - <<'EOF'
          import csv, os
          out = open(os.environ["GITHUB_STEP_SUMMARY"], "a")
          try:
              rows = list(csv.reader(open("results.csv")))
          except FileNotFoundError:
              out.write("No results.csv produced. Check the run log.\n")
              raise SystemExit
          if len(rows) < 2:
              out.write("No domains matched the filters.\n")
          else:
              out.write("| " + " | ".join(rows[0]) + " |\n")
              out.write("|" + "---|" * len(rows[0]) + "\n")
              for r in rows[1:51]:
                  out.write("| " + " | ".join(r) + " |\n")
          EOF

      - name: Save results to the repo
        if: always()
        run: |
          git config user.name "domainhunt-bot"
          git config user.email "domainhunt-bot@users.noreply.github.com"
          git add results.csv run.log || true
          git commit -m "domainhunt results $(date -u +%F)" || echo "nothing to commit"
          git push || true
