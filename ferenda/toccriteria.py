# -*- coding: utf-8 -*-
from __future__ import unicode_literals


class TocCriteria(object):

    """Represents a particular way of organizing the documents in a
    repository, for the purpose of generating a table of contents for
    those douments.

    :param binding: The variable name (binding) for that same
                    predicate in the sparql results retrieved from the
                    query constructed by
                    :py:meth:`~ferenda.DocumentRepository.toc_query`.
    :type  binding: str
    :param label: A text label for the entire set of toc for this criteria.
    :type  label: str
    :param pagetitle: A template string used together with whatever
                      selector returns to form a full TOC page title,
                      eg ``Documents starting with "%s"``.
    :type  pagetitle: str
    :param selector: Function that takes a single dict (from
                     :py:meth:`~ferenda.DocumentRepository.toc_select`)
                     and returns a single value used for grouping
                     documents (eg. first letter of ``title``,
                     year-part of ``date``, etc).
    :type  selector: callable
    :param key: Function that takes a single dict and returns a value
                that can be used for sorting documents
    :type key: callable
    :param selector_descending: Whether pagesets constructed by this selector should be sorted in descending (reverse) order
    :type  selector_descending: bool
    :param key_descending: Whether pages selected by this key should be sorted in descending (reverse) order
    :type  key_descending: bool
    """

    def __init__(self, binding, label, pagetitle, selector, key, selector_descending=False, key_descending=False):
        self.binding = binding
        self.label = label
        self.pagetitle = pagetitle
        self.selector = selector
        self.key = key
        self.selector_descending = selector_descending
        self.key_descending = key_descending
