#!/bin/bash
cd "$(dirname "$0")"
./runarms.sh none 3 fence fence-dir mip-dir
echo "CLEAN DONE"
./runarms.sh important_instructions 4 fence fence-dir
echo "ATTACK DONE"
