#!/bin/bash
exec 2>&1
set -ex
bash $SCRIPTS/create_mutated.sh -o mutated.il
ln -s ../../test_bmc.sby .
sby -f test_bmc.sby
awk "{ print 1, \$1; }" test_bmc/status >> output.txt
exit 0
