from ferenda.testutil import RepoTester, parametrize_repotester
from ferenda.sources.tech import RFC

class RFCTester(RepoTester):
    repoclass = RFC
    docroot = "myrepo/tests/files"

parametrize_repotester(RFCTester)
