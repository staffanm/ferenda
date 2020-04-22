FROM python:3.8-slim-buster
RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections && \
    apt -qq update && \
    apt -qq -y --no-install-recommends install \
        apt-transport-https \
	gnupg \
	man-db \
	software-properties-common \
	wget && \
    add-apt-repository "deb http://ftp.us.debian.org/debian stretch main" && \
    wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | apt-key add - && \
    add-apt-repository "deb https://artifacts.elastic.co/packages/7.x/apt stable main" && \
    apt -qq update && \
    mkdir /usr/share/man/man1 && \
    apt -q -y --no-install-recommends install \
       antiword \
       bzip2 \
       cron \
       curl \
       elasticsearch \
       emacs24-nox \
       file \
       g++ \
       gcc \
       git \
       imagemagick \
       libfontconfig1-dev \
       libjpeg-dev \
       liblcms2-dev \
       libopenjp2-7-dev \
       libreoffice \
       libtiff-dev \
       libtiff-tools \
       libxml2-dev \
       libxslt1-dev \
       locales \    
       make \
       mariadb-client \
       mariadb-server \
       mediawiki \
       nginx \
       openjdk-8-jre-headless \
       pkg-config \
       procps \
       python3-dev \
       python3-venv \
       silversearcher-ag \
       supervisor \
       tesseract-ocr \
       tesseract-ocr-swe \
       uwsgi \
       uwsgi-plugin-python3 \
       xz-utils \
       zlib1g-dev && \
   wget https://poppler.freedesktop.org/poppler-0.56.0.tar.xz && \
       xz -d poppler-0.56.0.tar.xz && \
       tar xvf poppler-0.56.0.tar && \
       cd poppler-0.56.0 && \
       ./configure && \
       make install && \
       cd .. && \
       rm -r poppler-0.56.0 && \
       ldconfig && \
    wget https://github.com/htacg/tidy-html5/releases/download/5.4.0/tidy-5.4.0-64bit.deb && \
       dpkg -i tidy-5.4.0-64bit.deb && \
    mkdir /opt/fuseki && \
       cd /opt/fuseki && \
       (curl -s http://www-eu.apache.org/dist/jena/binaries/apache-jena-fuseki-3.13.1.tar.gz | tar -xvz --strip-components=1 ) && \
       mkdir -p run/databases/lagen && \
       mkdir -p run/configuration 
WORKDIR /usr/share/ferenda
COPY requirements.txt . 
RUN python3.7 -m venv .virtualenv && \
    ./.virtualenv/bin/pip install wheel && \
    ./.virtualenv/bin/pip install -r requirements.txt

EXPOSE 80 8000 3030 9001 9200 
COPY docker /tmp/docker
RUN mv /tmp/docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf && \
    mv /tmp/docker/nginx.conf /etc/nginx/sites-enabled/default && \
    mv /tmp/docker/ferenda.ttl /opt/fuseki/run/configuration/ && \
    mv /tmp/docker/locale.gen /etc/locale.gen && locale-gen && \
    chmod +x /tmp/docker/build && mv /tmp/docker/build /usr/local/bin/build
COPY . .
# mv /tmp/docker/elasticsearch-jvm.options /etc/elasticsearch/jvm.options && \

ENTRYPOINT ["/bin/bash", "/tmp/docker/setup.sh"]
CMD ["/usr/bin/supervisord"] # starts nginx, elasticsearch, fuseki, cron etc

# docker build -t ferenda-image .
# then: docker run --name ferenda -d -v c:/docker/ferenda:/usr/share/ferenda/site  -p 81:80 -p 3030:3030 -p 9001:9001 -p 9200:9200 -p 8000:8000 ferenda-image
# and then: docker exec ferenda build all all --force --refresh