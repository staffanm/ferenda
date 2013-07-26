#!/bin/sh
PYTHONWARNINGS=i coverage run --source ferenda --omit "*.xhtml" -m unittest discover test
coverage html
open htmlcov/index.html
