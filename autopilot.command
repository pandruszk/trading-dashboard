#!/bin/bash
# Double-click to put the portfolio on Kell autopilot.
# First launch closes existing positions and builds the Kell book, then it
# runs the daily/monthly schedule. Ctrl+C (or close this window) to stop.
cd "$(dirname "$0")"
exec .venv/bin/python3 autopilot.py run
