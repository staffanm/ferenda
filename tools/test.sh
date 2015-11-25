#!/bin/sh
if [ -n "$1" ]
then
    # optionally pass -We::UserWarning to make exceptions out of warnings
    PYTHONPATH=test python -m unittest -v -f "$1"
else
    # When running the entire suite, exit at first failure (-f) in
    # order to not have to wait three minutes.
    python -Wi -m unittest discover -v test
    python -V
fi
