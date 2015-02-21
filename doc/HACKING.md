Misc notes on how ferenda is developed
======================================

Release process
---------------

Prep for release by creating a release branch off master

$ git branch
* master
$ git checkout -b release/0.1.0
# update ferenda/__init__.py, CHANGELOG.md, run last minute tests (in
# particularly, run "tox -r" if setup.py has changed) and generally
# tinker around
$ git commit -a -m "Final release prep"
$ git tag -a "v0.1.0" -m "Initial release"
$ git push orgin release/0.1.0
$ git push --tags # makes the release show up in Github
$ python setup.py register
$ python setup.py sdist
$ python setup.py bdist_wheel --universal
$ twine upload dist/ferenda-0.1.0.tar.gz dist/ferenda-0.1.0-py2.py3-none-any.whl
$ git checkout master
$ git merge release/0.1.0
# update ferenda/__init__.py and appveyor.yml to eg version=0.1.1.dev1
$ git commit -m "start of next iteration" ferenda/__init__.py
$ git push


Smoke-testing the released code
-------------------------------

In a new virtualenv:

$ pip install ferenda

