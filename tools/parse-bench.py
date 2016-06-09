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
from ferenda import util
  
from test.functionalLegalRef import TestLegalRef

class LegalRefTest(object):
    def __init__(self, alias):
        # setup 
        parsetype = alias.split("/")[1]
        self.parser = LegalRef({'SFS': LegalRef.LAGRUM,
                                'Short': LegalRef.KORTLAGRUM,
                                'DV': LegalRef.RATTSFALL,
                                'Regpubl': LegalRef.FORARBETEN,
                                'EGLag': LegalRef.EULAGSTIFTNING,
                                'ECJ': LegalRef.EGRATTSFALL}[parsetype]
        # attempt to run a classmethod for one class and 
        # have it modifying another. This can't possibly 
        # work for a bound method?
        TestLegalRef.setUpClass(self.__class__)
       
    def createtest(basefile, basedir):
        # FIXME: TestLegalRef._parse should be MOST of ._test_parser, without 
        # the ending assertEqual (and preferably w/o serialize as well)
        testfile = basedir + basefile + ".txt"
        start = time.time()
        nodes = TestLegalRef._parse(self, testfile, self.parser)
        elapsed = time.time() - start
        return elapsed, extractrefs(nodes)

    timetest = createtest


class RepoTest(object):

    def __init__(config, alias):
        repoconfig = getattr(config, alias)
        classname = getattr(repoconfig, 'class')
        repocls = _load_class(classname)
        self.repo = repocls()
        self.repo.config = repoconfig


    def timetest(basefile, basedir):
        serialized_path = self.repo.store.serialized_path(basefile) + ".unparsed"
        serialized_path.replace(self.repo.store.datadir, basedir)
        with codecs.open(serialized_path, "r", encoding="utf-8") as fp:
            doc = deserialize(fp.read(), format="json")
        refparser = self.repo.refparser
        start = time.time()
        refparser.parse_recursive(doc)
        elapsed = time.time() - start
        return elapsed, extractrefs(doc)

    def createtest(basefile, basedir):
        print("parsing %s/%s" % (self.repo.alias, basefile))
        self.repo.config.force = True # make this dependent on whether 
        self.repo.parse(basefile)
        # refs = refs_from_graph(Graph().parse(repo.store.distilled_path(basefile)))
        elapsed, refs = timetest(repo, basefile, basedir)
        return elapsed, refs

def extractrefs(node):
    if isinstance(node, Link):
        return [node.uri]
    elif isinstance(node, str):
        return []
    else:
        res = []
        try:
            for subnode in node:
                res.extend(extractrefs(subnode))
        except TypeError:  # subnode object is not iterable
            pass
        return res

def getconfig(basedir):
    defaults = dict(DEFAULT_CONFIG)
    os.environ['FERENDA_SERIALIZEUNPARSED'] = basedir
    return _load_config(_find_config_file())


def time_legalref_test(alias, test) # 
    # leverage the setUpClass and _test_parser methods from test/integrationLegalRef somehow
    parser = integrationLegalRef.setUpClass()
    start = time.time()
    nodes = integrationLegalRef._testParse(test, parser)
    elapsed = time.time() - start
    return elapsed, refs_from_graph(nodes)

    
def createtestsuite(testsuitefile):
    with open(testsuitefile) as fp:
        testsuite = json.load(fp)
    basedir = os.path.dirname(testsuitefile)
    config = getconfig(basedir)
    baseline = {}
    for alias, basefiles in testsuite.items():
        if alias.startswith("legalref/"):
            tester = LegalRefTest(alias)
            if not basefiles:
                basefiles = (x[11+len(alias):-4] for x in util.listdir("test/files/" + alias, suffix=".txt"))
        else:
            tester = RepoTest(config, alias)
        baseline[alias] = []
        for basefile in basefiles:
            elapsed, refgraph = tester.createtest(basefile, basedir)
            baseline[alias].append({'basefile': basefile,
                                    'elapsed': elapsed,
                                    'refgraph': refgraph})

    baselinefile = testsuitefile.replace(".json", ".baseline.json")
    if os.path.exists(baseline):
        with open(baselinefile) as fp:
            existingbaseline = json.load(fp)
        # TODO: copy elapsed values from existingbaseline to baseline
    with open(baselinefile, "w") as fp:
        json.dump(baseline, fp, indent=2)

def evaltestsuite(testsuitefile):
    with open(testsuitefile) as fp:
        testsuite = json.load(fp)
    basedir = os.path.dirname(testsuitefile)
    config = getconfig(basedir)
    results = {}
    for alias, basefiles in testsuite.items():
        if alias.startswith("legalref/"):
            tester = LegalRefTest(alias)
	else:
            tester = RepoTest(config, alias)
        results[alias] = []
        for basefile in basefiles:
            print("testing %s/%s" % (alias, basefile))
            elapsed, refgraph = tester.timetest(basefile, basedir)
            results[alias].append({'basefile': basefile,
                                   'elapsed': elapsed,
                                   'refgraph': refgraph})
    baselinefile = testsuitefile.replace(".json", ".baseline.json")
    with open(baselinefile) as fp:
        baselinefile = json.load(fp)
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
    if len(sys.argv) < 2:
        print("USAGE: %s testsuite.json [--createtest]" % sys.argv[0])
        sys.exit(1)
    testsuite = sys.argv[1]
    assert testsuite.endswith(".json")
    if len(sys.argv) > 2 and sys.argv[1] == "--createtest":
        createtestsuite(testsuite)
    else:
        evaltestsuite(testsuite)
