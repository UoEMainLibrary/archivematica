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
import pathlib

from agentarchives.atom.client import CommunicationError
from django import forms
from django.utils.translation import gettext as _
from requests import ConnectionError
from requests import Timeout

from archivematica.dashboard.components import helpers
from archivematica.dashboard.components.archival_storage.atom import get_atom_client


class CreateAICForm(forms.Form):
    results = forms.CharField(
        label=None, required=True, widget=forms.widgets.HiddenInput()
    )


class UploadMetadataOnlyAtomForm(forms.Form):
    slug = forms.CharField(
        label=_("Insert slug"),
        help_text=_("Only compatible with AtoM 2.4 or newer."),
        required=True,
        widget=forms.TextInput(attrs={"class": "span8"}),
    )

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        client = get_atom_client()
        try:
            client.find_parent_id_for_component(slug)
        except (Timeout, ConnectionError):
            raise forms.ValidationError(
                _("Connection establishment failed: AtoM server cannot be reached.")
            )
        except CommunicationError as e:
            message = str(e)
            if "404" in message:
                raise forms.ValidationError(
                    _("Description with slug %(slug)s not found!"),
                    code="notfound",
                    params={"slug": slug},
                )
            raise forms.ValidationError(
                _("Unknown error: %(error)s"), code="error", params={"error": message}
            )
        return slug


def get_processing_configurations():
    processing_configs_dir = pathlib.Path(helpers.processing_config_path())
    suffix = "ProcessingMCP.xml"
    return (
        (processing_config, processing_config)
        for processing_config in sorted(
            path.name[: -len(suffix)]
            for path in processing_configs_dir.iterdir()
            if path.name.endswith(suffix)
        )
    )


class ReingestAIPForm(forms.Form):
    METADATA_ONLY = "metadata"
    OBJECTS = "objects"
    FULL = "full"
    REINGEST_CHOICES = (
        (METADATA_ONLY, _("Metadata re-ingest")),
        (OBJECTS, _("Partial re-ingest")),
        (FULL, _("Full re-ingest")),
    )
    reingest_type = forms.ChoiceField(
        choices=REINGEST_CHOICES, widget=forms.RadioSelect, required=True
    )
    processing_config = forms.ChoiceField(
        choices=get_processing_configurations,
        required=False,
        initial="default",
    )


class DeleteAIPForm(forms.Form):
    uuid = forms.CharField(
        label=_("Please type in the UUID to confirm"),
        required=True,
        widget=forms.TextInput(attrs={"class": "xxlarge", "placeholder": _("UUID")}),
    )
    reason = forms.CharField(
        label=_("Reason for deletion"),
        required=True,
        widget=forms.Textarea(attrs={"class": "xxlarge", "rows": "3"}),
    )

    def __init__(self, *args, **kwargs):
        self.uuid = kwargs.pop("uuid", None)
        if self.uuid is None:
            raise ValueError(_("uuid must be defined"))
        super(forms.Form, self).__init__(*args, **kwargs)

    def clean_uuid(self):
        uuid = self.cleaned_data["uuid"]
        if self.uuid != uuid:
            raise forms.ValidationError(_("UUID mismatch"))
        return uuid
