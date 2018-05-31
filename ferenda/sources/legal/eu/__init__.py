# flake8: noqa
from rdflib import Namespace
CDM = Namespace('http://publications.europa.eu/ontology/cdm#')
# CDM is the Common Metadata Model of the resources published by the
# Publications Office of the European Union
# http://publications.europa.eu/mdr/cdm/index.html
from .formex import FormexParser
from .eurlex import EURLex
from .acts import EURLexActs
from .caselaw import EURLexCaselaw
from .treaties import EURLexTreaties
