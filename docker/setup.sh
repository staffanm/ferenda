#!/bin/bash
set -e

cd /usr/share

if [ -f site/ferenda.ini ]; then
    echo "site/ferenda.ini exists, not setting up a new site"
fi 

if [ ! -f site/ferenda.ini ]; then
    /usr/share/.virtualenv/bin/activate
    FERENDA_SET_TRIPLESTORE_LOCATION=1 FERENDA_SET_FULLTEXTINDEX_LOCATION=1 /usr/share/.virtualenv/bin/python ferenda-setup.py site --unattended --force
    cp /tmp/docker/ferenda-build.py site/
    cd site
    mkdir -p data/dv/generated/
    touch data/dv/generated/uri.map
    ./ferenda-build.py ferenda.Devel enable
    # maybe enable other modules as needed?
    ./ferenda-build.py all makeresources
    ./ferenda-build.py all frontpage
fi

exec "$@"
