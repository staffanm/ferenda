# -*- coding: UTF-8 -*-
__metaclass__ = type
import re
import urlparse
from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS


COIN = Namespace("http://purl.org/court/def/2009/coin#")


class URIMinter:

    def __init__(self, config, scheme_uri):
        self.space = URISpace(config.resource(scheme_uri))

    def compute_uris(self, data):
        results = {}
        for s in set(data.subjects()):
            uris = self.space.coin_uris(data.resource(s))
            if uris:
                results[s] = uris
        return results


class URISpace:

    def __init__(self, resource):
        self.base = unicode(resource.value(COIN.base))
        self.templates = [Template(self, template_resource)
                for template_resource in resource.objects(COIN.template)]
        self.slugTransform = SlugTransformer(resource.value(COIN.slugTransform))

    def coin_uris(self, resource):
        uris = []
        for template in self.templates:
            # TODO: order by specificity (number of non-shared vars per template)
            uri = template.coin_uri(resource)
            if uri:
                uris.append(uri)
        return uris

    def transform_value(self, value):
        return self.slugTransform(value)


class SlugTransformer:

    def __init__(self, resource):
        self.applyTransforms = resource and list(
                resource.objects(COIN.apply)) or []
        self.replace = resource and replacer(
                resource.objects(COIN['replace'])) or None
        self.spaceRepl = resource and resource.value(
                COIN.spaceReplacement) or u'+'
        self.stripPattern = resource and re.compile(
                unicode(resource.value(COIN.stripPattern))) or None

    def __call__(self, value):
        value = unicode(value)
        for transform in self.applyTransforms:
            if transform.identifier == COIN.ToLowerCase:
                value = value.lower()
            else:
                #raise NotImplementedError(
                #        u"URIMinter doesn't support the <%s> transform" %
                #        transform.identifier)
                pass
        if self.replace:
            value = self.replace(value)
        if self.spaceRepl:
            value = value.replace(" ", self.spaceRepl)
        if self.stripPattern:
            value = self.stripPattern.sub(u'', value)
        return value


def replacer(replacements):
    char_pairs = [unicode(repl).split(u' ') for repl in replacements]
    def replace(value):
        for char, repl in char_pairs:
            value = value.replace(char, repl)
        return value
    return replace


class Template:

    def __init__(self, space, resource):
        self.space = space
        self.forType = resource.value(COIN.forType)
        self.uriTemplate = resource.value(COIN.uriTemplate)
        self.relToBase = resource.value(COIN.relToBase)
        self.relFromBase = resource.value(COIN.relFromBase)
        self.bindings = [Binding(self, binding)
                for binding in resource.objects(COIN.binding)]
        # IMPROVE: if not template and variable bindings correspond: TemplateException

    def coin_uri(self, resource):
        if self.forType and not self.forType in resource.objects(RDF.type):
            return None
        matches = {}
        for binding in self.bindings:
            match = binding.find_match(resource)
            if match:
                matches[binding.variable] = match
        if len(matches) < len(self.bindings):
            return None
        # IMPROVE: store and return partial success (for detailed feedback)
        return self.build_uri(self.get_base(resource), matches)

    def build_uri(self, base, matches):
        if not base:
            return None
        if not self.uriTemplate:
            return None # TODO: one value, fragmentTemplate etc..
        expanded = unicode(self.uriTemplate)
        expanded = expanded.replace("{+base}", base)
        for var, value in matches.items():
            slug = self.space.transform_value(value)
            expanded = expanded.replace("{%s}" % var, slug)
        return urlparse.urljoin(base, expanded)

    def get_base(self, resource):
        base = self.space.base
        def guarded_base(b):
            if b:
                s = unicode(b.identifier)
                if s.startswith(base):
                    return s
        if self.relToBase:
            for baserel in resource.objects(self.relToBase.identifier):
                return guarded_base(baserel)
        elif self.relFromBase:
            for baserev in resource.subjects(self.relFromBase.identifier):
                return guarded_base(baserev)
        else:
            return base


class Binding:

    def __init__(self, template, resource):
        self.template = template
        self.p = resource.value(COIN.property).identifier
        self.variable = resource.value(COIN.variable) or uri_leaf(self.p)
        self.slugFrom = resource.value(COIN.slugFrom)

    def find_match(self, resource):
        value = resource.value(self.p)
        if self.slugFrom:
            if not value:
                return None
            return value.value(self.slugFrom.identifier)
        else:
            return value


def uri_leaf(uri):
    for char in ('#', '/', ':'):
        if uri.endswith(char):
            break
        base, sep, leaf = uri.rpartition(char)
        if sep and leaf:
            return leaf


if __name__ == '__main__':

    from sys import argv
    args = argv[1:]
    space_data = args.pop(0)
    sources = args

    from mimetypes import guess_type
    def parse_file(graph, fpath):
        graph.parse(fpath, format=guess_type(fpath)[0] or 'n3')

    coin_graph = Graph()
    parse_file(coin_graph, space_data)
    instance_data = Graph() if sources else coin_graph
    for source in sources:
        parse_file(instance_data, source)

    for space_uri in coin_graph.subjects(RDF.type, COIN.URISpace):
        print "URI Space <%s>:" % space_uri
        minter = URIMinter(coin_graph, space_uri)
        for subj, uris in minter.compute_uris(instance_data).items():
            if unicode(subj) in uris:
                print "Found <%s> in" % subj,
            else:
                print "Did not find <%s> in" % subj,
            print ", ".join(("<%s>" % uri) for uri in uris)


