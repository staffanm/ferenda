from ferenda.testutil import RepoTester, parametrize_repotester

# SUT
from ferenda.sources.legal.se import LNMediaWiki


class TestWiki(RepoTester):
    repoclass = LNMediaWiki
    docroot = "test/files/repo/mediawiki"
parametrize_repotester(TestWiki)
