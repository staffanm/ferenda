FROM python:3.8-slim-buster

RUN apt -qq update && \
    apt -qq -y --no-install-recommends install \
        apt-transport-https \
	gnupg \
	man-db \
	software-properties-common \
	wget && \
    add-apt-repository "deb http://ftp.us.debian.org/debian stretch main" && \
    wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | apt-key add - && \
    add-apt-repository "deb https://artifacts.elastic.co/packages/5.x/apt stable main" && \
    apt -qq update && \
    mkdir /usr/share/man/man1 && \
    apt -q -y --no-install-recommends install \
       antiword \
       cron \
       curl \
       mariadb-client \
       mariadb-server \
       mediawiki \
       elasticsearch \
       emacs24-nox \
       gcc \
       git \
       imagemagick \
       libreoffice \
       libtiff-tools \
       libxml2-dev \
       libxslt1-dev \
       mediawiki \
       nginx \
       openjdk-8-jre-headless \
       poppler-utils \
       procps \
       python3-dev \
       python3-venv \
       silversearcher-ag \
       supervisor \
       tesseract-ocr \
       tesseract-ocr-swe \
       uwsgi \
       uwsgi-plugin-python3 \
       zlib1g-dev && \
    mkdir /opt/fuseki && \
       cd /opt/fuseki && \
       (curl -s http://www-eu.apache.org/dist/jena/binaries/apache-jena-fuseki-3.13.1.tar.gz | tar -xvz --strip-components=1 ) && \
       mkdir -p run/databases/lagen && \
       mkdir -p run/configuration 
WORKDIR /usr/share/ferenda
COPY requirements.txt . 
RUN python3.7 -m venv .virtualenv && \
    ./.virtualenv/bin/pip install -r requirements.txt

EXPOSE 80 3330 9001 9200 
COPY docker /tmp/docker
RUN mv /tmp/docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf && \
    mv /tmp/docker/nginx.conf /etc/nginx/sites-enabled/default && \
    mv /tmp/docker/ferenda.ttl /opt/fuseki/run/configuration/
COPY . .

ENTRYPOINT ["/bin/bash", "/tmp/docker/setup.sh"]
CMD ["/usr/bin/supervisord"] # starts nginx, elasticsearch, fuseki, cron etc

# then: docker run -d -v ferendafiles:/usr/share/ferenda  -p 80:80 -p 3330:3330 -p 9001:9001 -p 9200:9200 <imageid>