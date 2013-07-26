#!/bin/sh
./ferenda-setup.py netstandards
cd netstandards
./ferenda-build.py ferenda.sources.tech.RFC enable
./ferenda-build.py ferenda.sources.tech.W3Standards enable
./ferenda-build.py all all --downloadmax=50
./ferenda-build.py all runserver &
open http://localhost:8000/

