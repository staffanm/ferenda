# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from operator import attrgetter


class NewsCriteria(object):

    """Represents a particular subset of the documents in a repository,
    for the purpose of generating a news feed for that subset. These
    criteria objects are used by
    :py:meth:`~ferenda.DocumentRepository.news`, and the simplest way
    of controlling which criteria are used for your docrepo is to
    override :py:meth:`~ferenda.DocumentRepository.news_criteria` to
    make it return a list of instantiated NewsCriteria objects.

    :param basefile: A slug-like basic text label for this subset.
    :type  basefile: str
    :param feedtitle: The title for this particular news feed
    :type  feedtitle: str
    :param selector: Function that takes a single
                     :py:class:`~ferenda.DocumentEntry` object and
                     returns true iff it should be included in this
                     feed.
    :type  selector: callable
    :param key: Function that takes a single
                :py:class:`~ferenda.DocumentEntry` object and returns
                a value that can be used for sorting that object. The
                default implementation returns the entrys
                ``updated`` attribute, so that the feed contains entries
                sorted most recently updated first.
    :type  key: callable

    """

    def __init__(self, basefile, feedtitle, selector=None, key=None):
        self.basefile = basefile
        self.feedtitle = feedtitle
        if not selector:
            self.selector = lambda entry: True
        else:
            assert callable(selector)
            self.selector = selector
        if not key:
            self.key = attrgetter('updated')  # or lambda x: x.updated
        else:
            assert callable(key)
            self.key = key
        self.entries = []
