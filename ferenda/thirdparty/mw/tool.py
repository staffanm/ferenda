# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, division
from __future__ import absolute_import, unicode_literals

import argparse
import sys
from collections import OrderedDict
from functools import wraps, partial
from timeit import Timer

from lxml import etree

import smc.mw as mw


def profiled(stage):
    def _decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            result = [None]
            def run():
                result[0] = fn(*args, **kwargs)
            timer = Timer(stmt=run)
            time = timer.timeit(number=1)
            profile_data = kwargs.get("profile_data", None)
            if profile_data is not None:
                profile_data[stage] = {"stage": stage, "time": time * 1000}
            return result[0]
        return wrapper
    return _decorator


@profiled("plain")
def run_plain(text, filename=None, start=None, profile_data=None,
              trace=False):
    if start is None:
        start = "document"
    return mw.Preprocessor().reconstruct(None, text)

@profiled("preprocessor")
def run_preprocessor(text, filename=None, start=None, profile_data=None,
                     trace=False):
    if start is None:
        start = "document"
    return mw.Preprocessor()._expand(None, text)


@profiled("parser")
def run_parser(text, filename=None, start=None, profile_data=None,
               trace=False, headings=None):
    if start is None:
        start = "document"
    parser = mw.Parser(parseinfo=False,  whitespace='', nameguard=False)
    ast = parser.parse(text, start, filename=filename,
                       semantics=mw.Semantics(parser, headings=headings), trace=trace,
                       nameguard=False, whitespace='')
    if sys.version < '3':
        text = etree.tostring(ast)
    else:
        text = etree.tostring(ast, encoding=str)
    return text


def process(input=None, output=None, start=None, stages=None,
            profile=False, trace=False):
    if input is None:
        filename = "-"
        input = sys.stdin.read()
    else:
        filename = input
        with open(input, "r") as fh:
            input = fh.read().decode("UTF-8")

    headings = None
    profile_data = OrderedDict()
    # If all stages are run, start only applies to the parser state.
    if stages is None:
        result, headings = run_preprocessor(input, filename=filename,
                                  profile_data=profile_data)
    elif stages == "preprocessor":
        result, headings = run_preprocessor(input, filename=filename, start=start,
                                  profile_data=profile_data)
    elif stages == "plain":
        result = run_plain(input, filename=filename, start=start,
                           profile_data=profile_data)
    else:
        result = input

    if stages is None or stages == "parser":
        result = run_parser(result, filename=filename, start=start,
                            profile_data=profile_data, trace=trace, headings=headings)

    if profile:
        for data in profile_data.values():
            print("{stage}: {time:.3f} msecs".format(**data), file=sys.stderr)

    if sys.version < '3':
        result = result.encode("UTF-8")
    if output is None:
        sys.stdout.write(result)
    else:
        with open(output, "w") as fh:
            fh.write(result)


def parse_args():
    parser = argparse.ArgumentParser(description="Process a MediaWiki formatted text.")
    stages_group = parser.add_mutually_exclusive_group()
    stages_group.add_argument("-p", action="store_const",
                              dest="stages", const="preprocessor",
                              help="only run the preprocessor (not the parser)")
    stages_group.add_argument("-r", action="store_const",
                              dest="stages", const="plain",
                              help="run preprocessor, then reconstruct")
    stages_group.add_argument("-P", action="store_const",
                              dest="stages", const="parser",
                              help="only run the parser (not the preprocessor)")

    parser.add_argument("-s", metavar="RULE", dest="start",
                        help="start parsing at the given rule")
    parser.add_argument("-x", action="store_true", dest="profile", default=False,
                        help="print profile information on stderr")

    parser.add_argument("-o", metavar="OUTFILE", dest="output",
                        help="write output to OUTFILE instead of stdout")
    parser.add_argument("-t", action="store_true", dest="trace", default=False,
                        help="start parsing at the given rule")

    parser.add_argument("input", metavar="INFILE", nargs="?",
                        help="input file to process instead of stdin")
    return parser.parse_args()

                                     
def main():
    args = parse_args()
    process(**vars(args))


if __name__ == "__main__":
    main()
