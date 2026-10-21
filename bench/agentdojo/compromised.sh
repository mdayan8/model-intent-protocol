#!/bin/bash
# Worst-case ablation: 5 user tasks per suite x every injection task, attacked.
PY=~/.mipbench_scratch/adenv/bin/python
cd "$(dirname "$0")"
mkdir -p ../results/agentdojo/logs
for ARM in baseline fence-dir; do
  ( for S in banking slack travel workspace; do
      MIP_COMPROMISED=1 PYTHONUNBUFFERED=1 $PY run.py --arm $ARM --suite $S --attack important_instructions --limit-user 5 --workers 3 2>&1 | grep --line-buffered -v Warning
    done ) > ../results/agentdojo/logs/$ARM-compromised.log 2>&1 &
done
wait
echo "COMPROMISED DONE"
