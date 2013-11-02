#!/bin/sh
if [ -n "$1" ]
then
    PYTHONPATH=test python -Wi -m unittest -v  "$1"
else
    # When running the entire suite, exit at first failure (-f) in
    # order to not have to wait three minutes.
    python -Wi -m unittest discover -v -f test
    python -V
fi
