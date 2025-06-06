#!/usr/bin/env python
import os

from archivematica.archivematicaCommon.executeOrRunSubProcess import executeOrRun


def call(jobs):
    command = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "copyRecursiveIfExists.sh"
    )

    for job in jobs:
        with job.JobContext():
            exit_code, std_out, std_error = executeOrRun(
                "command", [command] + job.args[1:], printing=True, capture_output=True
            )

            job.write_error(std_error)
            job.write_output(std_out)
            job.set_status(exit_code)
