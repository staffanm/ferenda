#! /bin/bash
echo "removing old generated files"
#rm -r data/{dv,keyword,mediawiki,sfs,jo,jk,arn,myndprax}/parsed
rm -r data/*/parsed
rm -r data/*/distilled
rm -r data/*/annotations
rm -r data/*/generated
rm -r data/*/toc
set -e  # fail immediately on error
echo "resetting fulltextindex"
./ferenda-build.py devel destroyindex
echo "resetting triplestore"
./ferenda-build.py devel clearstore
echo "updating git sources"
git pull -q
echo "building everything"
./ferenda-build.py all all --processes=7
echo "creating statusreport"
./ferenda-build.py devel statusreport
cd ..
echo "running smoketests"
FERENDA_TESTURL=http://i7-ferenda.lagen.nu/ tools/test.sh integrationLagen
echo "deploying to remote"
fab -H colo.tomtebo.org -f tools/fabfile.py deploy


