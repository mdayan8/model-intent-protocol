#!/bin/bash
# Usage: ./runarms.sh <attack> <workers> <arm> [<arm> ...]   Resumable.
PY=~/.mipbench_scratch/adenv/bin/python
ATTACK=$1; W=$2; shift 2
cd "$(dirname "$0")"
mkdir -p ../results/agentdojo/logs
for ARM in "$@"; do
  ( for S in banking slack travel workspace; do
      PYTHONUNBUFFERED=1 $PY run.py --arm $ARM --suite $S --attack $ATTACK --workers $W 2>&1 | grep --line-buffered -v Warning
    done ) > ../results/agentdojo/logs/$ARM-$ATTACK.log 2>&1 &
done
wait
echo "ALL DONE $ATTACK $*"
