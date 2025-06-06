# This file is part of Archivematica.
#
# Copyright 2010-2015 Artefactual Systems Inc. <http://artefactual.com>
#
# Archivematica is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Archivematica is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Archivematica.  If not, see <http://www.gnu.org/licenses/>.
import configparser
import importlib.util
import json
import logging.config
import multiprocessing
import os
from io import StringIO
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from archivematica.archivematicaCommon import email_settings
from archivematica.archivematicaCommon.appconfig import Config
from archivematica.archivematicaCommon.appconfig import process_search_enabled


def _get_settings_from_file(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as err:
        raise ImproperlyConfigured(f"{path} could not be imported: {err}")
    if hasattr(module, "__all__"):
        attrs = module.__all__
    else:
        attrs = [attr for attr in dir(module) if not attr.startswith("_")]
    return {attr: getattr(module, attr) for attr in attrs}


def workers(config, section):
    try:
        return config.config.getint(section, "workers")
    except (configparser.Error, ValueError):
        return multiprocessing.cpu_count()


CONFIG_MAPPING = {
    # [MCPClient]
    "workers": {
        "section": "MCPClient",
        "option": "workers",
        "process_function": workers,
    },
    "max_tasks_per_child": {
        "section": "MCPClient",
        "option": "max_tasks_per_child",
        "type": "int",
    },
    "shared_directory": {
        "section": "MCPClient",
        "option": "sharedDirectoryMounted",
        "type": "string",
    },
    "processing_directory": {
        "section": "MCPClient",
        "option": "processingDirectory",
        "type": "string",
    },
    "rejected_directory": {
        "section": "MCPClient",
        "option": "rejectedDirectory",
        "type": "string",
    },
    "watch_directory": {
        "section": "MCPClient",
        "option": "watchDirectoryPath",
        "type": "string",
    },
    "client_assets_directory": {
        "section": "MCPClient",
        "option": "clientAssetsDirectory",
        "type": "string",
    },
    "gearman_server": {
        "section": "MCPClient",
        "option": "MCPArchivematicaServer",
        "type": "string",
    },
    "client_modules_file": {
        "section": "MCPClient",
        "option": "archivematicaClientModules",
        "type": "string",
    },
    "elasticsearch_server": {
        "section": "MCPClient",
        "option": "elasticsearchServer",
        "type": "string",
    },
    "elasticsearch_timeout": {
        "section": "MCPClient",
        "option": "elasticsearchTimeout",
        "type": "float",
    },
    "search_enabled": {
        "section": "MCPClient",
        "process_function": process_search_enabled,
    },
    "index_aip_continue_on_error": {
        "section": "MCPClient",
        "option": "index_aip_continue_on_error",
        "type": "boolean",
    },
    "capture_client_script_output": {
        "section": "MCPClient",
        "option": "capture_client_script_output",
        "type": "boolean",
    },
    "removable_files": {
        "section": "MCPClient",
        "option": "removableFiles",
        "type": "string",
    },
    "temp_directory": {"section": "MCPClient", "option": "temp_dir", "type": "string"},
    "secret_key": {
        "section": "MCPClient",
        "option": "django_secret_key",
        "type": "string",
    },
    "storage_service_client_timeout": {
        "section": "MCPClient",
        "option": "storage_service_client_timeout",
        "type": "float",
    },
    "storage_service_client_quick_timeout": {
        "section": "MCPClient",
        "option": "storage_service_client_quick_timeout",
        "type": "float",
    },
    "agentarchives_client_timeout": {
        "section": "MCPClient",
        "option": "agentarchives_client_timeout",
        "type": "float",
    },
    "prometheus_bind_address": {
        "section": "MCPClient",
        "option": "prometheus_bind_address",
        "type": "string",
    },
    "prometheus_bind_port": {
        "section": "MCPClient",
        "option": "prometheus_bind_port",
        "type": "string",
    },
    "prometheus_detailed_metrics": {
        "section": "MCPClient",
        "option": "prometheus_detailed_metrics",
        "type": "boolean",
    },
    "metadata_xml_validation_enabled": {
        "section": "MCPClient",
        "option": "metadata_xml_validation_enabled",
        "type": "boolean",
    },
    "time_zone": {"section": "MCPClient", "option": "time_zone", "type": "string"},
    # [antivirus]
    "clamav_server": {
        "section": "MCPClient",
        "option": "clamav_server",
        "type": "string",
    },
    "clamav_pass_by_stream": {
        "section": "MCPClient",
        "option": "clamav_pass_by_stream",
        "type": "boolean",
    },
    "clamav_client_timeout": {
        "section": "MCPClient",
        "option": "clamav_client_timeout",
        "type": "float",
    },
    "clamav_client_backend": {
        "section": "MCPClient",
        "option": "clamav_client_backend",
        "type": "string",
    },
    # float for megabytes to preserve fractions on in-code operations on bytes
    "clamav_client_max_file_size": {
        "section": "MCPClient",
        "option": "clamav_client_max_file_size",
        "type": "float",
    },
    "clamav_client_max_scan_size": {
        "section": "MCPClient",
        "option": "clamav_client_max_scan_size",
        "type": "float",
    },
    # [client]
    "db_engine": {"section": "client", "option": "engine", "type": "string"},
    "db_name": {"section": "client", "option": "database", "type": "string"},
    "db_user": {"section": "client", "option": "user", "type": "string"},
    "db_password": {"section": "client", "option": "password", "type": "string"},
    "db_host": {"section": "client", "option": "host", "type": "string"},
    "db_port": {"section": "client", "option": "port", "type": "string"},
}

CONFIG_MAPPING.update(email_settings.CONFIG_MAPPING)

CONFIG_DEFAULTS = """[MCPClient]
MCPArchivematicaServer = localhost:4730
sharedDirectoryMounted = /var/archivematica/sharedDirectory/
watchDirectoryPath = /var/archivematica/sharedDirectory/watchedDirectories/
processingDirectory = /var/archivematica/sharedDirectory/currentlyProcessing/
rejectedDirectory = /var/archivematica/sharedDirectory/rejected/
archivematicaClientModules = /usr/lib/archivematica/MCPClient/archivematicaClientModules
clientAssetsDirectory = /usr/lib/archivematica/MCPClient/assets/
elasticsearchServer = localhost:9200
elasticsearchTimeout = 10
search_enabled = true
metadata_xml_validation_enabled = false
index_aip_continue_on_error = false
capture_client_script_output = true
temp_dir = /var/archivematica/sharedDirectory/tmp
removableFiles = Thumbs.db, Icon, Icon\r, .DS_Store
clamav_server = /var/run/clamav/clamd.ctl
clamav_pass_by_stream = True
storage_service_client_timeout = 86400
storage_service_client_quick_timeout = 5
agentarchives_client_timeout = 300
prometheus_bind_address =
prometheus_bind_port =
prometheus_detailed_metrics = false
time_zone = UTC
workers =
max_tasks_per_child = 10
clamav_client_timeout = 86400
clamav_client_backend = clamdscanner    ; Options: clamdscanner or clamscanner
clamav_client_max_file_size = 42        ; MB
clamav_client_max_scan_size = 42        ; MB


[client]
user = archivematica
password = demo
host = localhost
database = MCP
port = 3306
engine = django.db.backends.mysql

[email]
backend = django.core.mail.backends.console.EmailBackend
host = smtp.gmail.com
host_password =
host_user = your_email@example.com
port = 587
ssl_certfile =
ssl_keyfile =
use_ssl = False
use_tls = True
file_path =
default_from_email = webmaster@example.com
subject_prefix = [Archivematica]
timeout = 300
#server_email =
"""

config = Config(env_prefix="ARCHIVEMATICA_MCPCLIENT", attrs=CONFIG_MAPPING)
config.read_defaults(StringIO(CONFIG_DEFAULTS))
config.read_files(
    [
        "/etc/archivematica/archivematicaCommon/dbsettings",
        "/etc/archivematica/MCPClient/clientConfig.conf",
    ]
)


DATABASES = {
    "default": {
        "ENGINE": config.get("db_engine"),
        "NAME": config.get("db_name"),
        "USER": config.get("db_user"),
        "PASSWORD": config.get("db_password"),
        "HOST": config.get("db_host"),
        "PORT": config.get("db_port"),
        "CONN_MAX_AGE": 3600,  # 1 hour
    }
}

# Make this unique, and don't share it with anybody.
SECRET_KEY = config.get(
    "secret_key", default="e7b-$#-3fgu)j1k01)3tp@^e0=yv1hlcc4k-b6*ap^zezv2$48"
)

USE_TZ = True
TIME_ZONE = config.get("time_zone")

INSTALLED_APPS = (
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "archivematica.dashboard.components.accounts",
    "archivematica.dashboard.main",
    "archivematica.dashboard.components.mcp",
    "archivematica.dashboard.components.administration",
    "archivematica.dashboard.fpr",
    # Only needed because archivematicaClient calls django.setup()
    # which imports the ApiAccess model through the helpers module of
    # the dashboard
    "tastypie",
)

# Configure logging manually
LOGGING_CONFIG = None

# Location of the logging configuration file that we're going to pass to
# `logging.config.fileConfig` unless it doesn't exist.
LOGGING_CONFIG_FILE = "/etc/archivematica/clientConfig.logging.json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detailed": {
            "format": "%(levelname)-8s  %(asctime)s  %(name)s:%(module)s:%(funcName)s:%(lineno)d:  %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "detailed",
        }
    },
    "loggers": {"archivematica": {"level": "DEBUG"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

if os.path.isfile(LOGGING_CONFIG_FILE):
    with open(LOGGING_CONFIG_FILE) as f:
        logging.config.dictConfig(json.load(f))
else:
    logging.config.dictConfig(LOGGING)


WORKERS = config.get("workers")
MAX_TASKS_PER_CHILD = config.get("max_tasks_per_child")
SHARED_DIRECTORY = config.get("shared_directory")
PROCESSING_DIRECTORY = config.get("processing_directory")
REJECTED_DIRECTORY = config.get("rejected_directory")
WATCH_DIRECTORY = config.get("watch_directory")
CLIENT_ASSETS_DIRECTORY = config.get("client_assets_directory")
GEARMAN_SERVER = config.get("gearman_server")
CLIENT_MODULES_FILE = config.get("client_modules_file")
REMOVABLE_FILES = config.get("removable_files")
TEMP_DIRECTORY = config.get("temp_directory")
ELASTICSEARCH_SERVER = config.get("elasticsearch_server")
ELASTICSEARCH_TIMEOUT = config.get("elasticsearch_timeout")
CLAMAV_SERVER = config.get("clamav_server")
CLAMAV_PASS_BY_STREAM = config.get("clamav_pass_by_stream")
CLAMAV_CLIENT_TIMEOUT = config.get("clamav_client_timeout")
CLAMAV_CLIENT_BACKEND = config.get("clamav_client_backend")
CLAMAV_CLIENT_MAX_FILE_SIZE = config.get("clamav_client_max_file_size")
CLAMAV_CLIENT_MAX_SCAN_SIZE = config.get("clamav_client_max_scan_size")
STORAGE_SERVICE_CLIENT_TIMEOUT = config.get("storage_service_client_timeout")
STORAGE_SERVICE_CLIENT_QUICK_TIMEOUT = config.get(
    "storage_service_client_quick_timeout"
)
AGENTARCHIVES_CLIENT_TIMEOUT = config.get("agentarchives_client_timeout")
SEARCH_ENABLED = config.get("search_enabled")
INDEX_AIP_CONTINUE_ON_ERROR = config.get("index_aip_continue_on_error")
CAPTURE_CLIENT_SCRIPT_OUTPUT = config.get("capture_client_script_output")
DEFAULT_CHECKSUM_ALGORITHM = "sha256"
PROMETHEUS_DETAILED_METRICS = config.get("prometheus_detailed_metrics")
PROMETHEUS_BIND_ADDRESS = config.get("prometheus_bind_address")
try:
    PROMETHEUS_BIND_PORT = int(config.get("prometheus_bind_port"))
except ValueError:
    PROMETHEUS_ENABLED = False
else:
    PROMETHEUS_ENABLED = True

TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates"}]

# Apply email settings
globals().update(email_settings.get_settings(config))

METADATA_XML_VALIDATION_ENABLED = config.get("metadata_xml_validation_enabled")
if METADATA_XML_VALIDATION_ENABLED:
    METADATA_XML_VALIDATION_SETTINGS_FILE = os.environ.get(
        "METADATA_XML_VALIDATION_SETTINGS_FILE", ""
    )
    if METADATA_XML_VALIDATION_SETTINGS_FILE:
        xml_validation_settings = _get_settings_from_file(
            Path(METADATA_XML_VALIDATION_SETTINGS_FILE)
        )
        XML_VALIDATION = xml_validation_settings.get("XML_VALIDATION")
        XML_VALIDATION_FAIL_ON_ERROR = xml_validation_settings.get(
            "XML_VALIDATION_FAIL_ON_ERROR"
        )
        if not isinstance(XML_VALIDATION, dict) or not isinstance(
            XML_VALIDATION_FAIL_ON_ERROR, bool
        ):
            raise ImproperlyConfigured(
                f"The metadata XML validation settings file {METADATA_XML_VALIDATION_SETTINGS_FILE} does not contain "
                "the right settings: an XML_VALIDATION dictionary and an XML_VALIDATION_FAIL_ON_ERROR boolean"
            )
