FROM ubuntu:20.04
RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections && \
    apt -qq update && \
    apt -qq -y --no-install-recommends install \
        apt-transport-https \
	gnupg \
	man-db \
	software-properties-common \
	wget && \
    wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | apt-key add - && \
    add-apt-repository "deb https://artifacts.elastic.co/packages/7.x/apt stable main" && \
    apt -qq update
RUN apt -q -y --no-install-recommends install \
       antiword \
       bzip2 \
       cron \
       curl \
       emacs-nox \
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
       pkg-config \
       procps \
       python3-dev \
       python3-venv \
       silversearcher-ag \
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
       dpkg -i tidy-5.4.0-64bit.deb
WORKDIR /usr/share/ferenda
COPY requirements.txt . 
RUN python3 -m venv /usr/share/.virtualenv && \
    /usr/share/.virtualenv/bin/pip install wheel && \
    /usr/share/.virtualenv/bin/pip install -r requirements.txt

EXPOSE 8000 8001
COPY docker /tmp/docker
RUN mv /tmp/docker/locale.gen /etc/locale.gen && locale-gen && \
    chmod +x /tmp/docker/build && mv /tmp/docker/build /usr/local/bin/build
COPY . .

ENTRYPOINT ["/bin/bash", "/tmp/docker/setup.sh"]
CMD ["/usr/share/.virtualenv/bin/gunicorn", "--bind=0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "--workers=5", "--chdir=/usr/share/site", "wsgi:application"] 

# docker build -t ferenda-image .
# then: docker run --name ferenda -d -v c:/docker/ferenda:/usr/share/site -p 8000:8000 -p 8001:8001 ferenda-image
# and then: docker exec ferenda build all all --force --refresh