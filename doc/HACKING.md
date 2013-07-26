Misc notes on how ferenda is developed
======================================

Release process
---------------

Prep for release by creating a release branch off master

$ git branch
* master
$ git checkout -b release/0.1.0
# update ferenda/__init__.py, CHANGELOG.md, run last minute tests and generally tinker around
$ git commit -a -m "Final release prep"
$ git tag -a "v0.1.0" -m "Initial release"
$ git push --tags # makes the release show up in Github
$ python setup.py sdist upload -r testpypi
$ git checkout master
$ git merge release/0.1.0

Smoke-testing the released code
-------------------------------

In a new virtualenv:

$ pip install --extra-index-url https://testpypi.python.org/pypi ferenda

