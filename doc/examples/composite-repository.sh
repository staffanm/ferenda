$ ferenda-setup patents
$ cd patents
$ mv ../patents.py .
# begin example
$ ./ferenda-build.py patents.CompositePatents enable
# calls download() for all subrepos
$ ./ferenda-build.py pat download 
# selects the best subrepo that has patent 5,723,765, calls parse()
# for that, then copies the result to pat/parsed/ 5723765 (or links)
$ ./ferenda-build.py pat parse 5723765 
# uses the pat/parsed/5723765 data. From here on, we're just like any
# other docrepo.
$ ./ferenda-build.py pat generate 5723765 
# end example
