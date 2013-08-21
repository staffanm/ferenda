#!/bin/sh
if [ -n "$1" ]
then
    PYTHONWARNINGS=i PYTHONPATH=test coverage run --include "ferenda/*py" --omit "ferenda/thirdparty/*" -m unittest -v "$1"
else
    PYTHONWARNINGS=i coverage run --include "ferenda/*py" --omit "ferenda/thirdparty/*" -m unittest discover test
fi 
coverage html
open htmlcov/index.html
