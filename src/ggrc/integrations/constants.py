# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""This module contains constants for issue tracker integration."""

OBSOLETE_ISSUE_STATUS = "OBSOLETE"
ISSUE_TRACKED_MODELS = ["Assessment", "Issue"]
DEFAULT_ISSUETRACKER_VALUES = {
    'enabled': False,
    'issue_priority': 'P2',
    'issue_severity': 'S2',
    'issue_type': 'PROCESS',
    'component_id': 188208,
    'issue_hotlist_id': 1498476,
    'issue_component_id': 398781,
    'hotlist_id': 766459,
}

INITIAL_COMMENT_TMPL = (
    "This bug was auto-generated to track a GGRC assessment (a.k.a PBC Item). "
    "Use the following link to find the assessment - %s."
)

LINK_COMMENT_TMPL = (
    "This bug was linked to a GGRC assessment (a.k.a PBC Item). "
    "Use the following link to find the assessment - %s."
)

ARCHIVED_TMPL = (
    "Assessment has been archived. "
    "Changes to this GGRC Assessment will not be tracked within this bug "
    "until Assessment is unlocked."
)

UNARCHIVED_TMPL = (
    "Assessment has been unarchived. "
    "Changes to this GGRC Assessment will be tracked within this bug."
)

STATUS_CHANGE_TMPL = (
    "The status of this bug was automatically synced to reflect "
    "current GGRC assessment status. Current status of related "
    "GGRC Assessment is %s. Use the following to link to get "
    "information from the GGRC Assessment on why the status "
    "may have changed. Link - %s."
)

ENABLED_TMPL = (
    "Changes to this GGRC Assessment will "
    "be tracked within this bug."
)

DISABLED_TMPL = (
    "Changes to this GGRC Assessment will no longer be "
    "tracked within this bug."
)

COMMENT_TMPL = (
    u"A new comment is added by '{author}' to the '{model}': \n\n"
    u"'{comment}'\n\n Use the following to link to get more "
    u"information from the GGRC '{model}'. Link - {link}"
)

ASMT_TEST_PLAN_UPD_TMPL = (
    u"Assessment Procedure has been updated.\n{}"
)

AVAILABLE_PRIORITIES = ("P0", "P1", "P2", "P3", "P4", )
AVAILABLE_SEVERITIES = ("S0", "S1", "S2", "S3", "S4", )

COMMON_SYNCHRONIZATION_FIELDS = (
    "status",
    "type",
    "priority",
    "severity",
    "reporter",
    "assignee"
)

# Status transitions map for assessment without verifier.
NO_VERIFIER_STATUSES = {
    # (from_status, to_status): 'issue_tracker_status'
    ('Not Started', 'Completed'): 'VERIFIED',
    ('In Progress', 'Completed'): 'VERIFIED',

    ('Completed', 'In Progress'): 'ACCEPTED',
    ('Deprecated', 'In Progress'): 'ACCEPTED',

    ('Completed', 'Not Started'): 'ASSIGNED',
    ('Deprecated', 'Not Started'): 'ASSIGNED',

    ('Not Started', 'Deprecated'): 'OBSOLETE',
    ('In Progress', 'Deprecated'): 'OBSOLETE',
    ('Completed', 'Deprecated'): 'OBSOLETE',
}

STATUSES_MAPPING = {
    "Not Started": "ASSIGNED",
    "In Progress": "ACCEPTED",
    "In Review": "FIXED",
    "Rework Needed": "ACCEPTED",
    "Completed": "VERIFIED",
    "Deprecated": "OBSOLETE"
}

CREATE_STATUSES_MAPPING = {
    "Not Started": "ASSIGNED",
    "In Progress": "ASSIGNED",
    "In Review": "FIXED",
    "Rework Needed": "ASSIGNED",
    "Completed": "VERIFIED",
    "Deprecated": "OBSOLETE"
}

# Status transitions map for assessment with verifier.
VERIFIER_STATUSES = {
    # (from_status, to_status, verified): 'issue_tracker_status'
    # When verified is True 'Completed' means 'Completed and Verified'.

    # State: FIXED
    ('Not Started', 'In Review', False): 'FIXED',
    ('In Progress', 'In Review', False): 'FIXED',
    ('Rework Needed', 'In Review', False): 'FIXED',
    ('Completed', 'In Review', True): 'FIXED',
    ('Deprecated', 'In Review', False): 'FIXED',

    # State: ACCEPTED
    ('In Review', 'In Progress', False): 'ACCEPTED',
    # if gets from Completed and Verified to In Progress, can not be verified
    ('Completed', 'In Progress', False): 'ACCEPTED',
    ('Deprecated', 'In Progress', False): 'ACCEPTED',

    # State: ACCEPTED
    ('In Review', 'Rework Needed', False): 'ACCEPTED',
    ('Completed', 'Rework Needed', True): 'ACCEPTED',
    ('Deprecated', 'Rework Needed', False): 'ACCEPTED',

    # State: FIXED (Verified)
    ('Not Started', 'Completed', True): 'VERIFIED',
    ('In Progress', 'Completed', True): 'VERIFIED',
    ('In Review', 'Completed', True): 'VERIFIED',
    ('Rework Needed', 'Completed', True): 'VERIFIED',
    ('Deprecated', 'Completed', True): 'VERIFIED',

    # State: ASSIGNED
    ('In Review', 'Not Started', False): 'ASSIGNED',
    ('Rework Needed', 'Not Started', False): 'ASSIGNED',
    ('Completed', 'Not Started', True): 'ASSIGNED',
    ('Deprecated', 'Not Started', False): 'ASSIGNED',

    # State: WON'T FIX (OBSOLETE)
    ('Not Started', 'Deprecated', False): 'OBSOLETE',
    ('In Progress', 'Deprecated', False): 'OBSOLETE',
    ('In Review', 'Deprecated', False): 'OBSOLETE',
    ('Rework Needed', 'Deprecated', False): 'OBSOLETE',
    ('Completed', 'Deprecated', True): 'OBSOLETE',
}

MAX_REQUEST_ATTEMPTS = 3
REQUEST_TIMEOUT = 5
REQUEST_DEADLINE = 200


class CustomFields(object):
  """Custom fields for issue tracker."""
  # pylint: disable=too-few-public-methods
  DUE_DATE = "Due Date"


class ErrorsDescription(object):
  """Constants for errors description."""
  # pylint: disable=too-few-public-methods
  CREATE_ASSESSMENT = "Unable to create a ticket while " \
                      "creating assessment ID=%s: %s"
  LINK_ASSESSMENT = "Unable to link a ticket while " \
                    "creating assessment ID=%d: %s"
  UPDATE_ASSESSMENT = "Unable to update a ticket ID=%d while " \
                      "updating assessment ID=%d: %s"
  SYNC_ASSESSMENT = "Unable to update status of Issue Tracker " \
                    "issue ID=%s for assessment ID=%d: %r"
  DELETE_ASSESSMENT = "Unable to update a ticket ID=%s " \
                      "while deleting assessment ID=%d: %s"
  DETACH_ASSESSMENT = "Unable to add detach comment " \
                      "to ticket issue ID=%d: %s"


class WarningsDescription(object):
  """Constants for warnings description."""
  # pylint: disable=too-few-public-methods
  CREATE_ASSESSMENT = "Unable to create a ticket."
  CREATE_ISSUE = "Unable to create a ticket in issue tracker."
  LINK_ASSESSMENT = "Unable to link a ticket."
  UPDATE_ASSESSMENT = "Unable to update a ticket."
  SYNC_ASSESSMENT = "Unable to sync assessment."
  DELETE_ASSESSMENT = "Unable to delete assessment."
  DETACH_ASSESSMENT = "Unable to add detach comment to ticket issue ID=%d"
  HOTLIST_PERMISSIONS_ERROR = (
      "Ticket in issue tracker wasn't added to Hotlist. "
      "Please make sure that you have enough rights for the "
      "Hotlist and try again.")
