# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from rdflib.extras.describer import Describer as OrigDescriber
from rdflib import URIRef
from rdflib import Literal
from rdflib import RDF


class Describer(OrigDescriber):

    """Extends the utility class
    :py:class:`rdflib.extras.describer.Describer` so that it reads
    values and refences as well as write them.

    :param graph: The graph to read from and write to
    :type  graph: :py:class:`~rdflib.graph.Graph`
    :param about: the current subject to use
    :type  about: string or :py:class:`~rdflib.term.Identifier`
    :param base: Base URI for any relative URIs used with :py:meth:`~ferenda.Describer.about`, :py:meth:`~ferenda.Describer.rel` or :py:meth:`~ferenda.Describer.rev`, 
    :type  base: string
    """

    def getvalues(self, p):
        """Get a list (possibly empty) of all literal values for the
        given property and the current subject. Values will be
        converted to plain literals, i.e. not
        :py:class:`rdflib.term.Literal` objects.

        :param p: The property of the sought literal.
        :type  p: :py:class:`rdflib.term.URIRef`
        :returns: a list of matching literals
        :rtype: list of strings (or other appropriate python type if the
                literal has a datatype)
        """
        return [x.toPython() for x in self.graph.objects(self._current(), p) if isinstance(x, Literal)]

    def getrels(self, p):
        """Get a list (possibly empty) of all URIs for the
        given property and the current subject. Values will be
        converted to strings, i.e. not
        :py:class:`rdflib.term.URIRef` objects.

        :param p: The property of the sought URI.
        :type  p: :py:class:`rdflib.term.URIRef`
        :returns: The  matching URIs
        :rtype: list of strings
        """
        return [str(x) for x in self.graph.objects(self._current(), p) if isinstance(x, URIRef)]

    def getrdftype(self):
        """Get the `rdf:type` of the current subject.

        :returns: The URI of the current subjects's rdf:type.
        :rtype: string
        """
        return self.getrel(RDF.type)

    def getvalue(self, p):
        """Get a single literal value for the given property and the
        current subject. If the graph contains zero or more than one
        such literal, a :py:exc:`KeyError` will be raised.

        .. note::

           If this is all you use ``Describer`` for, you might want to use
           :py:meth:`rdflib.graph.Graph.value` instead -- the main advantage
           that this method has is that it converts the return value
           to a plain python object instead of a
           :py:class:`rdflib.term.Literal` object.
        
        :param p: The property of the sought literal.
        :type  p: :py:class:`rdflib.term.URIRef`
        :returns: The sought literal
        :rtype: string (or other appropriate python type if the literal has
                a datatype)

        """
        values = list(self.getvalues(p))
        if len(values) == 0:
            raise KeyError("No values for predicate %s" % p)
        elif len(values) > 1:
            raise KeyError("More than one value for predicate %s" % p)
        return values[0]

    def getrel(self, p):
        """Get a single URI for the given property and the current
        subject. If the graph contains zero or more than one such URI,
        a :py:exc:`KeyError` will be raised.

        :param p: The property of the sought literal.
        :type  p: :py:class:`rdflib.term.URIRef`
        :returns: The sought URI
        :rtype: string
        """
        refs = list(self.getrels(p))
        if len(refs) == 0:
            raise KeyError("No objects for predicate %s" % p)
        elif len(refs) > 1:
            raise KeyError("More than one object for predicate %s" % p)
        return refs[0]
