# flake8: noqa
from rdflib import Namespace
RPUBL = Namespace('http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#')
URISPACE = Namespace('http://rinfo.lagrummet.se/sys/uri/space#')
RINFOEX = Namespace("http://lagen.nu/terms#")
from .swedishlegalsource import SwedishLegalSource, SwedishLegalStore, SwedishCitationParser
from .fixedlayoutsource import FixedLayoutStore, FixedLayoutSource
from .regeringen import Regeringen
from .riksdagen import Riksdagen
from .trips import Trips, NoMoreLinks
from .arn import ARN
from .direktiv import Direktiv
from .ds import Ds
from .dv import DV
from .jk import JK
from .jo import JO
from .kommitte import Kommitte
from .myndfskr import MyndFskrBase
from .propositioner import Propositioner
from .sfs import SFS
from .sou import SOU
