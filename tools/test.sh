#!/bin/sh
if [ -n "$1" ]
then
    # optionally pass -We::UserWarning to make exceptions out of warnings
    # -Wi::DeprecationWarning:lxml to ignore warnings in lxml module
    PYTHONPATH=test python -m unittest -f -v "$1"
else
    # When running the entire suite, exit at first failure (-f) in
    # order to not have to wait three minutes.
    python -m unittest discover -v test
    python -V
fi
