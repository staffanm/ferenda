import re

from . import EURLex, CDM

class EURLexActs(EURLex):
    alias = "eurlexacts"
    # DTS_SUBDOM can be either ALL_ALL, MNE (National transposition
    # measures), EU_LAW_ALL (legislation + consolidations),
    # LEGISLATION, or CONSLEG (consolidated acts. Maybe EU_LAW_ALL for
    # now is good (excluding national transposition measures cuts ~60%
    # of crap) Unfortunately, we also need the (DTT = R OR DTT = L)
    # clause (only select requlations or directives) or we'll get a
    # bunch of differnent crap (in sector 6, ie ECJ, and other)
    # expertquery_template = "SELECT CELLAR_ID, TI_DISPLAY, DN, DD WHERE DTS_SUBDOM = EU_LAW_ALL AND (DTT = R OR DTT = L) AND DD >= 01/01/2017 <= 31/12/2017 ORDER BY DD ASC"
    expertquery_template = "DTS_SUBDOM = EU_LAW_ALL AND (DTT = R OR DTT = L)"
    # Match 31960R0009 and 31960R0009(01) and 31960R0009R(01) or  02002L0057-20241224 (consolidated version of 32002L0057 on date 2024-12-24)

    celexfilter = re.compile(r"((3|0)\d{4}[RL]\d{4}(|-\d{8}|R?\(\d+\)))$").match

    rdf_type = (CDM.directive, CDM.regulation)
    xslt_template = "xsl/eurlexacts.xsl"
