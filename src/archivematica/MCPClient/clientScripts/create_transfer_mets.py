#!/usr/bin/env python
#
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Archivematica.    If not, see <http://www.gnu.org/licenses/>.
import argparse
import json
import logging
import os
import uuid

import django
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from lxml import etree

django.setup()
import metsrw
from metsrw.label import SimpleDMD
from metsrw.dmdsecs import DMDSection

from archivematica.archivematicaCommon.archivematicaFunctions import get_dashboard_uuid
from archivematica.archivematicaCommon.countryCodes import getCodeForCountry
from archivematica.dashboard.main.models import Agent
from archivematica.dashboard.main.models import Derivation
from archivematica.dashboard.main.models import Directory
from archivematica.dashboard.main.models import File
from archivematica.dashboard.main.models import FPCommandOutput
from archivematica.dashboard.main.models import RightsStatement
from archivematica.dashboard.main.models import Transfer

PREMIS_META = metsrw.plugins.premisrw.PREMIS_3_0_META
FILE_PREMIS_META = PREMIS_META.copy()
FILE_PREMIS_META["xsi:type"] = "premis:file"
IE_PREMIS_META = PREMIS_META.copy()
IE_PREMIS_META["xsi:type"] = "premis:intellectualEntity"


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logger.info("**TEST** Logging is working inside create_transfer_mets.")

def write_mets(mets_path, transfer_dir_path, base_path_placeholder, transfer_uuid):
    """
    Writes a METS XML file to disk, containing all the data we can find.

    Args:
        mets_path: Output path for METS XML output
        transfer_dir_path: Location of the files on disk
        base_path_placeholder: The placeholder string for the base path, e.g. 'transferDirectory'
        identifier_group: The database column used to lookup file UUIDs, e.g. 'transfer_id'
        transfer_uuid: The UUID for the transfer
    """
    transfer_dir_path = os.path.expanduser(transfer_dir_path)
    transfer_dir_path = os.path.normpath(transfer_dir_path)
    db_base_path = rf"%{base_path_placeholder}%"
    mets = metsrw.METSDocument()
    mets.objid = str(transfer_uuid)

    dashboard_uuid = get_dashboard_uuid()
    if dashboard_uuid:
        agent = metsrw.Agent(
            "CREATOR",
            type="SOFTWARE",
            name=str(dashboard_uuid),
            notes=["Archivematica dashboard UUID"],
        )
        mets.agents.append(agent)

    try:
        transfer = Transfer.objects.get(uuid=transfer_uuid)
    except (Transfer.DoesNotExist, ValidationError):
        logger.info("No record in database for transfer: %s", transfer_uuid)
        raise

    if transfer.accessionid:
        alt_record_id = metsrw.AltRecordID(transfer.accessionid, type="Accession ID")
        mets.alternate_ids.append(alt_record_id)

    fsentry_tree = FSEntriesTree(transfer_dir_path, db_base_path, transfer)
    fsentry_tree.scan()

    mets.append_file(fsentry_tree.root_node)
    mets.write(mets_path, pretty_print=True)
    logger.info("**TEST** wrote initial METS to path")

    metadata_json_path = os.path.join(transfer_dir_path, "metadata.json")
    if os.path.isfile(metadata_json_path):
        logger.info("**TEST** found metadata.json at %s", metadata_json_path)

        with open(metadata_json_path) as f:
            metadata_entries = json.load(f)

        for entry in metadata_entries:
            dc_elements = {k: v for k, v in entry.items() if k.startswith("dc.")}
            logger.info("**TEST** extracted DC elements: %s", dc_elements)

            if not dc_elements:
                logger.info("**TEST** no dc elements in this entry, skipping")
                continue

            # Find first existing dmdSec with dc metadata (we assume there's at least one)
            dc_dmdsecs = [
                dmd for dmd in mets.dmdsecs if isinstance(dmd.label, SimpleDMD)
            ]
            if not dc_dmdsecs:
                logger.info("**TEST** no existing DC dmdSec found, creating one")
                dmd = SimpleDMD()
                mets.dmdsecs.append(DMDSection(dmd))
                dc_dmdsecs = [mets.dmdsecs[-1]]

            existing_dmd = dc_dmdsecs[0].label
            dc_dict = existing_dmd.to_dict()
            logger.info("**TEST** existing DC dict before merge: %s", dc_dict)

            for k, v in dc_elements.items():
                tag = k.split("dc.")[-1]
                if isinstance(v, list):
                    dc_dict[tag] = v
                else:
                    dc_dict[tag] = [v]

            logger.info("**TEST** merged DC dict: %s", dc_dict)

            new_dmd = SimpleDMD()
            for tag, values in dc_dict.items():
                for value in values:
                    new_dmd.add_dc_element(tag, value)
            dc_dmdsecs[0].label = new_dmd
            logger.info("**TEST** updated first DC dmdSec with new metadata")

        mets.write(mets_path, pretty_print=True)
        logger.info("**TEST** rewrote METS with merged metadata")

        os.remove(metadata_json_path)
        logger.info("**TEST** removed metadata.json after processing")

    else:
        logger.info("**TEST** metadata.json not found, skipping metadata merge")


def convert_to_premis_hash_function(hash_type):
    """
    Returns a PREMIS valid hash function name, if possible.
    """
    if hash_type.lower().startswith("sha") and "-" not in hash_type:
        hash_type = "SHA-" + hash_type.upper()[3:]
    elif hash_type.lower() == "md5":
        return "MD5"

    return hash_type


def clean_date(date_string):
    if date_string is None:
        return ""

    # Legacy dates may be slash seperated
    return date_string.replace("/", "-")


class FSEntriesTree:
    """
    Builds a tree of FSEntry objects from the path given, and looks up required data
    from the database.
    """

    QUERY_BATCH_SIZE = 2000
    TRANSFER_RIGHTS_LOOKUP_UUID = "45696327-44c5-4e78-849b-e027a189bf4d"
    FILE_RIGHTS_LOOKUP_UUID = "7f04d9d4-92c2-44a5-93dc-b7bfdf0c1f17"

    file_queryset_prefetches = [
        "identifiers",
        "event_set",
        "event_set__agents",
        "fileid_set",
        Prefetch(
            "original_file_set",
            queryset=Derivation.objects.filter(event__isnull=False),
            to_attr="related_has_source",
        ),
        Prefetch(
            "derived_file_set",
            queryset=Derivation.objects.filter(event__isnull=False),
            to_attr="related_is_source_of",
        ),
        Prefetch(
            "fpcommandoutput_set",
            queryset=FPCommandOutput.objects.filter(
                rule__purpose__in=["characterization", "default_characterization"]
            ),
            to_attr="characterization_documents",
        ),
    ]
    file_queryset = File.objects.prefetch_related(*file_queryset_prefetches).order_by(
        "currentlocation"
    )
    rights_prefetches = [
        "rightsstatementcopyright_set",
        "rightsstatementcopyright_set__rightsstatementcopyrightnote_set",
        "rightsstatementcopyright_set__rightsstatementcopyrightdocumentationidentifier_set",
        "rightsstatementstatuteinformation_set",
        "rightsstatementstatuteinformation_set__rightsstatementstatuteinformationnote_set",
        "rightsstatementstatuteinformation_set__rightsstatementstatutedocumentationidentifier_set",
        "rightsstatementrightsgranted_set",
        "rightsstatementrightsgranted_set__restrictions",
        "rightsstatementrightsgranted_set__notes",
    ]
    rights_queryset = RightsStatement.objects.prefetch_related(*rights_prefetches)

    def __init__(self, root_path, db_base_path, transfer):
        self.root_path = root_path
        self.db_base_path = db_base_path
        self.transfer = transfer
        self.root_node = metsrw.FSEntry(
            path=os.path.basename(root_path), type="Directory"
        )
        self.file_index = {}
        self.dir_index = {}

    def scan(self):
        self.build_tree(self.root_path, parent=self.root_node)
        self.load_file_data_from_db()
        self.load_rights_data_from_db()
        self.load_dir_uuids_from_db()
        self.check_for_missing_file_uuids()

    def get_relative_path(self, path):
        return os.path.relpath(path, start=self.root_path)

    def build_tree(self, path, parent=None):
        dir_entries = sorted(os.scandir(path), key=lambda d: d.name)
        for dir_entry in dir_entries:
            entry_relative_path = os.path.relpath(dir_entry.path, start=self.root_path)
            if dir_entry.is_dir():
                fsentry = metsrw.FSEntry(
                    path=entry_relative_path, label=dir_entry.name, type="Directory"
                )
                db_path = "".join([self.db_base_path, entry_relative_path, os.path.sep])
                self.dir_index[db_path] = fsentry
                self.build_tree(dir_entry.path, parent=fsentry)
            else:
                fsentry = metsrw.FSEntry(
                    path=entry_relative_path, label=dir_entry.name, type="Item"
                )
                db_path = "".join([self.db_base_path, entry_relative_path])
                self.file_index[db_path] = fsentry

            parent.add_child(fsentry)

    def _batch_query(self, queryset):
        offset, limit = 0, self.QUERY_BATCH_SIZE
        total_count = queryset.count()

        while offset < total_count:
            batch = queryset[offset:limit]
            yield from batch
            offset += self.QUERY_BATCH_SIZE
            limit += self.QUERY_BATCH_SIZE

    def load_file_data_from_db(self):
        file_objs = self.file_queryset.filter(
            transfer=self.transfer, removedtime__isnull=True
        )
        for file_obj in self._batch_query(file_objs):
            try:
                fsentry = self.file_index[file_obj.currentlocation.decode()]
            except KeyError:
                logger.info(
                    "File is no longer present on the filesystem: %s",
                    file_obj.currentlocation.decode(),
                )
                continue
            fsentry.file_uuid = str(file_obj.uuid)
            fsentry.checksum = file_obj.checksum
            fsentry.checksumtype = convert_to_premis_hash_function(
                file_obj.checksumtype
            )
            premis_object = file_obj_to_premis(file_obj)
            fsentry.add_premis_object(premis_object)

            for event in file_obj.event_set.all():
                premis_event = event_to_premis(event)
                fsentry.add_premis_event(premis_event)

            for agent in Agent.objects.extend_queryset_with_preservation_system(
                Agent.objects.filter(event__file_uuid=file_obj).distinct()
            ):
                premis_agent = agent_to_premis(agent)
                fsentry.add_premis_agent(premis_agent)

    def load_rights_data_from_db(self):
        transfer_rights = self.rights_queryset.filter(
            metadataappliestoidentifier=self.transfer.uuid,
            metadataappliestotype_id=self.TRANSFER_RIGHTS_LOOKUP_UUID,
        )

        for rights in transfer_rights:
            for _, fsentry in self.file_index.items():
                premis_rights = rights_to_premis(rights, fsentry.file_uuid)
                fsentry.add_premis_rights(premis_rights)

        for _, fsentry in self.file_index.items():
            file_rights = self.rights_queryset.filter(
                metadataappliestoidentifier=fsentry.file_uuid,
                metadataappliestotype_id=self.FILE_RIGHTS_LOOKUP_UUID,
            )

            for rights in file_rights:
                premis_rights = rights_to_premis(rights, fsentry.file_uuid)
                fsentry.add_premis_rights(premis_rights)

    def load_dir_uuids_from_db(self):
        dir_objs = Directory.objects.prefetch_related("identifiers").filter(
            transfer=self.transfer
        )

        for dir_obj in self._batch_query(dir_objs):
            try:
                fsentry = self.dir_index[dir_obj.currentlocation.decode()]
            except KeyError:
                logger.info(
                    "Directory is no longer present on the filesystem: %s",
                    dir_obj.currentlocation.decode(),
                )
            else:
                premis_intellectual_entity = dir_obj_to_premis(dir_obj)
                fsentry.add_premis_object(premis_intellectual_entity)

    def check_for_missing_file_uuids(self):
        missing = []
        for path, fsentry in self.file_index.items():
            if fsentry.file_uuid is None:
                logger.info("No record in database for file: %s", path)
                missing.append(path)

        # update our index after, so we don't modify the dict during iteration
        for path in missing:
            fsentry = self.file_index[path]
            fsentry.parent.remove_child(fsentry)
            del self.file_index[path]


def get_premis_format_data(file_ids):
    format_data = ()

    for file_id in file_ids:
        format_data += (
            "format",
            (
                "format_designation",
                ("format_name", file_id.format_name),
                ("format_version", file_id.format_version),
            ),
            (
                "format_registry",
                ("format_registry_name", file_id.format_registry_name),
                ("format_registry_key", file_id.format_registry_key),
            ),
        )
    if not format_data:
        # default to unknown
        format_data = ("format", ("format_designation", ("format_name", "Unknown")))

    return format_data


def get_premis_relationship_data(derivations, originals):
    relationship_data = ()

    for derivation in derivations:
        relationship_data += (
            (
                "relationship",
                ("relationship_type", "derivation"),
                ("relationship_sub_type", "is source of"),
                (
                    "related_object_identification",
                    ("related_object_identifier_type", "UUID"),
                    (
                        "related_object_identifier_value",
                        str(derivation.derived_file_id),
                    ),
                ),
                (
                    "related_event_identification",
                    ("related_event_identifier_type", "UUID"),
                    ("related_event_identifier_value", str(derivation.event_id)),
                ),
            ),
        )
    for original in originals:
        relationship_data += (
            (
                "relationship",
                ("relationship_type", "derivation"),
                ("relationship_sub_type", "has source"),
                (
                    "related_object_identification",
                    ("related_object_identifier_type", "UUID"),
                    ("related_object_identifier_value", str(original.source_file_id)),
                ),
                (
                    "related_event_identification",
                    ("related_event_identifier_type", "UUID"),
                    ("related_event_identifier_value", str(original.event_id)),
                ),
            ),
        )

    return relationship_data


def get_premis_object_characteristics_extension(documents):
    extensions = ()

    for document in documents:
        # lxml will complain if we pass unicode, even if the XML
        # is UTF-8 encoded.
        # See https://lxml.de/parsing.html#python-unicode-strings
        # This only covers the UTF-8 and ASCII cases.
        xml_element = etree.fromstring(document.content.encode("utf-8"))

        extensions += (("object_characteristics_extension", xml_element),)

    return extensions


def get_premis_rights_documentation_identifiers(rights_type, identifiers):
    if rights_type == "otherrights":
        tag_name = "other_rights_documentation_identifier"
        type_tag_name = "other_rights_documentation_identifier_type"
        value_tag_name = "other_rights_documentation_identifier_value"
        role_tag_name = "other_rights_documentation_role"
    else:
        tag_name = f"{rights_type}_documentation_identifier"
        type_tag_name = f"{rights_type}_documentation_identifier_type"
        value_tag_name = f"{rights_type}_documentation_identifier_value"
        role_tag_name = f"{rights_type}_documentation_role"

    data = ()
    for identifier in identifiers:
        identifier_type = getattr(
            identifier, f"{rights_type}documentationidentifiertype"
        )
        identifier_value = getattr(
            identifier, f"{rights_type}documentationidentifiervalue"
        )
        identifier_role = (
            getattr(identifier, f"{rights_type}documentationidentifierrole") or ""
        )

        data += (
            (
                tag_name,
                (type_tag_name, identifier_type),
                (value_tag_name, identifier_value),
                (role_tag_name, identifier_role),
            ),
        )

    return data


def get_premis_rights_applicable_dates(rights_type, rights_obj):
    if rights_type == "otherrights":
        tag_name = "other_rights_applicable_dates"
    else:
        tag_name = f"{rights_type}_applicable_dates"

    start_date = getattr(rights_obj, f"{rights_type}applicablestartdate")
    end_date_open = getattr(rights_obj, f"{rights_type}enddateopen", False)
    if end_date_open:
        end_date = "OPEN"
    else:
        end_date = getattr(rights_obj, f"{rights_type}applicableenddate")

    data = (tag_name,)
    if start_date:
        data += (("start_date", clean_date(start_date)),)
    if end_date:
        data += (("end_date", clean_date(end_date)),)

    return data


def get_premis_copyright_information(rights):
    premis_data = ()

    for copyright_section in rights.rightsstatementcopyright_set.all():
        copyright_jurisdiction_code = (
            getCodeForCountry(str(copyright_section.copyrightjurisdiction).upper())
            or ""
        )
        determination_date = clean_date(
            copyright_section.copyrightstatusdeterminationdate
        )

        copyright_info = (
            "copyright_information",
            ("copyright_status", copyright_section.copyrightstatus),
            ("copyright_jurisdiction", copyright_jurisdiction_code),
            ("copyright_status_determination_date", determination_date),
        )
        for note in copyright_section.rightsstatementcopyrightnote_set.all():
            copyright_info += (("copyright_note", note.copyrightnote),)
        copyright_info += get_premis_rights_documentation_identifiers(
            "copyright",
            copyright_section.rightsstatementcopyrightdocumentationidentifier_set.all(),
        )
        copyright_info += (
            get_premis_rights_applicable_dates("copyright", copyright_section),
        )

        premis_data += copyright_info

    return premis_data


def get_premis_license_information(rights):
    premis_data = ()

    for license_section in rights.rightsstatementlicense_set.all():
        license_information = ("license_information",)
        license_information += get_premis_rights_documentation_identifiers(
            "license",
            license_section.rightsstatementlicensedocumentationidentifier_set.all(),
        )
        license_information += (("license_terms", license_section.licenseterms or ""),)
        for note in license_section.rightsstatementlicensenote_set.all():
            license_information += (("license_note", note.licensenote),)
        license_information += (
            get_premis_rights_applicable_dates("license", license_section),
        )

        premis_data += license_information

    return premis_data


def get_premis_statute_information(rights):
    premis_data = ()

    for statute_obj in rights.rightsstatementstatuteinformation_set.all():
        determination_date = clean_date(statute_obj.statutedeterminationdate)
        statute_info = (
            "statute_information",
            ("statute_jurisdiction", statute_obj.statutejurisdiction),
            ("statute_citation", statute_obj.statutecitation),
            ("statute_information_determination_date", determination_date),
        )
        for note in statute_obj.rightsstatementstatuteinformationnote_set.all():
            statute_info += (("statute_note", note.statutenote),)
        statute_info += get_premis_rights_documentation_identifiers(
            "statute",
            statute_obj.rightsstatementstatutedocumentationidentifier_set.all(),
        )
        statute_info += (get_premis_rights_applicable_dates("statute", statute_obj),)

        premis_data += statute_info

    return premis_data


def get_premis_other_rights_information(rights):
    premis_data = ()

    for other_obj in rights.rightsstatementotherrightsinformation_set.all():
        other_rights_basis = other_obj.otherrightsbasis or rights.rightsbasis
        other_information = ("other_rights_information",)
        other_information += get_premis_rights_documentation_identifiers(
            "otherrights",
            other_obj.rightsstatementotherrightsdocumentationidentifier_set.all(),
        )
        other_information += (("other_rights_basis", other_rights_basis),)
        other_information += (
            get_premis_rights_applicable_dates("otherrights", other_obj),
        )

        for note in other_obj.rightsstatementotherrightsinformationnote_set.all():
            other_information += (("other_rights_note", note.otherrightsnote),)

        premis_data += other_information

    return premis_data


def get_premis_rights_granted(rights):
    rights_granted_info = ()
    for rights_granted in rights.rightsstatementrightsgranted_set.all():
        start_date = clean_date(rights_granted.startdate)
        if rights_granted.enddateopen:
            end_date = "OPEN"
        else:
            end_date = clean_date(rights_granted.enddate)

        rights_granted_info += ("rights_granted", ("act", rights_granted.act))

        for restriction in rights_granted.restrictions.all():
            if restriction.restriction.lower() == "allow":
                grant_tag = "term_of_grant"
            else:
                grant_tag = "term_of_restriction"
            rights_granted_info += (
                ("restriction", restriction.restriction),
                (grant_tag, ("start_date", start_date), ("end_date", end_date)),
            )

        for note in rights_granted.notes.all():
            rights_granted_info += (("rights_granted_note", note.rightsgrantednote),)

    return rights_granted_info


def get_premis_object_identifiers(uuid, additional_identifiers):
    object_identifiers = (
        (
            "object_identifier",
            ("object_identifier_type", "UUID"),
            ("object_identifier_value", str(uuid)),
        ),
    )
    for identifier in additional_identifiers:
        object_identifiers += (
            (
                "object_identifier",
                ("object_identifier_type", identifier.type),
                ("object_identifier_value", str(identifier.value)),
            ),
        )

    return object_identifiers


def file_obj_to_premis(file_obj):
    """
    Converts an File model object to a PREMIS event object via metsrw.

    Returns:
        lxml.etree._Element
    """
    premis_digest_algorithm = convert_to_premis_hash_function(file_obj.checksumtype)
    format_data = get_premis_format_data(file_obj.fileid_set.all())
    original_name = file_obj.originallocation.decode()
    object_identifiers = get_premis_object_identifiers(
        file_obj.uuid, file_obj.identifiers.all()
    )
    object_characteristics_extensions = get_premis_object_characteristics_extension(
        file_obj.characterization_documents
    )
    relationship_data = get_premis_relationship_data(
        file_obj.related_is_source_of, file_obj.related_has_source
    )

    object_characteristics = (
        "object_characteristics",
        ("composition_level", "0"),
        (
            "fixity",
            ("message_digest_algorithm", premis_digest_algorithm),
            ("message_digest", file_obj.checksum),
        ),
        ("size", str(file_obj.size)),
        format_data,
        (
            "creating_application",
            (
                "date_created_by_application",
                file_obj.modificationtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        ),
    )

    if object_characteristics_extensions:
        object_characteristics += object_characteristics_extensions

    premis_data = (
        ("object", FILE_PREMIS_META)
        + object_identifiers
        + (object_characteristics, ("original_name", original_name))
        + relationship_data
    )
    return metsrw.plugins.premisrw.data_to_premis(
        premis_data, premis_version=FILE_PREMIS_META["version"]
    )


def dir_obj_to_premis(dir_obj):
    """
    Converts an Directory model object to a PREMIS object via metsrw.

    Returns:
        lxml.etree._Element
    """
    original_name = dir_obj.originallocation.decode()
    object_identifiers = get_premis_object_identifiers(
        dir_obj.uuid, dir_obj.identifiers.all()
    )

    premis_data = (
        ("object", IE_PREMIS_META)
        + object_identifiers
        + (("original_name", original_name),)
    )

    return metsrw.plugins.premisrw.data_to_premis(
        premis_data, premis_version=IE_PREMIS_META["version"]
    )


def event_to_premis(event):
    """
    Converts an Event model to a PREMIS event object via metsrw.

    Returns:
        lxml.etree._Element
    """
    premis_data = (
        "event",
        PREMIS_META,
        (
            "event_identifier",
            ("event_identifier_type", "UUID"),
            ("event_identifier_value", str(event.event_id)),
        ),
        ("event_type", event.event_type),
        ("event_date_time", event.event_datetime),
        ("event_detail_information", ("event_detail", event.event_detail)),
        (
            "event_outcome_information",
            ("event_outcome", event.event_outcome),
            (
                "event_outcome_detail",
                ("event_outcome_detail_note", event.event_outcome_detail),
            ),
        ),
    )
    for agent in Agent.objects.extend_queryset_with_preservation_system(
        event.agents.all()
    ):
        premis_data += (
            (
                "linking_agent_identifier",
                ("linking_agent_identifier_type", agent.identifiertype or ""),
                ("linking_agent_identifier_value", str(agent.identifiervalue or "")),
            ),
        )

    return metsrw.plugins.premisrw.data_to_premis(
        premis_data, premis_version=PREMIS_META["version"]
    )


def agent_to_premis(agent):
    """
    Converts an Agent model to a PREMIS event object via metsrw.

    Returns:
        lxml.etree._Element
    """
    premis_data = (
        "agent",
        PREMIS_META,
        (
            "agent_identifier",
            ("agent_identifier_type", agent.identifiertype or ""),
            ("agent_identifier_value", str(agent.identifiervalue or "")),
        ),
        ("agent_name", agent.name or ""),
        ("agent_type", agent.agenttype),
    )

    return metsrw.plugins.premisrw.data_to_premis(
        premis_data, premis_version=PREMIS_META["version"]
    )


def rights_to_premis(rights, file_uuid):
    """
    Converts an RightsStatement model to a PREMIS event object via metsrw.

    Returns:
        lxml.etree._Element
    """
    VALID_RIGHTS_BASES = ["copyright", "institutional policy", "license", "statute"]

    if rights.rightsstatementidentifiervalue:
        id_type = rights.rightsstatementidentifiertype
        id_value = rights.rightsstatementidentifiervalue
    else:
        id_type = "UUID"
        id_value = str(uuid.uuid4())

    # TODO: this logic should be in metsrw
    if rights.rightsbasis.lower() in VALID_RIGHTS_BASES:
        rights_basis = rights.rightsbasis
    else:
        rights_basis = "Other"

    rights_statement = (
        "rights_statement",
        (
            "rights_statement_identifier",
            ("rights_statement_identifier_type", id_type),
            ("rights_statement_identifier_value", str(id_value)),
        ),
        ("rights_basis", rights_basis),
    )

    basis_type = rights_basis.lower()
    if basis_type == "copyright":
        copyright_info = get_premis_copyright_information(rights)
        if copyright_info:
            rights_statement += (copyright_info,)
    elif basis_type == "license":
        license_info = get_premis_license_information(rights)
        if license_info:
            rights_statement += (license_info,)
    elif basis_type == "statute":
        statute_info = get_premis_statute_information(rights)
        if statute_info:
            rights_statement += (statute_info,)
    elif basis_type == "other":
        other_info = get_premis_other_rights_information(rights)
        if other_info:
            rights_statement += (other_info,)

    rights_info = get_premis_rights_granted(rights)
    if rights_info:
        rights_statement += (rights_info,)

    rights_statement += (
        (
            "linking_object_identifier",
            ("linking_object_identifier_type", "UUID"),
            ("linking_object_identifier_value", str(file_uuid)),
        ),
    )

    premis_data = ("rights", PREMIS_META, rights_statement)

    return metsrw.plugins.premisrw.data_to_premis(
        premis_data, premis_version=PREMIS_META["version"]
    )


def call(jobs):
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--basePath", dest="base_path")
    parser.add_argument(
        "-b", "--basePathString", dest="base_path_string", default="SIPDirectory"
    )
    parser.add_argument("-S", "--sipUUID", dest="sip_uuid")
    parser.add_argument("-x", "--xmlFile", dest="xml_file")

    for job in jobs:
        with job.JobContext(logger=logger):
            print("**TEST** finished create_transfer_mets")
            logger.info("**TEST** about to start create_transfer_mets")
            args = parser.parse_args(job.args[1:])
            write_mets(
                args.xml_file, args.base_path, args.base_path_string, args.sip_uuid
            )
            logger.info("**TEST** finished create_transfer_mets")
