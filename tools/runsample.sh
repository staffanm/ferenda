#!/bin/sh

echo "Removing everything under ./data/ in 5 seconds, hit Ctrl-C to abort"
sleep 5
rm -rf data
./ferenda-build.py devel clearstore
./ferenda-build.py devel destroyindex
./ferenda-build.py devel samplerepos bigdata
./ferenda-build.py all all

