# This file is part of Archivematica.
#
# Copyright 2010-2016 Artefactual Systems Inc. <http://artefactual.com>
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
from django.conf import settings
from django.urls import path
from django.urls import re_path

from archivematica.dashboard.components.unit import views

app_name = "unit"
# The first segment of these urls is '^(?P<unit_type>transfer|ingest)/'
# All views should expect a first parameter of unit_type, with a value of 'transfer' or 'ingest'
urlpatterns = [
    re_path(
        r"^(?P<unit_uuid>" + settings.UUID_REGEX + ")/$", views.detail, name="detail"
    ),
    re_path(
        r"^(?P<unit_uuid>" + settings.UUID_REGEX + ")/microservices/$",
        views.microservices,
        name="microservices",
    ),
    re_path(
        r"^(?P<unit_uuid>" + settings.UUID_REGEX + ")/delete/$",
        views.mark_hidden,
        name="mark_hidden",
    ),
    path("delete/", views.mark_completed_hidden, name="mark_all_hidden"),
]
