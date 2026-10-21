#!/bin/bash
# Full AgentDojo v1.2.2 run. Resumable: rerun to continue.  Usage: ./runall.sh <attack> <workers>
PY=~/.mipbench_scratch/adenv/bin/python
ATTACK=${1:-none}; W=${2:-4}
cd "$(dirname "$0")"
mkdir -p ../results/agentdojo/logs
for ARM in baseline mip mip-approve; do
  ( for S in banking slack travel workspace; do
      $PY run.py --arm $ARM --suite $S --attack $ATTACK --workers $W 2>&1 | grep -v Warning
    done ) > ../results/agentdojo/logs/$ARM-$ATTACK.log 2>&1 &
done
wait
echo "ALL DONE $ATTACK"
