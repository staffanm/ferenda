#!/usr/bin/env python
import sys
import re
from collections import defaultdict

def analyze_log(filename, listerrors=False):
    modules = defaultdict(int)
    locations = defaultdict(int)
    locationmsg = {}
    errors = []
    with open(filename) as fp:
        for line in fp:
            try:
                timestamp, module, level, message = line.split(" ", 3)
            except ValueError:
                continue
            if level == "ERROR":
                if module == "root":
                    module = message.split(" ", 1)[0]
                modules[module] += 1
            m = re.search("\([\w/]+.py:\d+\)", message)
            if m:
                location = m.group(0)
                locations[location] += 1
                if location not in locationmsg:
                    locationmsg[location] = message.strip()
            if listerrors:
                m = re.match("([\w\.]+) (\w+) ([^ ]*) failed", message)
                if m:
                    errors.append((m.group(1), m.group(3)))
    if listerrors:
        for repo, basefile in errors:
            print(repo,basefile)
    else:
        print("Top error modules:")
        printdict(modules)
        print("Top error messages:")
        printdict(locations, locationmsg) 

def printdict(d, labels=None):
    # prints out a dict with int values, sorted by these
    for k in sorted(d, key=d.get, reverse=True):
        if labels:
            lbl = labels[k]
        else:
            lbl = k
        print("%4d %s" % (d[k], lbl))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("USAGE: %s logfilename" % sys.argv[0])
    else:
        listerrors = False
        if len(sys.argv) > 2 and sys.argv[2] == "--listerrors":
            listerrors = True
        analyze_log(sys.argv[1], listerrors)
