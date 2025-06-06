#!/usr/bin/env python
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
import getpass
import optparse
import os
import re
import subprocess
import sys
import tempfile
import time

import django
import requests

from archivematica.archivematicaCommon.custom_handlers import get_script_logger

django.setup()

from django.conf import settings as mcpclient_settings
from django.core.exceptions import ValidationError
from django.db import transaction

import archivematica.dashboard.main.models as models

logger = get_script_logger("archivematica.upload.qubit")

PREFIX = "[uploadDIP]"


# Colorize output
def hilite(string, status=True):
    if not os.isatty(sys.stdout.fileno()):
        return string
    attr = []
    if status:
        attr.append("32")
    else:
        attr.append("31")
    return "\x1b[{}m{}\x1b[0m".format(";".join(attr), string)


# Print to stdout
def log(message, access=None):
    logger.error(f"{PREFIX} {hilite(message)}")
    if access:
        access.status = message
        access.save()


# Print to stderr and exit
def error(job, message, code=1):
    job.pyprint(f"{PREFIX} {hilite(message, False)}", file=sys.stderr)
    return 1


def start(job, data):
    # Make sure we are working with an existing SIP record
    if not models.SIP.objects.filter(pk=data.uuid).exists():
        return error(job, "UUID not recognized")

    # Get directory
    jobs = models.Job.objects.filter(sipuuid=data.uuid, jobtype="Upload DIP")
    if jobs.count():
        directory = (
            jobs[0]
            .directory.rstrip("/")
            .replace("%sharedPath%", "/var/archivematica/sharedDirectory/")
        )
    else:
        return error(job, "Cannot determine directory")

    # Check if exists
    if not os.path.exists(directory):
        log("Directory not found: %s" % directory)

        # Trying with uploadedDIPs
        log("Looking up uploadedDIPs/")
        directory = directory.replace("uploadDIP", "uploadedDIPs")

        if os.path.exists(directory) is False:
            return error(job, "Directory not found: %s" % directory)

    try:
        # This upload was called before, restore Access record
        access = models.Access.objects.get(sipuuid=data.uuid)
    except (
        models.Access.DoesNotExist,
        ValidationError,
    ):  # First time this job is called, create new Access record
        access = models.Access(sipuuid=data.uuid)
        # Look for access system ID
        transfers = models.Transfer.objects.filter(file__sip_id=data.uuid).distinct()
        if transfers.count() == 1:
            access.target = transfers[0].access_system_id
        access.save()

    target = access.target
    log("Target: %s" % target)
    if not target:
        return error(job, "No target was selected")

    # Rsync if data.rsync_target option was passed to this script
    if data.rsync_target:
        """Build command (rsync)
        -a =
          -r = recursive
          -l = recreate symlinks on destination
          -p = set same permissions
          -t = transfer modification times
          -g = set same group owner on destination
          -o = set same user owner on destination (if possible, super-user)
          --devices = transfer character and block device files (only super-user)
          --specials = transfer special files like sockets and fifos
        -z = compress
        -P = --partial + --stats
        """
        # Using rsync -rltzP
        command = [
            "rsync",
            "--protect-args",
            "-rltz",
            "-P",
            "--chmod=ugo=rwX",
            directory,
            data.rsync_target,
        ]

        # Add -e if data.rsync_command was passed to this script
        if data.rsync_command:
            # Insert in second position. Example: rsync -e "ssh -i key" ...
            command.insert(1, "-e %s" % data.rsync_command)

        log(" ".join(command))

        # Getting around of rsync output buffering by outputting to a temporary file
        pipe_output, file_name = tempfile.mkstemp()
        log("Rsync output is being saved in %s" % file_name)

        # Call Rsync
        process = subprocess.Popen(command, stdout=pipe_output, stderr=pipe_output)

        # poll() returns None while the process is still running
        while process.poll() is None:
            time.sleep(1)
            last_line = open(file_name).readlines()

            # It's possible that it hasn't output yet, so continue
            if len(last_line) == 0:
                continue
            last_line = last_line[-1]

            # Matching to "[bytes downloaded]  number%  [speed] number:number:number"
            match = re.match(".* ([0-9]*)%.* ([0-9]*:[0-9]*:[0-9]*).*", last_line)

            if not match:
                continue

            # Update upload status
            # - percentage in match.group(1)
            # - ETA in match.group(2)
            access.status = f"Sending... {match.group(1)} (ETA: {match.group(2)})"
            access.statuscode = 10
            access.save()
            log(access.status)

        # We don't need the temporary file anymore!
        # log("Removing temporary rsync output file: %s" % file_name)
        # os.unlink(file_name)

        # At this point, we should have a return code
        # If greater than zero, see man rsync (EXIT VALUES)
        access.exitcode = process.returncode
        if 0 < process.returncode:
            access.statuscode = 12
        else:
            access.statuscode = 11
        access.save()

        if 0 < process.returncode:
            return error(
                job,
                "Rsync quit unexpectedly (exit %s), the upload script will be stopped here"
                % process.returncode,
            )

    # Building headers dictionary for the deposit request
    headers = {}
    headers["User-Agent"] = "Archivematica"
    headers["X-Packaging"] = "http://purl.org/net/sword-types/METSArchivematicaDIP"
    """ headers['X-On-Beahalf-Of'] """
    headers["Content-Type"] = "application/zip"
    headers["X-No-Op"] = "false"
    headers["X-Verbose"] = "false"
    headers["Content-Location"] = "file:///%s" % os.path.basename(directory)
    """ headers['Content-Disposition'] """

    # Build URL (expected sth like http://localhost/ica-atom/index.php)
    atom_url_prefix = ";" if data.version == 1 else ""
    deposit_url = f"{data.url}/{atom_url_prefix}sword/deposit/{target}"

    # Auth and request!
    log("About to deposit to: %s" % data.url)
    access.statuscode = 13
    access.resource = f"{data.url}/{target}"
    access.save()
    auth = requests.auth.HTTPBasicAuth(data.email, data.password)

    # Disable redirects: AtoM returns 302 instead of 202, but Location header field is valid
    response = requests.request(
        "POST",
        deposit_url,
        auth=auth,
        headers=headers,
        allow_redirects=False,
        timeout=mcpclient_settings.AGENTARCHIVES_CLIENT_TIMEOUT,
    )

    # response.{content,headers,status_code}
    log("> Response code: %s" % response.status_code)
    log("> Location: %s" % response.headers.get("Location"))

    if data.debug:
        # log("> Headers sent: %s" % headers)
        # log("> Headers received: %s" % response.headers)
        log("> Content received: %s" % response.content)

    # Check AtoM response status code
    if response.status_code not in [200, 201, 302]:
        return error(job, "Response code not expected")

    # Location is a must, if it is not included in the AtoM response something was wrong
    if response.headers["Location"] is None:
        return error(
            job, "Location is expected, if not is likely something is wrong with AtoM"
        )

    # (A)synchronously?
    if response.status_code == 302:
        access.status = (
            "Deposited asynchronously, AtoM is processing the DIP in the job queue"
        )
        log(access.status)
    else:
        access.statuscode = 14
        access.status = "Deposited synchronously"
        log(access.status)
    access.save()

    # We also have to parse the XML document

    return 0


def call(jobs):
    parser = optparse.OptionParser(usage="Usage: %prog [options]")

    options = optparse.OptionGroup(parser, "Basic options")
    options.add_option("-u", "--url", dest="url", metavar="URL", help="URL")
    options.add_option(
        "-e", "--email", dest="email", metavar="EMAIL", help="account e-mail"
    )
    options.add_option(
        "-p", "--password", dest="password", metavar="PASSWORD", help="account password"
    )
    options.add_option("-U", "--uuid", dest="uuid", metavar="UUID", help="UUID")
    options.add_option(
        "-d",
        "--debug",
        dest="debug",
        metavar="DEBUG",
        default="no",
        type=str,
        help="Debug mode, prints HTTP headers",
    )
    options.add_option(
        "-v", "--version", dest="version", type="int", default=1, help="AtoM version"
    )
    parser.add_option_group(options)

    options = optparse.OptionGroup(parser, "Rsync options")
    options.add_option(
        "-c",
        "--rsync-command",
        dest="rsync_command",
        metavar="RSYNC_COMMAND",
        help="Rsync command, e.g.: ssh -p 2222",
    )
    options.add_option(
        "-t",
        "--rsync-target",
        dest="rsync_target",
        metavar="RSYNC_TARGET",
        help="Rsync target, e.g.: foo@bar:~/dips/",
    )
    parser.add_option_group(options)

    with transaction.atomic():
        for job in jobs:
            with job.JobContext(logger=logger):
                (opts, args) = parser.parse_args(job.args[1:])

                # Make sure that archivematica user is executing this script
                user = getpass.getuser()
                if "archivematica" != user:
                    job.print_error(
                        'This user is required to be executed as "archivematica" user but you are using %s.'
                        % user
                    )
                    job.set_status(1)
                    continue

                if (
                    opts.email is None
                    or opts.password is None
                    or opts.url is None
                    or opts.uuid is None
                ):
                    parser.print_help()
                    job.print_error("Invalid syntax", 2)
                    job.set_status(1)
                    continue

                opts.debug = opts.debug.lower() in ["yes", "y", "true", "1"]

                try:
                    job.set_status(start(job, opts))
                except Exception as inst:
                    job.pyprint("Exception!", file=sys.stderr)
                    job.pyprint(type(inst), file=sys.stderr)
                    job.pyprint(inst.args, file=sys.stderr)
                    import traceback

                    job.print_error(traceback.format_exc())
                    job.set_status(1)
                finally:
                    pass
