name: Tor Playwright Traffic (matrix)

on:
  workflow_dispatch:
  # schedule:
  #   - cron: '0 * * * *'  # optional

jobs:
  simulate:
    runs-on: ubuntu-latest
    timeout-minutes: 1440   # attempt to allow up to 24 hours (subject to account limits)
    strategy:
      matrix:
        port: [9050, 9051, 9052, 9053]  # only 4 parallel jobs for free tier


    steps:
      - uses: actions/checkout@v4

      - name: Set up system packages and Python
        run: |
          sudo apt-get update -y
          sudo apt-get install -y tor curl wget unzip build-essential
          python -m pip install --upgrade pip
          pip install playwright fake-useragent
          python -m playwright install --with-deps chromium

      - name: Debug info
        run: |
          echo "Runner matrix port = ${{ matrix.port }}"
          echo "This runner public IP (for debug) will be:"
          curl -s https://ifconfig.me || true

      - name: Run simulator
        env:
          TOR_BASE_PORT: ${{ matrix.port }}
          CONCURRENCY: 2
        run: |
          python main.py

      - name: Upload logs on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-logs-${{ matrix.port }}
          path: |
            **/*.log
            main.py
