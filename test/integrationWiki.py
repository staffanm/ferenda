from ferenda.testutil import RepoTester, parametrize_repotester

# SUT
from lnwiki import LNMediaWiki


class TestWiki(RepoTester):
    repoclass = LNMediaWiki
    docroot = "../../ferenda/test/files/repo/mediawiki"
parametrize_repotester(TestWiki)
