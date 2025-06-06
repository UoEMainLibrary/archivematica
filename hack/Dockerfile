ARG TARGET=archivematica-mcp-server
ARG UBUNTU_VERSION=24.04
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG PYTHON_VERSION=3.9
ARG NODE_VERSION=20
ARG PYENV_DIR=/pyenv
ARG SELENIUM_DIR=/selenium
ARG MEDIAAREA_VERSION=1.0-25
ARG JHOVE_VERSION=1.32.1-1~22.04

# -----------------------------------------------------------------------------

FROM ubuntu:${UBUNTU_VERSION} AS base-builder

ARG PYENV_DIR=/pyenv

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Ubuntu 24.04 and later Docker images include a default user with UID (1000)
# and GID (1000). Remove this user to prevent conflicts with the USER_ID and
# GROUP_ID build arguments.
RUN set -ex \
	&& id -u ubuntu >/dev/null 2>&1 \
	&& userdel --remove ubuntu || true

RUN set -ex \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates \
		curl \
		git \
		gnupg \
		libldap2-dev \
		libmysqlclient-dev \
		libsasl2-dev \
		libsqlite3-dev \
		locales \
		make \
		pkg-config \
		tzdata \
	&& rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

ENV PYENV_ROOT=${PYENV_DIR}/data
ENV PATH=$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH

# -----------------------------------------------------------------------------

FROM base-builder AS pyenv-builder

ARG PYTHON_VERSION=3.9

RUN set -ex \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		build-essential \
		libbz2-dev \
		libffi-dev \
		liblzma-dev \
		libncursesw5-dev \
		libreadline-dev \
		libsqlite3-dev \
		libssl-dev \
		libxml2-dev \
		libxmlsec1-dev \
		tk-dev \
		xz-utils \
		zlib1g-dev \
	&& rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN set -ex \
	&& curl --retry 3 -L https://github.com/pyenv/pyenv-installer/raw/master/bin/pyenv-installer | bash \
	&& pyenv install ${PYTHON_VERSION} \
	&& pyenv global ${PYTHON_VERSION}

COPY --link requirements-dev.txt /src/requirements-dev.txt

RUN set -ex \
	&& pyenv exec python3 -m pip install --upgrade pip setuptools \
	&& pyenv exec python3 -m pip install --requirement /src/requirements-dev.txt \
	&& pyenv rehash

# -----------------------------------------------------------------------------

FROM base-builder AS browsers-builder

ARG SELENIUM_DIR=/selenium

RUN set -ex \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		jq \
	&& rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN set -ex \
	&& SELENIUM_CACHE=${SELENIUM_DIR}/cache \
	&& SELENIUM_BIN=${SELENIUM_DIR}/bin \
	&& mkdir -p $SELENIUM_CACHE $SELENIUM_BIN \
	&& SELENIUM_MANAGER_DOWNLOAD_URL=$(curl -sL -o /dev/null -w '%{url_effective}' https://github.com/SeleniumHQ/selenium_manager_artifacts/releases/latest | sed -e 's|/tag/\(.*\)|/download/\1/selenium-manager-linux|g') \
	&& curl -o $SELENIUM_BIN/selenium-manager -L $SELENIUM_MANAGER_DOWNLOAD_URL \
	&& chmod +x $SELENIUM_BIN/selenium-manager \
	&& CHROME_OUTPUT=$($SELENIUM_BIN/selenium-manager --cache-path $SELENIUM_CACHE --browser chrome --output JSON) \
	&& FIREFOX_OUTPUT=$($SELENIUM_BIN/selenium-manager --cache-path $SELENIUM_CACHE --browser firefox --output JSON) \
	&& ln -s $(echo $CHROME_OUTPUT | jq -r '.result.browser_path') $SELENIUM_BIN/google-chrome \
	&& ln -s $(echo $CHROME_OUTPUT | jq -r '.result.driver_path') $SELENIUM_BIN/chromedriver \
	&& ln -s $(echo $FIREFOX_OUTPUT | jq -r '.result.browser_path') $SELENIUM_BIN/firefox \
	&& ln -s $(echo $FIREFOX_OUTPUT | jq -r '.result.driver_path') $SELENIUM_BIN/geckodriver

# -----------------------------------------------------------------------------

FROM node:${NODE_VERSION} AS archivematica-dashboard-frontend-builder

COPY --link src/archivematica/dashboard/frontend /src/src/archivematica/dashboard/frontend

WORKDIR /src/src/archivematica/dashboard/frontend

RUN set ex \
	&& npm ci

# -----------------------------------------------------------------------------

FROM node:${NODE_VERSION} AS archivematica-dashboard-testing

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG SELENIUM_DIR=/selenium
ENV PATH=${SELENIUM_DIR}/bin:$PATH

RUN set -ex \
	&& apt-get update \
	&& apt-get -y --no-install-recommends install \
		libasound2 \
		libdbus-glib-1-2 \
		libdrm2 \
		libgbm1 \
		libglib2.0-0 \
		libgtk-3-0 \
		libnss3 \
		libx11-xcb1 \
		libxcb1 \
		libxtst6 \
	&& rm -rf /var/lib/apt/lists/* /var/cache/apt/*

COPY --chown=${USER_ID}:${GROUP_ID} --from=browsers-builder --link ${SELENIUM_DIR} ${SELENIUM_DIR}
COPY --chown=${USER_ID}:${GROUP_ID} --from=archivematica-dashboard-frontend-builder --link /src/src/archivematica/dashboard/frontend/node_modules /src/src/archivematica/dashboard/frontend/node_modules
COPY --link src/archivematica/dashboard/frontend /src/src/archivematica/dashboard/frontend

WORKDIR /src/src/archivematica/dashboard/frontend

ENTRYPOINT ["npm", "run", "test-single-run"]

# -----------------------------------------------------------------------------

FROM base-builder AS base

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG PYENV_DIR=/pyenv
ARG MEDIAAREA_VERSION
ARG JHOVE_VERSION

RUN set -ex \
	&& curl --retry 3 -fsSL https://packages.archivematica.org/1.18.x/key.asc | gpg --dearmor -o /etc/apt/keyrings/archivematica-1.18.x.gpg \
	&& echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/archivematica-1.18.x.gpg] http://packages.archivematica.org/1.18.x/ubuntu-externals jammy main" > /etc/apt/sources.list.d/archivematica-external.list \
	&& curl --retry 3 -so /tmp/repo-mediaarea.deb -L https://mediaarea.net/repo/deb/repo-mediaarea_${MEDIAAREA_VERSION}_all.deb \
	&& dpkg -i /tmp/repo-mediaarea.deb \
	&& rm /tmp/repo-mediaarea.deb \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		atool \
		bulk-extractor \
		clamav \
		coreutils \
		ffmpeg \
		g++ \
		gcc \
		gearman \
		gettext \
		ghostscript \
		hashdeep \
		imagemagick \
		inkscape \
		jhove=${JHOVE_VERSION} \
		libffi-dev \
		libimage-exiftool-perl \
		libldap2-dev \
		libmysqlclient-dev \
		libsasl2-dev \
		libssl-dev \
		libxml2-dev \
		libxslt1-dev \
		logapp \
		md5deep \
		mediaconch \
		mediainfo \
		nfs-common \
		openjdk-8-jre-headless \
		p7zip-full \
		pbzip2 \
		pst-utils \
		python3-lxml \
		rsync \
		siegfried \
		sleuthkit \
		tesseract-ocr \
		tree \
		unar \
		unrar-free \
		uuid \
	&& rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN set -ex \
	&& groupadd --gid ${GROUP_ID} --system archivematica \
	&& useradd --uid ${USER_ID} --gid ${GROUP_ID} --home-dir /var/archivematica --system archivematica \
	&& mkdir -p /var/archivematica/sharedDirectory \
	&& chown -R archivematica:archivematica /var/archivematica

# Download ClamAV virus signatures
RUN freshclam --quiet

USER archivematica

COPY --chown=${USER_ID}:${GROUP_ID} --from=pyenv-builder --link ${PYENV_DIR} ${PYENV_DIR}
COPY --chown=${USER_ID}:${GROUP_ID} --link . /src

ENV PYTHONPATH=/src/src

# -----------------------------------------------------------------------------

FROM base AS archivematica-mcp-client

ENV DJANGO_SETTINGS_MODULE=archivematica.MCPClient.settings.common
ENV ARCHIVEMATICA_MCPCLIENT_ARCHIVEMATICACLIENTMODULES=/src/src/archivematica/MCPClient/archivematicaClientModules
ENV ARCHIVEMATICA_MCPCLIENT_CLIENTASSETSDIRECTORY=/src/src/archivematica/MCPClient/assets/
ENV ARCHIVEMATICA_MCPCLIENT_CLIENTSCRIPTSDIRECTORY=/src/src/archivematica/MCPClient/clientScripts/

# Some scripts in archivematica-fpr-admin executed by MCPClient rely on certain
# files being available in this image (e.g. see https://git.io/vA1wF).
COPY --link src/archivematica/archivematicaCommon/externals/fido/ /usr/lib/archivematica/archivematicaCommon/externals/fido/
COPY --link src/archivematica/archivematicaCommon/externals/fiwalk_plugins/ /usr/lib/archivematica/archivematicaCommon/externals/fiwalk_plugins/

ENTRYPOINT ["pyenv", "exec", "python3", "-m", "archivematica.MCPClient.archivematicaClient"]

# -----------------------------------------------------------------------------

FROM base AS archivematica-mcp-server

ENV DJANGO_SETTINGS_MODULE=archivematica.MCPServer.settings.common

ENTRYPOINT ["pyenv", "exec", "python3", "-m", "archivematica.MCPServer.archivematicaMCP"]

# -----------------------------------------------------------------------------

FROM base AS archivematica-dashboard

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG PYTHON_VERSION=3.9

USER root

RUN set -ex \
	&& internalDirs=' \
		/src/src/archivematica/dashboard/frontend \
		/src/src/archivematica/dashboard/static \
		/src/src/archivematica/dashboard/media \
	' \
	&& mkdir -p $internalDirs \
	&& chown -R archivematica $internalDirs

USER archivematica

COPY --chown=${USER_ID}:${GROUP_ID} --from=archivematica-dashboard-frontend-builder --link /src/src/archivematica/dashboard/media/js/ /src/src/archivematica/dashboard/media/js

ENV DJANGO_SETTINGS_MODULE=archivematica.dashboard.settings.local
ENV AM_GUNICORN_BIND=0.0.0.0:8000
ENV AM_GUNICORN_CHDIR=/src/src/archivematica/dashboard
ENV FORWARDED_ALLOW_IPS=*

RUN set -ex \
	&& python3 -m archivematica.dashboard.manage collectstatic --noinput --clear \
	&& python3 -m archivematica.dashboard.manage compilemessages

ENV DJANGO_SETTINGS_MODULE=archivematica.dashboard.settings.production

EXPOSE 8000

ENTRYPOINT ["pyenv", "exec", "python3", "-m", "gunicorn", "--config=/src/src/archivematica/dashboard/install/dashboard.gunicorn-config.py", "wsgi:application"]

# -----------------------------------------------------------------------------

FROM base AS archivematica-tests

# -----------------------------------------------------------------------------

FROM base AS archivematica-dashboard-integration-tests

USER root

RUN set -ex \
	&& python3 -m playwright install-deps firefox

USER archivematica

RUN set -ex \
	&& mkdir -p /var/archivematica/.cache/ms-playwright \
	&& python3 -m playwright install firefox

# -----------------------------------------------------------------------------

FROM ${TARGET}
