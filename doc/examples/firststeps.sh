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

$ mv ../w3cstandards.py .

# begin enable
$ ./ferenda-build.py w3cstandards.W3CStandards enable
22:16:26 root INFO Enabled class w3cstandards.W3CStandards (alias 'w3c')
# end enable

# begin status-example
$ ./ferenda-build.py w3cstandards.W3CStandards status # verbose
22:16:27 root INFO w3cstandards.W3CStandards status finished in 0.010 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.

$ ./ferenda-build.py w3c status # terse, exactly the same result
# end status-example
22:16:28 root INFO w3c status finished in 0.004 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.


# begin download
$ ./ferenda-build.py w3c download 
22:16:31 w3c INFO Downloading max 3 documents
22:16:32 w3c INFO emotionml: downloaded from http://www.w3.org/TR/2014/REC-emotionml-20140522/
22:16:33 w3c INFO MathML3: downloaded from http://www.w3.org/TR/2014/REC-MathML3-20140410/
22:16:33 w3c INFO xml-entity-names: downloaded from http://www.w3.org/TR/2014/REC-xml-entity-names-20140410/
# and so on...
# end download
22:16:33 root INFO w3c download finished in 4.118 sec

# begin list-downloaded
$ ls -1 data/w3c/downloaded
MathML3.html
MathML3.html.etag
emotionml.html
emotionml.html.etag
xml-entity-names.html
xml-entity-names.html.etag
# end list-downloaded

# begin status
$ ./ferenda-build.py w3c status
22:16:34 root INFO w3c status finished in 0.011 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xml-entity-names, emotionml, MathML3.
 parse: None. Todo: xml-entity-names, emotionml, MathML3.
 generated: None.
# end status

# make sure the basefile we use for examples is available. To match
# logging output, it should not be one of the basefiles downloaded
# above
# begin single-download
$ ./ferenda-build.py w3c download rdfa-core --loglevel=CRITICAL
# end single-download
 
# begin parse
$ ./ferenda-build.py w3c parse rdfa-core
22:16:45 w3c INFO rdfa-core: parse OK (4.863 sec)
22:16:45 root INFO w3c parse finished in 4.935 sec
# end parse

# begin list-parsed
$ ls -1 data/w3c/parsed
rdfa-core.xhtml
# end list-parsed

# begin status-2
$ ./ferenda-build.py w3c status
22:16:47 root INFO w3c status finished in 0.032 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xml-entity-names, rdfa-core, emotionml... (1 more)
 parse: rdfa-core. Todo: xml-entity-names, emotionml, MathML3.
 generated: None. Todo: rdfa-core.
# end status-2

# begin parse-again
$ ./ferenda-build.py w3c parse rdfa-core
22:16:50 root INFO w3c parse finished in 0.019 sec
# end parse-again

# begin parse-force
$ ./ferenda-build.py w3c parse rdfa-core --force
22:16:56 w3c INFO rdfa-core: parse OK (5.123 sec)
22:16:56 root INFO w3c parse finished in 5.166 sec
# end parse-force

$ rm data/w3c/downloaded/rdfa-core.html*

# begin parse-all
$ ./ferenda-build.py w3c parse --all --loglevel=DEBUG
22:16:59 w3c DEBUG xml-entity-names: Starting
22:16:59 w3c DEBUG xml-entity-names: Created data/w3c/parsed/xml-entity-names.xhtml
22:17:00 w3c DEBUG xml-entity-names: 6 triples extracted to data/w3c/distilled/xml-entity-names.rdf
22:17:00 w3c INFO xml-entity-names: parse OK (0.717 sec)
22:17:00 w3c DEBUG emotionml: Starting
22:17:00 w3c DEBUG emotionml: Created data/w3c/parsed/emotionml.xhtml
22:17:01 w3c DEBUG emotionml: 11 triples extracted to data/w3c/distilled/emotionml.rdf
22:17:01 w3c INFO emotionml: parse OK (1.174 sec)
22:17:01 w3c DEBUG MathML3: Starting
22:17:01 w3c DEBUG MathML3: Created data/w3c/parsed/MathML3.xhtml
22:17:01 w3c DEBUG MathML3: 8 triples extracted to data/w3c/distilled/MathML3.rdf
22:17:01 w3c INFO MathML3: parse OK (0.332 sec)
22:17:01 root INFO w3c parse finished in 2.247 sec
# end parse-all

# begin relate-all
$ ./ferenda-build.py w3c relate --all
22:17:03 w3c INFO xml-entity-names: relate OK (0.618 sec)
22:17:04 w3c INFO rdfa-core: relate OK (1.542 sec)
22:17:06 w3c INFO emotionml: relate OK (1.647 sec)
22:17:08 w3c INFO MathML3: relate OK (1.604 sec)
22:17:08 w3c INFO Dumped 34 triples from context http://localhost:8000/dataset/w3c to data/w3c/distilled/dump.nt (0.007 sec)
22:17:08 root INFO w3c relate finished in 5.555 sec
# end relate-all

# begin makeresources
$ ./ferenda-build.py w3c makeresources
22:17:08 ferenda.resources INFO Wrote data/rsrc/resources.xml
$ find data/rsrc -print
data/rsrc
data/rsrc/api
data/rsrc/api/common.json
data/rsrc/api/context.json
data/rsrc/api/terms.json
data/rsrc/css
data/rsrc/css/ferenda.css
data/rsrc/css/main.css
data/rsrc/css/normalize-1.1.3.css
data/rsrc/img
data/rsrc/img/navmenu-small-black.png
data/rsrc/img/navmenu.png
data/rsrc/img/search.png
data/rsrc/js
data/rsrc/js/ferenda.js
data/rsrc/js/jquery-1.10.2.js
data/rsrc/js/modernizr-2.6.3.js
data/rsrc/js/respond-1.3.0.js
data/rsrc/resources.xml
# end makeresources

# begin generate-all
$ ./ferenda-build.py w3c generate --all
22:17:14 w3c INFO xml-entity-names: generate OK (1.728 sec)
22:17:14 w3c INFO rdfa-core: generate OK (0.242 sec)
22:17:14 w3c INFO emotionml: generate OK (0.336 sec)
22:17:14 w3c INFO MathML3: generate OK (0.216 sec)
22:17:14 root INFO w3c generate finished in 2.535 sec
# end generate-all

# begin final-commands
$ ./ferenda-build.py w3c toc
22:17:17 w3c INFO Created data/w3c/toc/dcterms_issued/2014.html
22:17:17 w3c INFO Created data/w3c/toc/dcterms_title/m.html
22:17:17 w3c INFO Created data/w3c/toc/dcterms_title/r.html
22:17:17 w3c INFO Created data/w3c/toc/dcterms_title/x.html
22:17:18 w3c INFO Created data/w3c/toc/index.html
22:17:18 root INFO w3c toc finished in 2.059 sec
$ ./ferenda-build.py w3c news
21:43:55 w3c INFO feed type/document: 4 entries
22:17:19 w3c INFO feed main: 4 entries
22:17:19 root INFO w3c news finished in 0.115 sec
$ ./ferenda-build.py w3c frontpage
22:17:21 root INFO frontpage: wrote data/index.html (0.112 sec)
# end final-commands

# begin runserver
# $ ./ferenda-build.py w3c runserver & 
# $ open http://localhost:8080/
# end runserver

# begin all
$ ./ferenda-build.py w3c all
22:17:25 w3c INFO Downloading max 3 documents
22:17:25 root INFO w3cstandards.W3CStandards download finished in 2.648 sec
22:17:25 root INFO w3cstandards.W3CStandards parse finished in 0.019 sec
22:17:25 root INFO w3cstandards.W3CStandards relate: Nothing to do!
22:17:25 root INFO w3cstandards.W3CStandards relate finished in 0.025 sec
22:17:25 root INFO Wrote data/rsrc/resources.xml
22:17:29 root INFO w3cstandards.W3CStandards generate finished in 0.006 sec
22:17:32 root INFO w3cstandards.W3CStandards toc finished in 3.376 sec
22:17:34 w3c INFO feed type/document: 4 entries
22:17:32 w3c INFO feed main: 4 entries
22:17:32 root INFO w3cstandards.W3CStandards news finished in 0.063 sec
22:17:32 root INFO frontpage: wrote data/index.html (0.017 sec)
# end all

$ cd ..
$ rm -r netstandards
