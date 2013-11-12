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
13:04:16 root INFO Enabled class w3cstandards.W3CStandards (alias 'w3c')
# end enable

# begin status-example
$ ./ferenda-build.py w3cstandards.W3CStandards status # verbose
13:04:17 root INFO w3cstandards.W3CStandards status finished in 0.004 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.

$ ./ferenda-build.py w3c status # terse, exactly the same result
# end status-example
13:04:17 root INFO w3c status finished in 0.004 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: None.
 parse: None.
 generated: None.


# begin download
$ ./ferenda-build.py w3c download 
13:04:21 w3c INFO Downloading max 3 documents
13:04:22 w3c INFO geolocation-API: downloaded from http://www.w3.org/TR/2013/REC-geolocation-API-20131024/
13:04:23 w3c INFO touch-events: downloaded from http://www.w3.org/TR/2013/REC-touch-events-20131010/
13:04:25 w3c INFO ttml1: downloaded from http://www.w3.org/TR/2013/REC-ttml1-20130924/
# and so on...
# end download
13:04:25 root INFO w3c download finished in 5.958 sec

# begin list-downloaded
$ ls -1 data/w3c/downloaded
geolocation-API.html
geolocation-API.html.etag
touch-events.html
touch-events.html.etag
ttml1.html
ttml1.html.etag
# end list-downloaded

# begin status
$ ./ferenda-build.py w3c status
13:04:26 root INFO w3c status finished in 0.014 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: ttml1, touch-events, geolocation-API.
 parse: None. Todo: ttml1, touch-events, geolocation-API.
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
13:04:33 w3c INFO rdfa-core: OK (2.033 sec)
13:04:33 root INFO w3c parse finished in 2.053 sec
# end parse

# begin list-parsed
$ ls -1 data/w3c/parsed
rdfa-core.xhtml
# end list-parsed

# begin status-2
$ ./ferenda-build.py w3c status
13:04:34 root INFO w3c status finished in 0.013 sec
Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: ttml1, touch-events, rdfa-core... (1 more)
 parse: rdfa-core. Todo: ttml1, touch-events, geolocation-API.
 generated: None. Todo: rdfa-core.
# end status-2

# begin parse-again
$ ./ferenda-build.py w3c parse rdfa-core
13:04:35 root INFO w3c parse finished in 0.016 sec
# end parse-again

# begin parse-force
$ ./ferenda-build.py w3c parse rdfa-core --force
13:04:38 w3c INFO rdfa-core: OK (2.024 sec)
13:04:38 root INFO w3c parse finished in 2.043 sec
# end parse-force

# begin parse-all
$ ./ferenda-build.py w3c parse --all --loglevel=DEBUG
13:04:39 w3c DEBUG ttml1: Starting
13:04:43 w3c DEBUG ttml1: Created data/w3c/parsed/ttml1.xhtml
13:04:45 w3c DEBUG ttml1: 12 triples extracted to data/w3c/distilled/ttml1.rdf
13:04:45 w3c INFO ttml1: OK (5.816 sec)
13:04:45 w3c DEBUG rdfa-core: Skipped
13:04:45 w3c DEBUG touch-events: Starting
13:04:45 w3c DEBUG touch-events: Created data/w3c/parsed/touch-events.xhtml
13:04:45 w3c DEBUG touch-events: 8 triples extracted to data/w3c/distilled/touch-events.rdf
13:04:45 w3c INFO touch-events: OK (0.486 sec)
13:04:45 w3c DEBUG geolocation-API: Starting
13:04:46 w3c DEBUG geolocation-API: Created data/w3c/parsed/geolocation-API.xhtml
13:04:46 w3c DEBUG geolocation-API: 5 triples extracted to data/w3c/distilled/geolocation-API.rdf
13:04:46 w3c INFO geolocation-API: OK (0.323 sec)
13:04:46 root INFO w3c parse finished in 6.662 sec
# end parse-all

# begin relate-all
$ ./ferenda-build.py w3c relate --all
13:04:47 w3c INFO Clearing context http://localhost:8000/dataset/w3c at repository ferenda
13:04:54 w3c INFO Dumped 34 triples from context http://localhost:8000/dataset/w3c to data/w3c/distilled/dump.nt
13:04:54 root INFO w3c relate finished in 7.655 sec
# end relate-all

# begin makeresources
$ ./ferenda-build.py w3c makeresources
$ find data/rsrc -print
data/rsrc
data/rsrc/css
data/rsrc/css/ferenda.css
data/rsrc/css/main.css
data/rsrc/css/normalize-1.1.3.css
data/rsrc/js
data/rsrc/js/ferenda.js
data/rsrc/js/jquery-1.10.2.js
data/rsrc/js/modernizr-2.6.3.js
data/rsrc/js/respond-1.3.0.js
data/rsrc/resources.xml
# end makeresources

# begin generate-all
$ ./ferenda-build.py w3c generate --all
13:04:58 w3c INFO ttml1: OK (2.102 sec)
13:04:59 w3c INFO touch-events: OK (0.112 sec)
13:04:59 w3c INFO rdfa-core: OK (0.220 sec)
13:04:59 w3c INFO geolocation-API: OK (0.100 sec)
13:04:59 root INFO w3c generate finished in 2.547 sec
# end generate-all

# begin final-commands
$ ./ferenda-build.py w3c toc
13:05:01 w3c INFO Created data/w3c/toc/issued/1999.html
13:05:01 w3c INFO Created data/w3c/toc/issued/2013.html
13:05:01 w3c INFO Created data/w3c/toc/title/c.html
13:05:02 w3c INFO Created data/w3c/toc/title/p.html
13:05:02 w3c INFO Created data/w3c/toc/title/r.html
13:05:02 w3c INFO Created data/w3c/toc/title/w.html
13:05:02 w3c INFO Created data/w3c/toc/index.html
13:05:02 root INFO w3c toc finished in 1.739 sec
$ ./ferenda-build.py w3c news
13:05:03 w3c INFO feed main: 4 entries
13:05:03 root INFO w3c news finished in 0.086 sec
$ ./ferenda-build.py w3c frontpage
13:05:04 root INFO frontpage: wrote data/index.html (0.017 sec)
# end final-commands

# begin runserver
# $ ./ferenda-build.py w3c runserver & 
# $ open http://localhost:8080/
# end runserver

# begin all
$ ./ferenda-build.py w3c all
13:05:07 w3c INFO Downloading max 3 documents
13:05:07 root INFO w3cstandards.W3CStandards download finished in 2.476 sec
13:05:07 root INFO w3cstandards.W3CStandards parse finished in 0.010 sec
13:05:07 root INFO w3cstandards.W3CStandards relate: Nothing to do!
13:05:07 root INFO w3cstandards.W3CStandards relate finished in 0.005 sec
13:05:07 w3c INFO ttml1: OK (0.000 sec)
13:05:07 w3c INFO touch-events: OK (0.000 sec)
13:05:07 w3c INFO rdfa-core: OK (0.000 sec)
13:05:07 w3c INFO geolocation-API: OK (0.000 sec)
13:05:07 root INFO w3cstandards.W3CStandards generate finished in 0.006 sec
13:05:09 w3c INFO Created data/w3c/toc/issued/1999.html
13:05:09 w3c INFO Created data/w3c/toc/issued/2013.html
13:05:09 w3c INFO Created data/w3c/toc/title/c.html
13:05:09 w3c INFO Created data/w3c/toc/title/p.html
13:05:09 w3c INFO Created data/w3c/toc/title/r.html
13:05:09 w3c INFO Created data/w3c/toc/title/w.html
13:05:09 w3c INFO Created data/w3c/toc/index.html
13:05:09 root INFO w3cstandards.W3CStandards toc finished in 1.705 sec
13:05:09 w3c INFO feed main: 4 entries
13:05:09 root INFO w3cstandards.W3CStandards news finished in 0.057 sec
13:05:09 root INFO frontpage: wrote data/index.html (0.013 sec)
# end all

$ cd ..
$ rm -r netstandards
