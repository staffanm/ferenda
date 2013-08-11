#!/bin/sh
PYTHONWARNINGS=i coverage run --source ferenda --omit "ferenda/thirdparty/*py" -m unittest discover test
coverage html
open htmlcov/index.html
