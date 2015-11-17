#!/bin/sh
python -Wi -m unittest discover -f -v -p "integration*py" test
