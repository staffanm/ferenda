#!/bin/sh
python -Wi -m unittest discover -v -f -p "integration*py" test
