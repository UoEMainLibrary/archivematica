#!/usr/bin/env python
import os
import shutil
import sys
from pprint import pformat

import django

django.setup()
from bagit import Bag
from bagit import BagError
from django.conf import settings as mcpclient_settings
from django.db import transaction

from archivematica.archivematicaCommon import databaseFunctions
from archivematica.archivematicaCommon.archivematicaFunctions import get_setting
from archivematica.archivematicaCommon.executeOrRunSubProcess import executeOrRun
from archivematica.dashboard.main.models import SIP
from archivematica.dashboard.main.models import File


class VerifyChecksumsError(Exception):
    """Checksum verification has failed."""


def extract_aip(job, aip_path, extract_path):
    os.makedirs(extract_path)
    command = f"atool --extract-to={extract_path} -V0 {aip_path}"
    job.pyprint("Running extraction command:", command)
    exit_code, stdout, stderr = executeOrRun(
        "command", command, printing=True, capture_output=True
    )
    job.write_output(stdout)
    job.write_error(stderr)
    if exit_code != 0:
        raise Exception("Error extracting AIP")

    aip_identifier, ext = os.path.splitext(os.path.basename(aip_path))
    if ext in (".bz2", ".gz"):
        aip_identifier, _ = os.path.splitext(aip_identifier)
    return os.path.join(extract_path, aip_identifier)


def write_premis_event(
    job, sip_uuid, checksum_type, event_outcome, event_outcome_detail_note
):
    """Write the AIP-level "fixity check" PREMIS event."""
    try:
        databaseFunctions.insertIntoEvents(
            fileUUID=sip_uuid,
            eventType="fixity check",
            eventDetail=f'program="python, bag"; module="hashlib.{checksum_type}()"',
            eventOutcome=event_outcome,
            eventOutcomeDetailNote=event_outcome_detail_note,
        )
    except Exception as err:
        job.pyprint(f"Failed to write PREMIS event to database. Error: {err}")
    else:
        return event_outcome_detail_note


def assert_checksum_types_match(job, file_, sip_uuid, settings_checksum_type):
    """Raise exception if checksum types (i.e., algorithms, e.g., 'sha256') of
    the file and the settings do not match.
    """
    if file_.checksumtype != settings_checksum_type:
        event_outcome_detail_note = (
            f"The checksum type of file {file_.uuid} is"
            f" {file_.checksumtype}; given the current application settings, we"
            f" expect it to {settings_checksum_type}"
        )
        raise VerifyChecksumsError(
            write_premis_event(
                job, sip_uuid, settings_checksum_type, "Fail", event_outcome_detail_note
            )
        )


def get_expected_checksum(
    job, bag, file_, sip_uuid, checksum_type, file_path, is_reingest
):
    """Raise an exception if an expected checksum cannot be found in the
    Bag manifest.
    """
    try:
        return bag.entries[file_path][checksum_type]
    except KeyError:
        if is_reingest:
            return None
        event_outcome_detail_note = (
            f"Unable to find expected path {file_path} for file"
            f" {file_.uuid} in the following mapping from file paths to"
            f" checksums: {pformat(bag.entries)}:"
        )
        raise VerifyChecksumsError(
            write_premis_event(
                job, sip_uuid, checksum_type, "Fail", event_outcome_detail_note
            )
        )


def assert_checksums_match(job, file_, sip_uuid, checksum_type, expected_checksum):
    """Raise an exception if checksums do not match."""
    if file_.checksum != expected_checksum:
        event_outcome_detail_note = (
            f"The checksum {file_.checksum} for file {file_.uuid} from the"
            " database did not match the corresponding checksum from the"
            f" Bag manifest file {expected_checksum}"
        )
        raise VerifyChecksumsError(
            write_premis_event(
                job, sip_uuid, checksum_type, "Fail", event_outcome_detail_note
            )
        )


def verify_checksums(job, bag, sip_uuid):
    """Verify that the checksums generated at the beginning of transfer match
    those generated near the end of ingest by bag, i.e., "Prepare AIP"
    (bagit_v0.0).
    """
    is_reingest = "REIN" in SIP.objects.get(uuid=sip_uuid).sip_type
    checksum_type = get_setting(
        "checksum_type", mcpclient_settings.DEFAULT_CHECKSUM_ALGORITHM
    )
    removableFiles = {e.strip() for e in mcpclient_settings.REMOVABLE_FILES.split(",")}
    try:
        verification_count = 0
        verification_skipped_because_reingest = 0
        for file_ in File.objects.filter(sip_id=sip_uuid):
            if (
                os.path.basename(file_.originallocation.decode()) in removableFiles
                or file_.removedtime
                or not file_.currentlocation.decode().startswith(
                    "%SIPDirectory%objects/"
                )
                or file_.filegrpuse == "manualNormalization"
            ):
                continue
            file_path = os.path.join(
                "data", file_.currentlocation.decode().replace("%SIPDirectory%", "", 1)
            )
            assert_checksum_types_match(job, file_, sip_uuid, checksum_type)
            expected_checksum = get_expected_checksum(
                job, bag, file_, sip_uuid, checksum_type, file_path, is_reingest
            )
            if expected_checksum is None:
                verification_skipped_because_reingest += 1
                continue
            assert_checksums_match(
                job, file_, sip_uuid, checksum_type, expected_checksum
            )
            verification_count += 1
    except VerifyChecksumsError as err:
        job.print_error(repr(err))
        raise
    event_outcome_detail_note = (
        f"All checksums (count={verification_count}) generated at start of"
        " transfer match those generated by BagIt (bag)."
    )
    if verification_skipped_because_reingest:
        event_outcome_detail_note += (
            f" Note that checksum verification was skipped for {verification_skipped_because_reingest}"
            " file(s) because this AIP is being re-ingested and the re-ingest"
            " payload did not include said file(s)."
        )
    write_premis_event(job, sip_uuid, checksum_type, "Pass", event_outcome_detail_note)
    job.pyprint(event_outcome_detail_note)


def verify_aip(job):
    """Verify the AIP was bagged correctly by extracting it and running
    verification on its contents. This is also where we verify the checksums
    now that the verifyPREMISChecksums_v0.0 ("Verify checksums generated on
    ingest") micro-service has been removed. It was removed because verifying
    checksums by calculating them in that MS and then having bagit calculate
    them here was redundant.

    job.args[1] = UUID
      UUID of the SIP, which will become the UUID of the AIP
    job.args[2] = current location
      Full absolute path to the AIP's current location on the local filesystem
    """

    sip_uuid = job.args[1]  # %sip_uuid%
    aip_path = job.args[2]  # SIPDirectory%%sip_name%-%sip_uuid%.7z

    temp_dir = mcpclient_settings.TEMP_DIRECTORY

    is_uncompressed_aip = os.path.isdir(aip_path)

    if is_uncompressed_aip:
        bag_path = aip_path
    else:
        try:
            extract_dir = os.path.join(temp_dir, sip_uuid)
            bag_path = extract_aip(job, aip_path, extract_dir)
        except Exception as err:
            job.print_error(repr(err))
            job.pyprint(f'Error extracting AIP at "{aip_path}"', file=sys.stderr)
            return 1

    return_code = 0
    try:
        # Only validate completeness since we're going to verify checksums
        # later against what we have in the database via `verify_checksums`.
        bag = Bag(bag_path)
        bag.validate(completeness_only=True)
    except BagError as err:
        job.print_error(f"Error validating BagIt package: {err}")
        return_code = 1

    if return_code == 0:
        try:
            verify_checksums(job, bag, sip_uuid)
        except VerifyChecksumsError:
            return_code = 1
    else:
        job.pyprint("Not verifying checksums because other tests have already failed.")

    # cleanup
    if not is_uncompressed_aip:
        try:
            shutil.rmtree(extract_dir)
        except OSError as err:
            job.pyprint(
                f"Failed to remove temporary directory at {extract_dir} which"
                " contains the AIP extracted for verification."
                f" Error:\n{err}",
                file=sys.stderr,
            )

    return return_code


def call(jobs):
    with transaction.atomic():
        for job in jobs:
            with job.JobContext():
                job.set_status(verify_aip(job))
