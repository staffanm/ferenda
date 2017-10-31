#!/bin/bash

while true
do
    git pull
    ./ferenda-build.py all buildclient --serverhost=192.168.1.128 --processes=8
done
      
