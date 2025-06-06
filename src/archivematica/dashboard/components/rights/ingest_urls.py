# This file is part of Archivematica.
#
# Copyright 2010-2013 Artefactual Systems Inc. <http://artefactual.com>
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
from django.urls import path

from archivematica.dashboard.components.rights import views

app_name = "rights_ingest"
urlpatterns = [
    path("", views.ingest_rights_list, name="index"),
    path("add/", views.ingest_rights_edit, name="add"),
    path("delete/<int:id>/", views.ingest_rights_delete),
    path(
        "grants/<int:id>/delete/",
        views.ingest_rights_grant_delete,
        name="grant_delete",
    ),
    path("grants/<int:id>/", views.ingest_rights_grants_edit, name="grants_edit"),
    path("<int:id>/", views.ingest_rights_edit, name="edit"),
]
