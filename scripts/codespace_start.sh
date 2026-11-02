#!/usr/bin/env bash
# Runs automatically when you open the Codespace: make sure there's data, then start the dashboard.
set -u
cd "$(dirname "$0")/.."

if [ ! -f data/footy.db ]; then
  echo "First start: trying to download the latest database from the daily report..."
  REPO="${GITHUB_REPOSITORY:-}"
  if [ -n "$REPO" ]; then
    OWNER="${REPO%%/*}"; NAME="${REPO##*/}"
    URL="https://$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/${NAME}/download/footy.db"
    mkdir -p data
    curl -fsSL "$URL" -o data/footy.db 2>/dev/null && echo "Downloaded $URL" || rm -f data/footy.db
  fi
fi

if [ ! -f data/footy.db ]; then
  echo "No database yet - fetching Premier League + FPL data (a few minutes)..."
  python -m footy update -l E0 --sources football_data,understat || true
fi

echo "Starting dashboard on port 8501 (tap 'Open in Browser' if it doesn't open by itself)"
nohup python -m streamlit run app/dashboard.py --server.headless true --server.port 8501 \
  --server.enableCORS false --server.enableXsrfProtection false > /tmp/dashboard.log 2>&1 &
