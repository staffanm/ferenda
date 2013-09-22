# firststeps.sh

# begin setup
$ ferenda-setup netstandards
Prerequisites ok
Selected SQLITE as triplestore
Selected WHOOSH as search engine
Project created in netstandards
$ cd netstandards
$ ls
ferenda-build.py
ferenda.ini
wsgi.py
# end setup

$ cp ../doc/w3cstandards.py .

# begin enable
$ ./ferenda-build.py w3cstandards.W3CStandards enable
Enabled class w3cstandards.W3CStandards (alias 'w3c')
# end enable

# begin status-example
$ ./ferenda-build.py w3cstandards.W3CStandards status # verbose
$ ./ferenda-build.py w3c status # terse, exactly the same result
# end status-example

export FERENDA_DOWNLOADMAX=3
ls # begin download
$ ./ferenda-build.py w3c download 
20:16:40 w3c DEBUG download: Starting full download
20:16:40 w3c DEBUG download: Not re-downloading downloaded files
20:16:40 w3c DEBUG Starting at http://www.w3.org/TR/tr-status-all
20:16:42 w3c INFO Downloading max 3 documents
20:16:43 w3c INFO rdfa-core: downloaded from http://www.w3.org/TR/2013/REC-rdfa-core-20130822/
20:16:44 w3c INFO xhtml-rdfa: downloaded from http://www.w3.org/TR/2013/REC-xhtml-rdfa-20130822/
20:16:44 w3c INFO html-rdfa: downloaded from http://www.w3.org/TR/2013/REC-html-rdfa-20130822/
20:16:44 root INFO w3c download finished in 4.666 sec
# and so on...
# end download


# begin list-downloaded
$ ls -1 data/w3c/downloaded
html-rdfa.html
html-rdfa.html.etag
rdfa-core.html
rdfa-core.html.etag
xhtml-rdfa.html
xhtml-rdfa.html.etag
# end list-downloaded

# begin status
$ ./ferenda-build.py w3c status
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xhtml-rdfa, rdfa-core, html-rdfa.
 parse: None. Todo: xhtml-rdfa, rdfa-core, html-rdfa.
 generated: None.
20:18:21 root INFO w3c status finished in 0.013 sec
# end status

# begin parse
$ ./ferenda-build.py w3c parse rdfa-core
2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping start
2012-10-09 10:06:15 DEBUG: 3 triples extracted
2012-10-09 10:06:15 INFO: Parse rdf-direct-mapping OK (3.423 sec)
# end parse

# begin list-parsed
$ ls -1 data/w3c/parsed
rdb-direct-mapping.xhtml
# end list-parsed

# begin status-2
$ ./ferenda-build.py w3c status
Status for document repository 'w3c' (w3cstandards.W3CStandards)
download: widgets, rdf-plain-literal, rdb-direct-mapping... (13 more)
parse: rdb-direct-mapping. Todo: rdf-plain-literal, owl2-xml-serialization, owl2-syntax... (12 more)
generated: None. Todo: rdb-direct-mapping
# end status-2

# begin parse-again
$ ./ferenda-build.py w3c parse rdb-direct-mapping
2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping skipped (data/parsed/rdf-direct-mapping/index.xhtml up-to-date)
# end parse-again

# begin parse-force
$ ./ferenda-build.py w3c parse rdb-direct-mapping --force
2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping start
2012-10-09 10:06:15 DEBUG: 3 triples extracted
2012-10-09 10:06:15 INFO: Parse rdf-direct-mapping OK (3.423 sec)
# end parse-force

# begin parse-all
$ ./ferenda-build.py w3c parse --all --loglevel=INFO
2012-10-09 10:06:15 INFO: Parse r2rml OK (3.423 sec)
2012-10-09 10:06:15 INFO: Parse foo OK (3.423 sec)
2012-10-09 10:06:15 INFO: Parse bar OK (3.423 sec)
# end parse-all

# begin relate-all
$ ./ferenda-build.py w3c relate --all
2012-10-09 10:06:15 INFO: 467 triples in total (data/w3c/distilled/rdf.nt)
# end relate-all

# begin makeresources
$ ./ferenda-build.py w3c makeresources
$ ls -lR data/rsrc
resources.xml
css/normalize.css
css/base.css
css/ferenda.css
js/jquery-1.2.3.js
js/modernizr-123.js
js/ferenda.js
# end makeresources

# begin generate-all
$ ./ferenda-build.py w3c generate --all
# end generate-all

# begin final-commands
$ ./ferenda-build.py w3c toc
$ ./ferenda-build.py w3c news
$ ./ferenda-build.py w3c frontpage
# end final-commands

# begin runserver
$ ./ferenda-build.py w3c runserver & 
$ open http://localhost:8080/
# end runserver

# begin all
$ ./ferenda-build.py w3c all
# end all
