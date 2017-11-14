# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from contextlib import contextmanager
from tempfile import NamedTemporaryFile
import filecmp
import os
import codecs
import shutil
import unicodedata
from urllib.parse import quote, unquote
from gzip import GzipFile
from bz2 import BZ2File
try:
    from lzma import LZMAFile
except ImportError:
    LZMAFile = None


from ferenda import util
from ferenda import errors


def _compressed_suffix(compression):
    """Returns a suitable suffix (including leading dot, eg ".bz2") for
    the selected compression method."""
    if compression is True: # select best compression -- but is xz always the best?
        return ".xz"
    elif compression in ("xz", "bz2", "gz"): # valid compression identifiers
        return "." + compression
    else:
        return ""

class _open(object):
    """This class can work both as a context manager and as a substitute
    for a straight open() call. Most of the time you want to use it as
    a context manager ("with _open(...) as fp:"), but sometimes you
    need to open a file in one function, return it and let the
    reciever close the file when done.

    """

    def __enter__(self):
        return self.fp  # this is what's returned as the fp in "with
                        # _open(...) as fp".

    def __exit__(self, *args):
        self.close(*args)
        
    def __init__(self, filename, mode, compression=None):
        self.filename = filename
        self.mode = mode
        self.compression = compression
        suffix = _compressed_suffix(compression)
        def wrap_fp(fp):
            if suffix == ".gz":
                fp = GzipFile(fileobj=fp, mode=mode)
            elif suffix == ".bz2":
                fp = BZ2File(fp, mode=mode)
            elif suffix == ".xz":
                fp = LZMAFile(fp, mode=mode)
            if suffix and "b" not in mode:
                # If mode is not binary (and we expect to be able to
                # write() str values, not bytes), need need to create
                # an additional encoding wrapper. That encoder can
                # probably use UTF-8 without any need for additional
                # configuration
                if "r" in mode and "w" in mode:
                    fp = StreamReaderWriter(fp, codecs.getreader("utf-8"),
                                            codecs.getwriter("utf-8"))
                elif "w" in mode:
                    fp = codecs.getwriter("utf-8")(fp)
                else:
                    fp = codecs.getreader("utf-8")(fp)
            fp.realname = filename
            return fp

        def opener():
            if suffix == ".gz":
                return GzipFile
            elif suffix == ".bz2":
                return BZ2File
            elif suffix == ".xz":
                return LZMAFile
            else:
                return open  # or io.open?
    
        if "w" in mode:
            if suffix:
                # We ignore the mode when creating the temporary file
                # and use the the default "w+b" so that we can create
                # compressed (binary) data. The wrapped fp can take
                # care of str->binary conversions
                tempmode = "w+b"
            else:
                tempmode = mode
            self.fp = wrap_fp(NamedTemporaryFile(mode=tempmode, delete=False))
        else:
            if "a" in mode and not os.path.exists(filename):
                util.ensure_dir(filename)
            if suffix:
                # compressed files must always be read as binary -
                # wrap_fp takes care of bytes->str conversion
                tempmode = "rb"
            else:
                tempmode = mode
            util.ensure_dir(filename)
            self.fp = wrap_fp(open(filename, tempmode))

    def close(self, *args, **kwargs):
        if "w" in self.mode:
            tempname = util.name_from_fp(self.fp)
            ret = self.fp.close()
            if not os.path.exists(self.filename) or not filecmp.cmp(tempname, self.filename):
                util.ensure_dir(self.filename)
                shutil.move(tempname, self.filename)
            else:
                os.unlink(tempname)
            return ret
        else:
            return self.fp.close()

    def read(self, *args, **kwargs):
        return self.fp.read(*args, **kwargs)

    def readlines(self, *args, **kwargs):
        return self.fp.readlines(*args, **kwargs)

    def write(self, *args, **kwargs):
        return self.fp.write(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self.fp.seek(*args, **kwargs)

    def tell(self, *args, **kwargs):
        return self.fp.tell(*args, **kwargs)

    @property
    def closed(self):
        return self.fp.closed

    @property
    def name(self):
        return self.fp.name


class DocumentStore(object):
    """Unifies handling of reading and writing of various data files
    during the ``download``, ``parse`` and ``generate`` stages.

    :param datadir: The root directory (including docrepo path
                    segment) where files are stored.
    :type datadir: str
    :param storage_policy: Some repositories have documents in several
                           formats, documents split amongst several
                           files or embedded resources. If
                           ``storage_policy`` is set to ``dir``, then
                           each document gets its own directory (the
                           default filename being ``index`` +suffix),
                           otherwise each doc gets stored as a file in
                           a directory with other files.  Affects
                           :py:meth:`~ferenda.DocumentStore.path`
                           (and therefore all other ``*_path``
                           methods)
    :type storage_policy: str
    :param compression: Which compression method to use when storing
                        files. Can be ``None`` (no compression),
                        ``"gz"``, ``"bz2"``, ``"xz"`` or ``True``
                        (select best compression method, currently
                        xz). NB: This only affects
                        :py:meth:`~ferenda.DocumentStore.intermediate_path`
                        and
                        :py:meth:`~ferenda.DocumentStore.open_intermediate`.
    :type compression: str

    """
    compression = None
    downloaded_suffixes = [".html"]
    intermediate_suffixes = [".xml"]
    invalid_suffixes = [".invalid"]
    
    def __init__(self, datadir, storage_policy="file", compression=None):
        self.datadir = datadir  # docrepo.datadir + docrepo.alias
        self.storage_policy = storage_policy
        assert self.storage_policy in ("dir", "file")
        self.compression = compression


    # TODO: Maybe this is a worthwhile extension to the API? Could ofc
    # easily be done everywhere where a non-document related path is
    # needed.
    def resourcepath(self, resourcename):
        return self.datadir + os.sep + resourcename.replace("/", os.sep)

    def path(self, basefile, maindir, suffix, version=None, attachment=None,
             storage_policy=None):
        """Calculate a full filesystem path for the given parameters.

        :param basefile: The basefile of the resource we're calculating a filename for
        :type  basefile: str
        :param maindir: The stage of processing, e.g. ``downloaded`` or ``parsed``
        :type  maindir: str
        :param suffix: Appropriate file suffix, e.g. ``.txt`` or ``.pdf``
        :param version: Optional. The archived version id
        :type  version: str
        :param attachment: Optional. Any associated file needed by the main file.
        :type  attachment: str
        :param storage_policy: Optional. Used to override `storage_policy` if needed
        :type  attachment: str

        .. note::

           This is a generic method with many parameters. In order to
           keep your code tidy and and loosely coupled to the actual
           storage policy, you should use methods like
           :meth:`~ferenda.DocumentStore.downloaded_path` or :meth:`~ferenda.DocumentStore.parsed_path` when
           possible.

        Example:

        >>> d = DocumentStore(datadir="/tmp/base")
        >>> realsep = os.sep
        >>> os.sep = "/"
        >>> d.path('123/a', 'parsed', '.xhtml') == '/tmp/base/parsed/123/a.xhtml'
        True
        >>> d.storage_policy = "dir"
        >>> d.path('123/a', 'parsed', '.xhtml') == '/tmp/base/parsed/123/a/index.xhtml'
        True
        >>> d.path('123/a', 'downloaded', None, 'r4711', 'appendix.txt') == '/tmp/base/archive/downloaded/123/a/r4711/appendix.txt'
        True
        >>> os.sep = realsep

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  maindir: The processing stage directory (normally ``downloaded``, ``parsed``, or ``generated``)
        :type   maindin: str
        :param   suffix: The file extension including period (i.e. ``.txt``, not ``txt``)
        :type    suffix: str
        :param  version: Optional, the archived version id
        :type   version: str
        :param attachment: Optional. Any associated file needed by the main file. Requires that ``storage_policy`` is set to ``dir``. ``suffix`` is ignored if this parameter is used.
        :type  attachment: str
        :returns: The full filesystem path
        :rtype:   str

        """
        pathfrag = self.basefile_to_pathfrag(basefile)

        if not storage_policy:
            storage_policy = self.storage_policy

        if version:
            v_pathfrag = self.basefile_to_pathfrag(version)
            segments = [self.datadir,
                        'archive', maindir, pathfrag, v_pathfrag]
        else:
            segments = [self.datadir, maindir, pathfrag]

        if storage_policy == "dir":
            if attachment:
                for illegal in ':/':
                    if illegal in attachment:
                        raise errors.AttachmentNameError(
                            "Char '%s' in attachment name '%s' not allowed" % (illegal, attachment))
                segments.append(attachment)
            else:
                segments.append("index" + suffix)
        else:
            if attachment is not None:
                raise errors.AttachmentPolicyError(
                    "Can't add attachments (name %s) if "
                    "storage_policy != 'dir'" % attachment)
            segments[-1] += suffix
        path = "/".join(segments)
        if os.sep != "/":
            path = path.replace("/", os.sep)
        return path

    def open(self, basefile, maindir, suffix, mode="r",
             version=None, attachment=None, compression=None):
        """Context manager that opens files for reading or writing. The
        parameters are the same as for
        :meth:`~ferenda.DocumentStore.path`, and the note is
        applicable here as well -- use
        :meth:`~ferenda.DocumentStore.open_downloaded`,
        :meth:`~ferenda.DocumentStore.open_parsed` et al if possible.

        Example:

        >>> store = DocumentStore(datadir="/tmp/base")
        >>> with store.open('123/a', 'parsed', '.xhtml', mode="w") as fp:
        ...     res = fp.write("hello world")
        >>> os.path.exists("/tmp/base/parsed/123/a.xhtml")
        True

        """
        filename = self.path(basefile, maindir, suffix, version, attachment)
        return _open(filename, mode, compression)


    def list_basefiles_for(self, action, basedir=None):
        """Get all available basefiles that can be used for the
        specified action.

        :param action: The action for which to get available
                       basefiles (``parse``, ``relate``, ``generate``
                       or ``news``)
        :type action: str
        :param basedir: The base directory in which to search for
                        available files. If not provided, defaults to
                        ``self.datadir``.
        :type basedir: str
        :returns: All available basefiles
        :rtype: generator
        """
        def prepend_index(suffixes):
            prepend = self.storage_policy == "dir"
            # If each document is stored in a separate directory
            # (storage_policy = "dir"), there is usually other
            # auxillary files (attachments and whatnot) in that
            # directory as well. Make sure we only yield a single file
            # from each directory. By convention, the main file is
            # called index.html, index.pdf or whatever.
            return [os.sep + "index" + s if prepend else s for s in suffixes]
                    
        if not basedir:
            basedir = self.datadir
        directory = None
        if action == "parse":
            directory = os.path.sep.join((basedir, "downloaded"))
            suffixes = prepend_index(self.downloaded_suffixes)
        elif action == "relate":
            directory = os.path.sep.join((basedir, "distilled"))
            suffixes = [".rdf"]
        elif action == "generate":
            directory = os.path.sep.join((basedir, "parsed"))
            suffixes = prepend_index([".xhtml"])
        elif action == "news":
            directory = os.path.sep.join((basedir, "entries"))
            suffixes = [".json"]
        # FIXME: fake action, needed for get_status. replace with
        # something more elegant
        elif action in ("_postgenerate"):
            directory = os.path.sep.join((basedir, "generated"))
            suffixes = [".html"]

        if not directory:
            raise ValueError("No directory calculated for action %s" % action)

        if not os.path.exists(directory):
            return

        # FIXME: Some stores need a more sophisticated way of filtering than this.
        for x in util.list_dirs(directory, suffixes, reverse=True):
            # ignore empty files placed by download (which may
            # have done that in order to avoid trying to
            # re-download nonexistent resources)
            if os.path.exists(x) and os.path.getsize(x) > 0 and not x.endswith(".root.json"):
                # get a pathfrag from full path
                # suffixlen = len(suffix) if self.storage_policy == "file" else len(suffix) + 1
                suffixlen = 0
                for s in suffixes:
                    if x.endswith(s):
                        suffixlen = len(s)
                        break
                else:
                    raise ValueError("%s doesn't end with a valid suffix (%s)" % x, ", ".join(suffixes))
                x = x[len(directory) + 1:-suffixlen]
                yield self.pathfrag_to_basefile(x)

    def list_versions(self, basefile, action=None):
        """Get all archived versions of a given basefile.

        :param basefile: The basefile to list archived versions for
        :type  basefile: str
        :param action: The type of file to look for (either
                       ``downloaded``, ``parsed`` or ``generated``. If
                       ``None``, look for all types.
        :type action: str
        :returns: All available versions for that basefile
        :rtype: generator
        """

        if action:
            assert action in (
                'downloaded', 'parsed', 'generated'), "Action %s invalid" % action
            actions = (action,)
        else:
            actions = ('downloaded', 'parsed', 'generated')

        basedir = self.datadir
        pathfrag = self.basefile_to_pathfrag(basefile)
        yielded_basefiles = []
        for action in actions:
            directory = os.sep.join((basedir, "archive",
                                     action, pathfrag))
            if not os.path.exists(directory):
                continue
            for x in util.list_dirs(directory, reverse=False):
                if os.path.exists(x):
                    # /datadir/base/archive/downloaded/basefile/version.html
                    # => version.html
                    x = x[len(directory) + 1:]
                    if self.storage_policy == "dir":
                        # version/index.html => version
                        x = os.sep.join(x.split(os.sep)[:-1])
                    else:
                        # version.html => version
                        x = os.path.splitext(x)[0]
                    if os.sep in x:
                        # we didn't find an archived file for
                        # basefile, instead we found an archived file
                        # for another basefile that startswith our
                        # basefile (eg '123' and '123/a', and we found
                        # '123/a/4.html')
                        continue
                    # print("Found file %r %r" % (x, self.pathfrag_to_basefile(x)))
                    basefile = self.pathfrag_to_basefile(x)
                    if basefile not in yielded_basefiles:
                        yielded_basefiles.append(basefile)
                        yield basefile

    def list_attachments(self, basefile, action, version=None):
        """Get all attachments for a basefile in a specified state

        :param action: The state (type of file) to look for (either
                       ``downloaded``, ``parsed`` or ``generated``. If
                       ``None``, look for all types.
        :type action: str
        :param basefile: The basefile to list attachments for
        :type  basefile: str
        :param version: The version of the basefile to list attachments for. If None, list attachments for the current version.
        :type  version: str
        :returns: All available attachments for the basefile
        :rtype: generator
        """
        if self.storage_policy != "dir":
            raise errors.AttachmentPolicyError(
                "Can't list attachments if storage_policy != 'dir'")

        basedir = self.datadir
        # pathfrag = self.pathfrag_to_basefile(basefile) # that can't be right?
        pathfrag = self.basefile_to_pathfrag(basefile)
        if version:
            v_pathfrag = self.basefile_to_pathfrag(version)
            directory = os.sep.join((basedir, "archive", action, pathfrag, v_pathfrag))
        else:
            directory = os.sep.join((basedir, action, pathfrag))
        # FIXME: Similar map exists in list_basefiles_for and in other
        # places throughout the code. Should subclasses be able to
        # control suffixes beyond the simple self.downloaded_suffix
        # mechanism?
        suffixmap = {'downloaded': self.downloaded_suffixes,
                     'parsed': ['.xhtml'],
                     'generated': ['.html']}
        mainfiles = ["index" + s for s in suffixmap[action]]
        for x in util.list_dirs(directory, reverse=False):
            # /datadir/base/downloaded/basefile/attachment.txt => attachment.txt
            x = x[len(directory) + 1:]
            if x not in mainfiles:
                if not [suffix for suffix in self.invalid_suffixes if x.endswith(suffix)]:
                    yield x

    def basefile_to_pathfrag(self, basefile):
        """Given a basefile, returns a string that can safely be used
        as a fragment of the path for any representation of that
        file. The default implementation recognizes a number of
        characters that are unsafe to use in file names and replaces
        them with HTTP percent-style encoding.

        Example:

        >>> d = DocumentStore("/tmp")
        >>> realsep = os.sep
        >>> os.sep = "/"
        >>> d.basefile_to_pathfrag('1998:204') == '1998/%3A204'
        True
        >>> os.sep = realsep

        If you wish to override how document files are stored in
        directories, you can override this method, but you should make
        sure to also override
        :py:meth:`~ferenda.DocumentStore.pathfrag_to_basefile` to
        work as the inverse of this method.

        :param basefile: The basefile to encode
        :type basefile: str
        :returns: The encoded path fragment
        :rtype: str
        """
        safe = '/;@&=+,'
        return quote(basefile, safe=safe).replace('%', os.sep + '%')

    def pathfrag_to_basefile(self, pathfrag):
        """Does the inverse of
        :py:meth:`~ferenda.DocumentStore.basefile_to_pathfrag`,
        that is, converts a fragment of a file path into the
        corresponding basefile.

        :param pathfrag: The path fragment to decode
        :type pathfrag: str
        :returns: The resulting basefile
        :rtype: str
        """
        # Pathfrags on MacOS, coming from the file system, are unicode
        # strings in NFD (decompsed), ie 'å' is split into 'a' and
        # COMBINING CHARACTER RING (or whatever it's called. We need
        # them in NFC, where 'å' is a single character.
        pathfrag = unicodedata.normalize("NFC", pathfrag)
        if os.sep == "\\":
            pathfrag = pathfrag.replace("\\", "/")
        return unquote(pathfrag.replace('/%', '%'))

    def archive(self, basefile, version):
        """Moves the current version of a document to an archive. All
        files related to the document are moved (downloaded, parsed,
        generated files and any existing attachment files).

        :param basefile: The basefile of the document to archive
        :type basefile: str
        :param version: The version id to archive under
        :type version: str
        """

        for meth in (self.downloaded_path, self.documententry_path,
                     self.parsed_path, self.serialized_path,
                     self.distilled_path,
                     self.annotation_path, self.generated_path):
            # FIXME: what about intermediate? Ignore them as they
            # should be able to be regenerated at any time?
            src = meth(basefile)
            dest = meth(basefile, version)
            if self.storage_policy == "dir" and meth in (self.downloaded_path,
                                                         self.parsed_path,
                                                         self.generated_path):
                src = os.path.dirname(src)
                dest = os.path.dirname(dest)
            if not os.path.exists(src):
                continue
            if os.path.exists(dest):
                raise errors.ArchivingError(
                    "Archive destination %s for basefile %s version %s already exists!" % (dest, basefile, version))
            # self.log.debug("Archiving %s to %s" % (src,dest))
            # print("Archiving %s to %s" % (src,dest))
            util.ensure_dir(dest)
            shutil.move(src, dest)

    def downloaded_path(self, basefile, version=None, attachment=None):
        """Get the full path for the downloaded file for the given
        basefile (and optionally archived version and/or attachment
        filename).

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :param attachment: Optional. Any associated file needed by the main file.
        :type  attachment: str
        :returns: The full filesystem path
        :rtype:   str
        """
        for suffix in self.downloaded_suffixes:
            path = self.path(basefile, "downloaded", suffix, version, attachment)
            if os.path.exists(path):
                return path
        else:
            return self.path(basefile, 'downloaded', self.downloaded_suffixes[0], version, attachment)

    def open_downloaded(self, basefile, mode="r", version=None, attachment=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.downloaded_path`.

        """

        filename = self.downloaded_path(basefile, version, attachment)
        return _open(filename, mode)

    def documententry_path(self, basefile, version=None):
        """Get the full path for the documententry JSON file for the given
        basefile (and optionally archived version).

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'entries', '.json', version,
                         storage_policy="file")

    def intermediate_path(self, basefile, version=None, attachment=None, suffix=None):
        """Get the full path for the main intermediate file for the given
        basefile (and optionally archived version).

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :param attachment: Optional. Any associated file created or retained
                           in the intermediate step
        :returns: The full filesystem path
        :rtype:   str
        """
        if suffix:
            return self.path(basefile, 'intermediate', suffix + _compressed_suffix(self.compression),
                             version, attachment)
        for suffix in self.intermediate_suffixes:
            path = self.path(basefile, 'intermediate', suffix + _compressed_suffix(self.compression),
                             version, attachment)
            if os.path.exists(path):
                return path
        else:
            suffix = self.intermediate_suffixes[0]
            return self.path(basefile, 'intermediate', suffix + _compressed_suffix(self.compression),
                             version, attachment)

    def open_intermediate(self, basefile, mode="r", version=None,
                          attachment=None, suffix=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.intermediate_path`.
        """
        filename = self.intermediate_path(basefile, version, attachment, suffix)
        return _open(filename, mode, self.compression)

    def parsed_path(self, basefile, version=None, attachment=None):
        """Get the full path for the parsed XHTML file for the given
        basefile.

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :param attachment: Optional. Any associated file needed by the
                           main file (created by
                           :py:meth:`~ferenda.DocumentRepository.parse`)
        :type  attachment: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'parsed', '.xhtml',
                         version, attachment)

    def open_parsed(self, basefile, mode="r", version=None, attachment=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.parsed_path`.

        """
        filename = self.parsed_path(basefile, version, attachment)
        return _open(filename, mode)

    def serialized_path(self, basefile, version=None, attachment=None):
        """Get the full path for the serialized JSON file for the given
        basefile.

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'serialized', '.json',
                         version, storage_policy="file")

    def open_serialized(self, basefile, mode="r", version=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.serialized_path`.

        """
        filename = self.serialized_path(basefile, version)
        return _open(filename, mode)

    def distilled_path(self, basefile, version=None):
        """Get the full path for the distilled RDF/XML file for the given
        basefile.

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'distilled', '.rdf',
                         version, storage_policy="file")

    def open_distilled(self, basefile, mode="r", version=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.distilled_path`.

        """
        filename = self.distilled_path(basefile, version)
        return _open(filename, mode)

    def generated_path(self, basefile, version=None, attachment=None):
        """Get the full path for the generated file for the given
        basefile (and optionally archived version and/or attachment
        filename).

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :param attachment: Optional. Any associated file needed by the main file.
        :type  attachment: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'generated', '.html',
                         version, attachment)

# Removed this method until I find a reason to use it
#
#    def open_generated(self, basefile, mode="r", version=None, attachment=None):
#        """Opens files for reading and writing,
#        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
#        the same as for
#        :meth:`~ferenda.DocumentStore.generated_path`.
#
#        """
#        filename = self.generated_path(basefile, version, attachment)
#        return _open(filename, mode)

    def annotation_path(self, basefile, version=None):
        """Get the full path for the annotation file for the given
        basefile (and optionally archived version).

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :param  version: Optional. The archived version id
        :type   version: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'annotations', '.grit.xml',
                         version, storage_policy="file")

    def open_annotation(self, basefile, mode="r", version=None):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.annotation_path`."""
        filename = self.annotation_path(basefile, version)
        return _open(filename, mode)

    def dependencies_path(self, basefile):
        """Get the full path for the dependency file for the given
        basefile

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :returns: The full filesystem path
        :rtype:   str
        """
        return self.path(basefile, 'deps', '.txt', storage_policy="file")

    def open_dependencies(self, basefile, mode="r"):
        """Opens files for reading and writing,
        c.f. :meth:`~ferenda.DocumentStore.open`. The parameters are
        the same as for
        :meth:`~ferenda.DocumentStore.dependencies_path`."""
        filename = self.dependencies_path(basefile)
        return _open(filename, mode)

    def atom_path(self, basefile):
        """Get the full path for the atom file for the given
        basefile

        .. note::

           This is used by :meth:`ferenda.DocumentRepository.news` and
           does not really operate on "real" basefiles. It might be
           removed. You probably shouldn't use it unless you override
           :meth:`~ferenda.DocumentRepository.news`

        :param basefile: The basefile for which to calculate the path
        :type  basefile: str
        :returns: The full filesystem path
        :rtype:   str

        """
        return self.path(basefile, 'feed', '.atom', storage_policy="file")
