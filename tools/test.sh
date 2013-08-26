#!/bin/sh
if [ -n "$1" ]
then
    PYTHONPATH=test python -Wi -m unittest -v  "$1"
else
    python -Wi -m unittest discover -v -f test
    # python -Wi -m unittest discover  test
    python -V
fi    
