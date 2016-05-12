#!/usr/bin/env python
import sys
import re
from collections import defaultdict

def analyze_log(filename):
    modules = defaultdict(int)
    locations = defaultdict(int)
    locationmsg = {}
    with open(filename) as fp:
        for line in fp:
            try:
                timestamp, module, level, message = line.split(" ", 3)
                if module == "2015:98:":
                    import pudb; pu.db
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
    if len(sys.argv) != 2:
        print("USAGE: %s logfilename" % sys.argv[0])
    else:
        analyze_log(sys.argv[1])
