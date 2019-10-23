# FROM docker.elastic.co/elasticsearch/elasticsearch:5.6.16
# # we'd like to use buster (debian 10) but it seems difficult to get java 8 with that (only supports java 11, and using only java 8 from strech might not work either)
FROM python:3.8-slim-buster
RUN apt -qq update
RUN apt -qq -y  --no-install-recommends install apt-transport-https wget gnupg software-properties-common man-db 
RUN add-apt-repository "deb http://ftp.us.debian.org/debian stretch main"
RUN wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | apt-key add -
RUN add-apt-repository "deb https://artifacts.elastic.co/packages/5.x/apt stable main"
RUN apt -qq update
RUN mkdir /usr/share/man/man1
RUN apt -q -y --no-install-recommends install openjdk-8-jre-headless
RUN apt -q -y install gcc zlib1g-dev libxml2-dev libxslt1-dev poppler-utils antiword imagemagick tesseract-ocr tesseract-ocr-swe libtiff-tools emacs24-nox silversearcher-ag curl
RUN apt -q -y  --no-install-recommends --no-install-suggests install libreoffice
RUN mkdir /opt/fuseki && cd /opt/fuseki && ( curl -s http://www-eu.apache.org/dist/jena/binaries/apache-jena-fuseki-3.13.1.tar.gz | tar -xvz --strip-components=1 ) && mkdir run
COPY docker/policy-rc.d /usr/sbin/
RUN apt -q -y install supervisor nginx uwsgi elasticsearch uwsgi-plugin-python3
# TRY to not change anything above this line in order to not invalidate the cached itermediate images!
RUN mkdir /usr/share/ferenda
WORKDIR /usr/share/ferenda
COPY requirements.txt . 
# unfortunately we need py3.7 for now since that's what the uwsgi plugin runs with
RUN apt -q -y install python3-venv python3-dev procps
RUN python3.7 -m venv .virtualenv
RUN ./.virtualenv/bin/pip install -r requirements.txt
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/nginx.conf /etc/nginx/sites-enabled/
COPY docker/uwsgi.ini /etc/uwsgi/apps-enabled/
COPY docker/fuseki /etc/default/fuseki
COPY docker/start-fuseki.sh /opt/fuseki/
COPY docker/elasticsearch-jvm.options /etc/elasticsearch/jvm.options
COPY . .
RUN FERENDA_SET_TRIPLESTORE_LOCATION=1 FERENDA_SET_FULLTEXTINDEX_LOCATION=1 ./.virtualenv/bin/python ferenda-setup.py site
COPY docker/ferenda-build.py site/
RUN cd site && ../.virtualenv/bin/python ./ferenda-build.py all all
CMD ["/usr/bin/supervisord"] # starts nginx et al