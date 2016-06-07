#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
# 1 stdlib
import sys
import os
import time
import codecs
import json

# 2 third party
from rdflib import Graph
from rdflib.namespace import DCTERMS

# 3 own code
sys.path.append(os.path.normpath(os.getcwd() + os.sep + os.pardir))
# FIXME: As we seem to need these functions (both here and partially
# in devel.py, maybe they shouldn't be marked private?
from ferenda.manager import _load_class, _load_config, _find_config_file, DEFAULT_CONFIG
from ferenda.elements import deserialize, Link

TESTSUITE = {
#    'dirtrips': ['1995:91', '2002:31'],
#    'souregeringen': ['1997:39', '2004:6'],
#    'propregeringen': ['1998/99:44', '2005/06:173'],
    'sfs': ["1998:204", "1998:1191"],
    'dv': ['HGO/B747-00',  # RH 2004:51
           'HDO/Ö6229-14', # NJA 2015 s 180
           'HFD/4453-10',  # HFD 2012 ref 21
           'ADO/2012-87',   # AD 2012 nr 87
           'HFD/6894-13',  # HFD 2014 ref 32
           'HDO/T2807-12', # NJA 2013 s 1046
           ],
#    'myndfs': ['difs/2013:1', 'difs/2010:1']
}

def initialize_repo(config, alias):
    repoconfig = getattr(config, alias)
    classname = getattr(repoconfig, 'class')
    repocls = _load_class(classname)
    repo = repocls()
    repo.config = repoconfig
    return repo
    
def extractrefs(node):
    if isinstance(node, Link):
        return [node.uri]
    elif isinstance(node, str):
        return []
    else:
        res = []
        for subnode in node:
            res.extend(extractrefs(subnode))
        return res
            

def timetest(repo, basefile):
    serialized_path = repo.store.serialized_path(basefile) + ".unparsed"
    # 
    with codecs.open(serialized_path, "r", encoding="utf-8") as fp:
        doc = deserialize(fp.read(), format="json")
    refparser = repo.refparser
    start = time.time()
    refparser.parse_recursive(doc)
    elapsed = time.time() - start
    return elapsed, extractrefs(doc)


# - A function (createtest) taking a docrepo alias and basefile. Runs
#   parse on that file, injects something that prior to parseref dumps
#   the document as JSON, then post parseref dumps triples to n3
def createtest(repo, basefile):
    print("parsing %s/%s" % (repo.alias, basefile))
    repo.config.force = True # make this dependent on whether 
    repo.parse(basefile)
    # refs = refs_from_graph(Graph().parse(repo.store.distilled_path(basefile)))
    elapsed, refs = timetest(repo, basefile)
    return elapsed, refs

def getconfig():
    defaults = dict(DEFAULT_CONFIG)
    os.environ['FERENDA_SERIALIZEUNPARSED'] = "True"
    return _load_config(_find_config_file())

    
def createtestsuite():
    config = getconfig()
    baseline = {}
    for alias, basefiles in TESTSUITE.items():
        repo = initialize_repo(config, alias)
        baseline[alias] = []
        for basefile in basefiles:
            elapsed, refgraph = createtest(repo, basefile)
            baseline[alias].append({'basefile': basefile,
                                    'elapsed': elapsed,
                                    'refgraph': refgraph})
    with open("baseline.json", "w") as fp:
        json.dump(baseline, fp, indent=2)

def evaltestsuite():
    config = getconfig()
    results = {}
    for alias, basefiles in TESTSUITE.items():
        repo = initialize_repo(config, alias)
        results[alias] = []
        for basefile in basefiles:
            print("testing %s/%s" % (alias, basefile))
            elapsed, refgraph = timetest(repo, basefile)
            results[alias].append({'basefile': basefile,
                                   'elapsed': elapsed,
                                   'refgraph': refgraph})
    with open("baseline.json") as fp:
        baseline = json.load(fp)
    compare(baseline, results)


def compare(baseline, results):
    # sfs: 1 test in 24.4 seconds (95% of baseline), 0 tests had errors
    # arn: 4 tests in 4.04 seconds (105% of baseline), 2 tests had errors:
    #     13242-043: ref #14: expected http://lagen.nu/sfs/1993:323, got http://lagen.nu/sfs/1993:323#P4 
    #     13242-043: ref #42: expected http://lagen.nu/sfs/1993:323, got None
    total_baseline = 0
    total_results = 0
    total_errors = []
    for alias in baseline:
        baseline_time = 0
        results_time = 0
        errors = []
        for i in range(len(baseline[alias])):
            assert baseline[alias][i]['basefile'] == results[alias][i]['basefile']
            baseline_time += baseline[alias][i]['elapsed']
            results_time += results[alias][i]['elapsed']
            if baseline[alias][i]['refgraph'] != baseline[alias][i]['refgraph']:
                #matcher = difflib.SequenceMatcher(a=baseline[alias][i]['refgraph'],
                #                                  b=baseline[alias][i]['refgraph'])
                for j in range(len(baseline[alias][i]['refgraph'])):
                    if baseline[alias][i]['refgraph'][j] != results[alias][i]['refgraph'][j]:
                        errors.append("   %s: ref %s, expected %s, got %s" % (baseline[alias][i]['basefile'], j,
                                                                              baseline[alias][i]['refgraph'][j],
                                                                              results[alias][i]['refgraph'][j]))
#        from pudb import set_trace; set_trace()
        percent = (results_time / baseline_time) * 100
        print("%s: %s tests in %.2f seconds (%.2f percent of baseline), %s tests had errors" %
              (alias, len(baseline[alias]), results_time, percent, len(errors)))
        for error in errors:
            print(error)
        total_baseline += baseline_time
        total_results += results_time
        total_errors += errors
    total_percent = (total_results / total_baseline) * 100
    print("Total: %.2f seconds (%.2f percent of baseline), %s tests had errors" %
          (total_results, total_percent, len(total_errors)))
                
    
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == "--createtest":
        createtestsuite()
    else:
        evaltestsuite()
    
