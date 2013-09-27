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
12:22:18 root INFO Enabled class w3cstandards.W3CStandards (alias 'w3c')
# end enable

# begin status-example
$ ./ferenda-build.py w3cstandards.W3CStandards status # verbose
12:22:20 root INFO w3cstandards.W3CStandards status finished in 0.004 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.

$ ./ferenda-build.py w3c status # terse, exactly the same result
# end status-example
12:22:20 root INFO w3c status finished in 0.004 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.

# begin download
$ ./ferenda-build.py w3c download 
20:16:42 w3c INFO Downloading max 3 documents
20:16:43 w3c INFO rdfa-core: downloaded from http://www.w3.org/TR/2013/REC-rdfa-core-20130822/
20:16:44 w3c INFO xhtml-rdfa: downloaded from http://www.w3.org/TR/2013/REC-xhtml-rdfa-20130822/
20:16:44 w3c INFO html-rdfa: downloaded from http://www.w3.org/TR/2013/REC-html-rdfa-20130822/
# and so on...
# end download
20:16:44 root INFO w3c download finished in 4.666 sec
$ 
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
20:18:21 root INFO w3c status finished in 0.013 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xhtml-rdfa, rdfa-core, html-rdfa.
 parse: None. Todo: xhtml-rdfa, rdfa-core, html-rdfa.
 generated: None.
# end status

# begin parse
$ ./ferenda-build.py w3c parse rdfa-core
14:45:57 w3c INFO rdfa-core: OK (2.051 sec)
14:45:57 root INFO w3c parse finished in 2.068 sec
# end parse

# begin list-parsed
$ ls -1 data/w3c/parsed
rdfa-core.xhtml
# end list-parsed

# begin status-2
$ ./ferenda-build.py w3c status
14:59:56 root INFO w3c status finished in 0.014 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xhtml-rdfa, rdfa-core, html-rdfa.
 parse: rdfa-core. Todo: xhtml-rdfa, html-rdfa.
 generated: None. Todo: rdfa-core.
# end status-2

# begin parse-again
$ ./ferenda-build.py w3c parse rdfa-core
10:06:15 root INFO w3c parse finished in 0.014 sec
# end parse-again

# begin parse-force
$ ./ferenda-build.py w3c parse rdfa-core --force
14:45:57 w3c INFO rdfa-core: OK (2.051 sec)
14:45:57 root INFO w3c parse finished in 2.068 sec
# end parse-force

# begin parse-all
$ ./ferenda-build.py w3c parse --all --loglevel=DEBUG
15:44:48 w3c DEBUG xhtml-rdfa: Starting
15:44:48 w3c DEBUG xhtml-rdfa: Created data/w3c/parsed/xhtml-rdfa.xhtml
15:44:48 w3c DEBUG xhtml-rdfa: 5 triples extracted to data/w3c/distilled/xhtml-rdfa.rdf
15:44:48 w3c INFO xhtml-rdfa: OK (0.567 sec)
15:44:48 w3c DEBUG rdfa-core: Skipped
15:44:50 w3c DEBUG html-rdfa: Starting
15:44:51 w3c DEBUG html-rdfa: Created data/w3c/parsed/html-rdfa.xhtml
15:44:51 w3c DEBUG html-rdfa: 11 triples extracted to data/w3c/distilled/html-rdfa.rdf
15:44:51 w3c INFO html-rdfa: OK (0.552 sec)
15:44:51 root INFO w3c parse finished in 3.128 sec
# end parse-all

# begin relate-all
$ ./ferenda-build.py w3c relate --all
15:21:05 w3c INFO Clearing context http://localhost:8000/dataset/w3c at repository ferenda
15:21:10 w3c INFO Dumped 25 triples from context http://localhost:8000/dataset/w3c to data/w3c/distilled/dump.nt
15:21:10 root INFO w3c relate finished in 5.215 sec
# end relate-all

# begin makeresources
$ ./ferenda-build.py w3c makeresources
$ find data/rsrc -print
data/rsrc
data/rsrc/css
data/rsrc/css/ferenda.css
data/rsrc/css/main.css
data/rsrc/css/normalize.css
data/rsrc/js
data/rsrc/js/ferenda.js
data/rsrc/js/jquery-1.9.0.js
data/rsrc/js/modernizr-2.6.2-respond-1.1.0.min.js
data/rsrc/resources.xml
# end makeresources

# begin generate-all
$ ./ferenda-build.py w3c generate --all
15:26:37 w3c INFO xhtml-rdfa OK (1.628 sec)
15:26:37 w3c INFO rdfa-core OK (0.227 sec)
15:26:37 w3c INFO html-rdfa OK (0.105 sec)
15:26:37 root INFO w3c generate finished in 1.973 sec
# end generate-all

# begin final-commands
$ ./ferenda-build.py w3c toc
16:11:39 w3c INFO Created data/w3c/toc/title/x.html
16:11:39 w3c INFO Created data/w3c/toc/title/h.html
16:11:39 w3c INFO Created data/w3c/toc/issued/2013.html
16:11:39 w3c INFO Created data/w3c/toc/title/r.html
16:11:39 w3c INFO Created data/w3c/toc/index.html
16:11:39 root INFO w3c toc finished in 1.658 sec
$ ./ferenda-build.py w3c news
16:30:51 w3c INFO feed main: 3 entries
16:30:51 root INFO w3c news finished in 0.067 sec
$ ./ferenda-build.py w3c frontpage
15:28:59 root INFO frontpage: wrote data/index.html (0.016 sec)
# end final-commands

# begin runserver
# $ ./ferenda-build.py w3c runserver & 
# $ open http://localhost:8080/
# end runserver

# begin all
$ ./ferenda-build.py w3c all
# end all
