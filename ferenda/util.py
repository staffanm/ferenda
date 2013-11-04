# -*- coding: utf-8 -*-
"""General  library of small utility functions."""
from __future__ import unicode_literals

import codecs
import datetime
import filecmp
import hashlib
import locale
import os
import pkg_resources
import posixpath
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from email.utils import parsedate_tz
from ast import literal_eval

import six
from six.moves.urllib_parse import urlsplit, urlunsplit
from six import text_type as str
from six import binary_type as bytes


from . import errors

# We should reorganize this, maybe in util.File, util.String, and so on...


# util.Namespaces
# Set up common namespaces and suitable prefixes for them
ns = {'dc': 'http://purl.org/dc/elements/1.1/',
      'dct': 'http://purl.org/dc/terms/',
      'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
      'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
      'skos': 'http://www.w3.org/2004/02/skos/core#',
      'xsd': 'http://www.w3.org/2001/XMLSchema#',
      'foaf': 'http://xmlns.com/foaf/0.1/',
      'owl': 'http://www.w3.org/2002/07/owl#',
      'xhv': 'http://www.w3.org/1999/xhtml/vocab#',
      'prov': 'http://www.w3.org/ns/prov-o/',
      'bibo': 'http://purl.org/ontology/bibo/',
      }
"""A mapping of well-known prefixes and their corresponding namespaces. Includes ``dc``, ``dct``, ``rdfs``, ``rdf``, ``skos``, ``xsd``, ``foaf``, ``owl``, ``xhv``, ``prov`` and ``bibo``."""

# util.File


def mkdir(newdir):
    """Like :py:func:`os.makedirs`, but doesn't raise an exception if the directory already exists."""
    if not os.path.exists(newdir):
        os.makedirs(newdir)

# util.File


def ensure_dir(filename):
    """Given a filename (typically one that you wish to create), ensures that the directory the file is in actually exists."""
    d = os.path.dirname(filename)
    if d and not os.path.exists(d):
        try:
            mkdir(d)
        except OSError:
            # A separate process (when running multiprocessing) might
            # have created the directory
            pass

# util.File


def robust_rename(old, new):
    """Rename old to new no matter what (if the file exists, it's
    removed, if the target dir doesn't exist, it's created)"""
    # print "robust_rename: %s -> %s" % (old,new)
    ensure_dir(new)
    if os.path.exists(new):
        os.unlink(new)
    try:
        shutil.move(old, new)
    except IOError:
        # eh, what are you gonna do?
        pass

# util.File


def robust_remove(filename):
    """Removes a filename no matter what (unlike :py:func:`os.unlink`, does not raise an error if the file does not exist)."""
    if os.path.exists(filename):
        # try:
        os.unlink(filename)

# util.string
def relurl(url, starturl):
    """Works like :py:func:`os.path.relpath`, but for urls

    >>> relurl("http://example.org/other/index.html", "http://example.org/main/index.html") == '../other/index.html'
    True
    >>> relurl("http://other.org/foo.html", "http://example.org/bar.html") == 'http://other.org/foo.html'
    True

    """
    urlseg = urlsplit(url)
    startseg = urlsplit(starturl)
    urldomain = urlunsplit(urlseg[:2] + tuple('' for i in range(3)))
    startdomain = urlunsplit(startseg[:2] + tuple('' for i in range(3)))
    if urldomain != startdomain:  # different domain, no relative url possible
        return url

    relpath = posixpath.relpath(urlseg.path, posixpath.dirname(startseg.path))
    res = urlunsplit(('', '', relpath, urlseg.query, urlseg.fragment))
    return res


# util.Sort
def numcmp(x, y):
    # still used by SFS.py
    """Works like ``cmp`` in python 2, but compares two strings using a
    'natural sort' order, ie "10" < "2". Also handles strings that
    contains a mixture of numbers and letters, ie "2" < "2 a".

    Return negative if x<y, zero if x==y, positive if x>y.

    >>> numcmp("10", "2")
    1
    >>> numcmp("2", "2 a")
    -1
    >>> numcmp("3", "2 a")
    1

    """
    nx = split_numalpha(x)
    ny = split_numalpha(y)
    return (nx > ny) - (nx < ny)  # equivalent to cmp which is not in py3

# util.Sort


def split_numalpha(s):
    """Converts a string into a list of alternating string and
    integers. This makes it possible to sort a list of strings
    numerically even though they might not be fully convertable to
    integers

    >>> split_numalpha('10 a §') == ['', 10, ' a §']
    True
    >>> sorted(['2 §', '10 §', '1 §'], key=split_numalpha) == ['1 §', '2 §', '10 §']
    True

    """
    res = []
    seg = ''
    digit = s[0].isdigit()
    for c in s:
        if (c.isdigit() and digit) or (not c.isdigit() and not digit):
            seg += c
        else:
            res.append(int(seg) if seg.isdigit() else seg)
            seg = c
            digit = not digit
    res.append(int(seg) if seg.isdigit() else seg)
    if isinstance(res[0], int):
        res.insert(0, '')  # to make sure every list has type str,int,str,int....
    return res

# util.Process


def runcmd(cmdline, require_success=False, cwd=None,
           cmdline_encoding=None,
           output_encoding="utf-8"):
    """Run a shell command, wait for it to finish and return the results.

    :param cmdline: The full command line (will be passed through a shell)
    :type cmdline: str
    :param require_success: If the command fails (non-zero exit code), raise :py:class:`~ferenda.errors.ExternalCommandError`
    :type require_success: bool
    :param cwd: The working directory for the process to run
    :returns: The returncode, all stdout output, all stderr output
    :rtype: tuple
    """
    if sys.platform == "win32" and six.PY2:
        cmdline_encoding = "windows-1252"
        
    if cmdline_encoding:
        cmdline = cmdline.encode(cmdline_encoding)

    p = subprocess.Popen(
        cmdline, cwd=cwd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (stdout, stderr) = p.communicate()
    ret = p.returncode

    if output_encoding:
        stdout = stdout.decode(output_encoding)
        stderr = stderr.decode(output_encoding)

    if (require_success and ret != 0):
        # FIXME: ExternalCommandError should have fields for cmd and
        # ret as well (and a sensible __str__ implementatiton)
        raise errors.ExternalCommandError(stderr)
    return (p.returncode, stdout, stderr)

# util.String


def normalize_space(string):
    """Normalize all whitespace in string so that only a single space between words is ever used, and that the string neither starts with nor ends with whitespace.

    >>> normalize_space(" This is  a long \\n string\\n") == 'This is a long string'
    True
    """
    return ' '.join(string.split())

# util.File


def list_dirs(d, suffix=None, reverse=False):
    """A generator that works much like :py:func:`os.listdir`, only recursively (and only returns files, not directories).

    :param d: The directory to start in
    :type d: str
    :param suffix: Only return files with the given suffix
    :type suffix: str
    :param reverse: Returns result sorted in reverse alphabetic order
    :param type:
    :returns: the full path (starting from d) of each matching file
    :rtype: generator
    
    """
    # inspired by http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/161542

    directories = [d]
    while directories:
        d = directories.pop()
        for f in sorted(os.listdir(d), key=split_numalpha, reverse=reverse):
            f = "%s%s%s" % (d, os.path.sep, f)
            if os.path.isdir(f):
                directories.insert(0, f)
            elif os.path.isfile:
                if suffix and not f.endswith(suffix):
                    continue
                else:
                    yield f

# util.String (or XML?)
# Still used by manager.makeresources, should be removed in favor of lxml
#
def indent_node(elem, level=0):
    """indents a etree node, recursively.

    .. note::

       This is deprecated, don't use it
    """
    i = "\r\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for elem in elem:
            indent_node(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

# util.File


def replace_if_different(src, dst, archivefile=None):
    """Like :py:func:`shutil.move`, except the *src* file isn't moved if the
    *dst* file already exists and is identical to *src*. Also doesn't
    require that the directory of *dst* exists beforehand.

    **Note**: regardless of whether it was moved or not, *src* is always deleted. 

    :param src: The source file to move
    :type  src: str
    :param dst: The destination file
    :type  dst: str
    :returns: True if src was copied to dst, False otherwise
    :rtype: bool
    """
    assert os.path.exists(src)
    if not os.path.exists(dst):
        # print "old file %s didn't exist" % dst
        robust_rename(src, dst)
        return True
    elif not filecmp.cmp(src, dst, shallow=False):
        # print "old file %s different from new file %s" % (dst,src)
        if archivefile:
            robust_rename(dst, archivefile)
        robust_rename(src, dst)
        return True
    else:
        # print "old file %s identical to new file %s" % (dst,src)
        os.unlink(src)
        return False

# util.File


def copy_if_different(src, dest):
    """Like :py:func:`shutil.copyfile`, except the *src* file isn't copied
if the *dst* file already exists and is identical to *src*. Also
doesn't require that the directory of *dst* exists beforehand.

    :param src: The source file to move
    :type  src: str
    :param dst: The destination file
    :type  dst: str
    :returns: True if src was copied to dst, False otherwise
    :rtype: bool

    """
    if not os.path.exists(dest):
        ensure_dir(dest)
        shutil.copy2(src, dest)
        return True
    elif not filecmp.cmp(src, dest):
        os.unlink(dest)
        shutil.copy2(src, dest)
        return True
    else:
        return False

# util.File


def outfile_is_newer(infiles, outfile):
    """Check if a given *outfile* is newer (has a more recent modification time) than a list of *infiles*. Returns True if so, False otherwise (including if outfile doesn't exist)."""

    if not os.path.exists(outfile):
        return False
    outfile_mtime = os.stat(outfile).st_mtime
    for f in infiles:
        # print "Testing whether %s is newer than %s" % (f, outfile)
        if os.path.exists(f) and os.stat(f).st_mtime > outfile_mtime:
            # print "%s was newer than %s" % (f, outfile)
            return False
    # print "%s is newer than %r" % (outfile, infiles)
    return True

# util.file


def link_or_copy(src, dst):
    """Create a symlink at *dst* pointing back to *src* on systems that support it. On other systems (i.e. Windows), copy *src* to *dst* (using :py:func:`copy_if_different`)
    """
    ensure_dir(dst)
    if os.path.lexists(dst):
        os.unlink(dst)
    if sys.platform == 'win32':
        # windows python have no working sumlink
        copy_if_different(src, dst)
    else:
        # The semantics of symlink are not identical to copy. The
        # source must be relative to the dstination, not relative to
        # cwd at creation time.
        relsrc = os.path.relpath(src, os.path.dirname(dst))
        os.symlink(relsrc, dst)


# util.string
def ucfirst(string):
    """Returns string with first character uppercased but otherwise unchanged.

    >>> ucfirst("iPhone") == 'IPhone'
    True
    """
    l = len(string)
    if l == 0:
        return string
    elif l == 1:
        return string.upper()
    else:
        return string[0].upper() + string[1:]

# util.time
# From http://bugs.python.org/issue7584#msg96917


def rfc_3339_timestamp(dt):
    """Converts a datetime object to a RFC 3339-style date

    >>> rfc_3339_timestamp(datetime.datetime(2013, 7, 2, 21, 20, 25)) == '2013-07-02T21:20:25-00:00'
    True
    """
    if dt.tzinfo is None:
        suffix = "-00:00"
    else:
        suffix = dt.strftime("%z")
        suffix = suffix[:-2] + ":" + suffix[-2:]
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + suffix


def parse_rfc822_date(httpdate):
    """Converts a RFC 822-type date string (more-or-less the same as a
HTTP-date) to an UTC-localized (naive) datetime.

    >>> parse_rfc822_date("Mon, 4 Aug 1997 02:14:00 EST")
    datetime.datetime(1997, 8, 4, 7, 14)

    """
    parsed_date = parsedate_tz(httpdate)
    return (datetime.datetime(*parsed_date[:7]) -
            datetime.timedelta(seconds=parsed_date[9]))

def strptime(datestr, format):
    """Like datetime.strptime, but guaranteed to not be affected by
    current system locale -- all datetime parsing is done using the C
    locale.

    >>> strptime("Mon, 4 Aug 1997 02:14:05", "%a, %d %b %Y %H:%M:%S")
    datetime.datetime(1997, 8, 4, 2, 14, 5)

    """
    with c_locale():
        return datetime.datetime.strptime(datestr, format)
        
    
# Util.file
def readfile(filename, mode="r", encoding="utf-8"):
    """Opens *filename*, reads it's contents and returns them as a string."""
    if "b" in mode:
        with open(filename, mode=mode) as fp:
            return fp.read()  # returns bytes, not str
    else:
        with codecs.open(filename, mode=mode, encoding=encoding) as fp:
            return fp.read()

# util.file
def writefile(filename, contents, encoding="utf-8"):
    """Create *filename* and write *contents* to it."""
    ensure_dir(filename)
    with codecs.open(filename, "w", encoding=encoding) as fp:
        fp.write(contents)


# util.string
def extract_text(html, start, end, decode_entities=True, strip_tags=True):
    """Given *html*, a string of HTML content, and two substrings (*start* and *end*) present in this string, return all text between the substrings, optionally decoding any HTML entities and removing HTML tags.

    >>> extract_text("<body><div><b>Hello</b> <i>World</i>&trade;</div></body>",
    ...              "<div>", "</div>") == 'Hello World™'
    True
    >>> extract_text("<body><div><b>Hello</b> <i>World</i>&trade;</div></body>",
    ...              "<div>", "</div>", decode_entities=False) == 'Hello World&trade;'
    True
    >>> extract_text("<body><div><b>Hello</b> <i>World</i>&trade;</div></body>",
    ...              "<div>", "</div>", strip_tags=False) == '<b>Hello</b> <i>World</i>™'
    True

    
    """
    startidx = html.index(start)
    endidx = html.rindex(end)
    text = html[startidx + len(start):endidx]
    if decode_entities:
        from six.moves import html_entities
        entities = re.compile("&(\w+?);")
        text = entities.sub(lambda m: six.unichr(html_entities.name2codepoint[m.group(1)]), text)
    if strip_tags:
        # http://stackoverflow.com/a/1732454
        tags = re.compile("</?\w+>")
        text = tags.sub('', text)
    return text


def merge_dict_recursive(base, other):
    """Merges the *other* dict into the *base* dict. If any value in other is itself a dict and the base also has a dict for the same key, merge these sub-dicts (and so on, recursively).

    >>> base = {'a': 1, 'b': {'c': 3}}
    >>> other = {'x': 4, 'b': {'y': 5}}
    >>> want = {'a': 1, 'x': 4, 'b': {'c': 3, 'y': 5}}
    >>> got = merge_dict_recursive(base, other)
    >>> got == want
    True
    >>> base == want
    True
    """

    for (key, value) in list(other.items()):
        if (isinstance(value, dict) and
            (key in base) and
                (isinstance(base[key], dict))):
            base[key] = merge_dict_recursive(base[key], value)
        else:
            base[key] = value
    return base


def resource_extract(resource_name, outfile, params={}):
    """Copy a file from the ferenda package resources to a specified path, optionally performing variable substitutions on the contents of the file.

    :param resource_name: The named resource (eg 'res/sparql/annotations.rq')
    :param outfile: Path to extract the resource to
    :param params: A dict of parameters, to be used with regular string subtitutions in the resource file.
    """
    fp = pkg_resources.resource_stream('ferenda', resource_name)
    resource = fp.read().decode('utf-8')
    if params:
        resource = resource % params
    ensure_dir(outfile)
    with codecs.open(outfile, "w") as fp:
        fp.write(resource)

# Deprecated -- was only ever used to find a handle leak
#
# http://stackoverflow.com/a/7142094
# def print_open_fds():
#     '''
#     return the number of open file descriptors for current process
#
#     .. warning: will only work on UNIX-like os-es.
#     '''
#     import subprocess
#     import os
#
#     pid = os.getpid()
#     procs = subprocess.check_output(
#         [ "lsof", '-w', '-Ff', "-p", str( pid ) ] ).decode('utf-8')
#
#     fprocs = list(filter(lambda s: s and s[ 0 ] == 'f' and s[1: ].isdigit(),
#                 procs.split( '\n' )))
#     print("Open file descriptors: " + ", ".join(fprocs))


# Copied from rdfextras.utils.pathutils
def uri_leaf(uri):
    """
    Get the "leaf" - fragment id or last segment - of a URI. Useful e.g. for
    getting a term from a "namespace like" URI.

    >>> uri_leaf("http://purl.org/dc/terms/title") == 'title'
    True
    >>> uri_leaf("http://www.w3.org/2004/02/skos/core#Concept") == 'Concept'
    True
    >>> uri_leaf("http://www.w3.org/2004/02/skos/core#") # returns None
    
    """
    for char in ('#', '/', ':'):
        if uri.endswith(char):
            break
        if char in uri:
            sep = char
            leaf = uri.rsplit(char)[-1]
        else:
            sep = ''
            leaf = uri
        if sep and leaf:
            return leaf


@contextmanager
def logtime(method, format="The operation took %(elapsed).3f sec", values={}):
    """A context manager that uses the supplied method and format string
    to log the elapsed time::
    
        with util.logtime(log.debug,
                          "Basefile %(basefile)s took %(elapsed).3f s",
                          {'basefile':'foo'}):
            do_stuff_that_takes_some_time()

    This results in a call like log.debug("Basefile foo took 1.324 s").

    """
    start = time.time()
    yield
    values['elapsed'] = time.time() - start
    method(format % values)

# Python docs recommends against this. Eh, what are you going to do?


@contextmanager
def c_locale(category=locale.LC_TIME):
    """Temporarily change process locale to the C locale, for use when eg
    parsing English dates on a system that may have non-english
    locale.

    >>> with c_locale():
    ...     datetime.datetime.strptime("August 2013", "%B %Y")
    datetime.datetime(2013, 8, 1, 0, 0)
    """

    oldlocale = locale.getlocale(category)
    newlocale = 'C' if six.PY3 else b'C'
    locale.setlocale(category, newlocale)
    try:
        yield
    finally:
        locale.setlocale(category, oldlocale)


# Example code from http://www.diveintopython.org/
def from_roman(s):
    """convert Roman numeral to integer.

    >>> from_roman("MCMLXXXIV")
    1984
    
    """
    roman_numeral_map = (('M', 1000),
                         ('CM', 900),
                         ('D', 500),
                         ('CD', 400),
                         ('C', 100),
                         ('XC', 90),
                         ('L', 50),
                         ('XL', 40),
                         ('X', 10),
                         ('IX', 9),
                         ('V', 5),
                         ('IV', 4),
                         ('I', 1))
    result = 0
    index = 0
    for numeral, integer in roman_numeral_map:
        while s[index:index + len(numeral)] == numeral:
            result += integer
            index += len(numeral)
    return result


def title_sortkey(s):
    """Transform a document title into a key useful for sorting and partitioning documents.

    >>> title_sortkey("The 'viewstate' property") == 'viewstateproperty'
    True

    """
    s = s.lower()
    if s.startswith("the "):
        s = s[4:]
    # filter away all non-word characters (but not digits)
    s = re.sub("\W+", "", s)
    # remove spaces
    return "".join(s.split())


def parseresults_as_xml(parseres, depth=0):
    # workaround for a buggy pyparsing.ParseResults.asXML which relies
    # on having dict.items() (not) returning items in a particular
    # order. We can't access res.__tocdict which really holds what
    # we're after, so we do the insane procedure of first getting a
    # repr string representation of the contents (luckily
    # pyparsing.ParseResults.__repr__ returns a string representation
    # of __tocdict), then parsing that with ast.literal_eval)
    #
    # Note that this is not a complete as_xml implementation, but it
    # works for the ParseResult objects we're dealing with right now
    # -- this'll be updated as we go along.
    rep = repr(parseres)
    tocdict = literal_eval(rep)[1]
    res = "\n"
    for k, v in sorted(tocdict.items(), key=lambda i: i[1][0][1]):
        if k == parseres.getName():
            continue
        
        if isinstance(v[0][0], str):
            res += "%s<%s>%s</%s>\n" % ("  "*(depth+1),k,v[0][0],k)
        elif v[0][0][1] == {}:
            res += "%s<%s>%s</%s>\n" % ("  "*(depth+1),k,v[0][0][0][0],k)
        # else: call parseresults_as_xml again somehow -- but we don't
        # have any 3-level grammar productions to test with
        
    return "%s<%s>%s</%s>\n" % ("  "*depth, parseres.getName(), res, parseres.getName())
