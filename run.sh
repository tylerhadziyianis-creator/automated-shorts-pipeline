#!/bin/bash
export PATH="/Library/Frameworks/Python.framework/Versions/3.14/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")"

pkill -f "aita-bot/main.py" 2>/dev/null
sleep 2

while true; do
    echo "[$(date)] Starting AITA bot..."
    caffeinate -dims &
    CAFFEINATE_PID=$!
    python3 main.py
    EXIT_CODE=$?
    kill $CAFFEINATE_PID 2>/dev/null
    echo "[$(date)] Bot stopped (exit code $EXIT_CODE). Restarting in 10 seconds..."
    sleep 10
done
