# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Module contains functionality for issue with issue tracker integration."""

# pylint: disable=unused-argument
# pylint: disable=no-else-return

import logging
import itertools

from ggrc import db
from ggrc.integrations import constants
from ggrc.integrations import issues
from ggrc.integrations import integrations_errors
from ggrc.models import all_models
from ggrc.models.hooks.issue_tracker import issue_tracker_params_builder
from ggrc.models.hooks.issue_tracker import integration_utils
from ggrc.utils import user_generator
from ggrc.utils.custom_dict import MissingKeyDict
from ggrc.integrations.synchronization_jobs.issue_sync_job import \
    ISSUE_STATUS_MAPPING
from ggrc.services import signals


logger = logging.getLogger(__name__)


ISSUE_TRACKER_FIELDS_TO_UPDATE = ("component_id", "hotlist_id",
                                  "severity", "priority")


ISSUE_TRACKER_TO_DB_NAME_MAPPING = MissingKeyDict({
    "severity": "issue_severity",
    "priority": "issue_priority"
})


def build_issue_tracker_attrs(query):
  """Create dictionary to update IssuetrackerIssue from query

  Rename keys because of different naming in db and in issue tracker
  """
  name_mapping = ISSUE_TRACKER_TO_DB_NAME_MAPPING
  fields_to_update = ISSUE_TRACKER_FIELDS_TO_UPDATE
  return {name_mapping[field]: query[field] for field in fields_to_update
          if field in query}


def _is_already_linked(ticket_id):
  """Checks if ticket with ticket_id is already linked to GGRC object"""
  exists_query = db.session.query(
      all_models.IssuetrackerIssue.issue_id
  ).filter_by(issue_id=ticket_id).exists()
  return db.session.query(exists_query).scalar()


def create_missed_issue_acl(email, role_name, obj):
  """Create missed acl for emails from IssueTracker"""
  person = user_generator.find_user(email)
  if not person:
    return
  obj.add_person_with_role_name(person, role_name)


def update_initial_issue(obj, issue_tracker_params):
  """Updates initial object according to business requirements"""
  issue_tracker_status = issue_tracker_params.status.lower()
  ggrc_status = ISSUE_STATUS_MAPPING.get(issue_tracker_status)
  if ggrc_status:
    obj.status = ggrc_status

  issue_admins = [p.email for p in obj.get_persons_for_rolename("Admin")]
  issue_primary_contacts = [
      p.email for p in obj.get_persons_for_rolename("Primary Contacts")
  ]
  issue_secondary_contacts = [
      p.email for p in obj.get_persons_for_rolename("Secondary Contacts")
  ]

  verifier_email = issue_tracker_params.verifier
  if verifier_email and verifier_email not in issue_admins:
    create_missed_issue_acl(verifier_email, "Admin", obj)

  assignee_email = issue_tracker_params.assignee
  if assignee_email and assignee_email not in issue_primary_contacts:
    create_missed_issue_acl(assignee_email, "Primary Contacts", obj)

  for secondary_contact in issue_tracker_params.cc_list:
    if secondary_contact not in issue_secondary_contacts:
      create_missed_issue_acl(secondary_contact, "Secondary Contacts", obj)


def link_issue(obj, ticket_id, issue_tracker_info):
  """Link issue to existing IssueTracker ticket"""

  if _is_already_linked(ticket_id):
    logger.error(
        "Unable to link a ticket while creating object ID=%d: %s ticket ID is "
        "already linked to another GGRC object",
        obj.id,
        ticket_id,
    )
    obj.add_warning(
        "This ticket was already linked to another GGRC issue, assessment "
        "or review object. Linking the same ticket to multiple objects is not "
        "allowed due to potential for conflicting updates."
    )
    return

  builder = issue_tracker_params_builder.IssueParamsBuilder()
  issue_tracker_container = builder.build_params_for_issue_link(
      obj,
      ticket_id,
      issue_tracker_info,
  )

  if issue_tracker_container.is_empty():
    return

  # Query to IssueTracker.
  issue_tracker_query = issue_tracker_container.get_issue_tracker_params()

  # Parameters for creation IssuetrackerIssue object in GGRC.
  issuetracker_issue_params = \
      issue_tracker_container.get_params_for_ggrc_object()

  try:
    issues.Client().update_issue(ticket_id, issue_tracker_query)

    ticket_url = integration_utils.build_issue_tracker_url(ticket_id)
    issuetracker_issue_params["issue_url"] = ticket_url
    issuetracker_issue_params["issue_id"] = ticket_id
    update_initial_issue(obj, issue_tracker_container)
  except integrations_errors.Error as error:
    logger.error("Unable to update a ticket ID=%s while deleting"
                 " issue ID=%d: %s",
                 ticket_id, obj.id, error)
    obj.add_warning("Unable to update a ticket in issue tracker.")
    issuetracker_issue_params["enabled"] = False
    return

  if issuetracker_issue_params:
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        obj, issuetracker_issue_params
    )


def create_ticket_for_new_issue(obj, issue_tracker_info):
  """Create new IssueTracker ticket for issue"""
  builder = issue_tracker_params_builder.IssueParamsBuilder()
  issue_tracker_params = builder.build_create_issue_tracker_params(
      obj,
      issue_tracker_info
  )

  if issue_tracker_params.is_empty():
    return

  # Query to IssueTracker.
  issue_tracker_query = issue_tracker_params.get_issue_tracker_params()

  # Parameters for creation IssuetrackerIssue object in GGRC.
  issuetracker_issue_params = issue_tracker_params.get_params_for_ggrc_object()

  try:
    res = issues.Client().create_issue(issue_tracker_query)

    issue_url = integration_utils.build_issue_tracker_url(res["issueId"])
    issuetracker_issue_params["issue_url"] = issue_url
    issuetracker_issue_params["issue_id"] = res["issueId"]
  except integrations_errors.HotlistPermissionError:
    obj.add_warning(constants.WarningsDescription.HOTLIST_PERMISSIONS_ERROR)
    issuetracker_issue_params["enabled"] = False
  except integrations_errors.Error as error:
    logger.error(
        "Unable to create a ticket while creating object ID=%d: %s",
        obj.id, error
    )
    obj.add_warning(constants.WarningsDescription.CREATE_ISSUE)
    issuetracker_issue_params["enabled"] = False

  # Create object in GGRC with info about issue tracker integration.
  all_models.IssuetrackerIssue.create_or_update_from_dict(
      obj, issuetracker_issue_params
  )


def create_issue_handler(obj, issue_tracker_info):
  """Event handler for issue object creation."""

  if not issue_tracker_info or not issue_tracker_info.get("enabled"):
    return

  # We need in flush() here because we need object id for URL generation.
  db.session.flush()

  ticket_id = issue_tracker_info.get("issue_id")

  if ticket_id:
    link_issue(obj,
               ticket_id, {"enabled": issue_tracker_info.get("enabled",
                                                             False)})
  else:
    create_ticket_for_new_issue(obj, issue_tracker_info)


def delete_issue_handler(obj, **kwargs):
  """Event handler for issue object deletion."""
  issue_tracker_object = all_models.IssuetrackerIssue.get_issue("Issue",
                                                                obj.id)

  if issue_tracker_object:
    if issue_tracker_object.enabled and issue_tracker_object.issue_id:
      builder = issue_tracker_params_builder.IssueParamsBuilder()
      issue_tracker_params = builder.build_delete_issue_tracker_params()
      issue_tracker_query = issue_tracker_params.get_issue_tracker_params()
      try:
        issues.Client().update_issue(issue_tracker_object.issue_id,
                                     issue_tracker_query)
      except integrations_errors.Error as error:
        logger.error("Unable to update a ticket ID=%s while deleting"
                     " issue ID=%d: %s",
                     issue_tracker_object.issue_id, obj.id, error)
        obj.add_warning("Unable to update a ticket in issue tracker.")
    db.session.delete(issue_tracker_object)


def detach_issue(new_ticket_id, old_ticket_id):
  """Send to old IssueTracker ticket detachment comment."""
  builder = issue_tracker_params_builder.IssueParamsBuilder()
  params = builder.build_detach_comment(new_ticket_id)
  query = params.get_issue_tracker_params()

  try:
    issues.Client().update_issue(old_ticket_id, query)
  except integrations_errors.Error as error:
    logger.error("Unable to add detach comment to ticket issue ID=%d: %s",
                 old_ticket_id, error)


# pylint: disable=too-many-locals
def update_issue_handler(obj, initial_state, new_issuetracker_info=None):  # noqa
  """Event handler for issue object renewal."""
  # TODO: refactor this handler to be not so complex and more generic
  if not new_issuetracker_info:
    return

  it_object = all_models.IssuetrackerIssue.get_issue("Issue", obj.id)
  old_ticket_id = None
  if it_object:
    old_ticket_id = int(it_object.issue_id) if it_object.issue_id else None

  get_id = new_issuetracker_info.get("issue_id") if new_issuetracker_info \
      else None

  new_ticket_id = int(get_id) if get_id else None

  # We should create new ticket if new ticket_id is empty, we don't store
  # IssueTrackerIssue object or it contains empty ticket_id
  needs_creation = (not it_object) or \
                   (not old_ticket_id) or (not new_ticket_id)

  if needs_creation and new_issuetracker_info["enabled"]:
    create_issue_handler(obj, new_issuetracker_info)
    if not obj.warnings:
      it_issue = all_models.IssuetrackerIssue.get_issue(
          obj.__class__.__name__, obj.id
      )
      new_ticket_id = it_issue.issue_id if it_issue else None
      if old_ticket_id and new_ticket_id and old_ticket_id != new_ticket_id:
        detach_issue(new_ticket_id, old_ticket_id)
    return

  if not it_object:
    return

  if (new_ticket_id != old_ticket_id) and new_issuetracker_info["enabled"]:
    link_issue(obj, new_ticket_id, new_issuetracker_info)
    if not obj.warnings:
      detach_issue(new_ticket_id, old_ticket_id)
    return

  if new_ticket_id == old_ticket_id and \
     new_issuetracker_info.get("is_linking"):
    # issue already linked. no need to do something
    return

  current_issue_tracker_info = it_object.to_dict(
      include_issue=True,
      include_private=True
  )

  # Build query
  builder = issue_tracker_params_builder.IssueParamsBuilder()
  issue_tracker_params = builder.build_update_issue_tracker_params(
      obj,
      initial_state,
      new_issuetracker_info,
      current_issue_tracker_info
  )

  # Query to IssueTracker.
  issue_tracker_query = issue_tracker_params.get_issue_tracker_params()

  # Parameters for creation IssuetrackerIssue object in GGRC.
  issuetracker_issue_params = issue_tracker_params.get_params_for_ggrc_object()

  if not issue_tracker_params.is_empty():
    try:
      issue_id = it_object.issue_id
      issues.Client().update_issue(issue_id, issue_tracker_query)
    except integrations_errors.Error as error:
      logger.error("Unable to update a ticket ID=%s while deleting"
                   " issue ID=%d: %s",
                   it_object.issue_id, obj.id, error)
      obj.add_warning("Unable to update a ticket in issue tracker.")

  if issuetracker_issue_params:
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        obj, issuetracker_issue_params
    )


def create_comment_handler(sync_object, comment, author):
  """Event handler for adding comment to Issue object."""
  issue_tracker_object = all_models.IssuetrackerIssue.get_issue("Issue",
                                                                sync_object.id)

  if not issue_tracker_object or not issue_tracker_object.enabled:
    return

  builder = issue_tracker_params_builder.IssueParamsBuilder()
  params = builder.build_params_for_comment(
      sync_object,
      comment.description,
      author
  )
  query = params.get_issue_tracker_params()

  try:
    issues.Client().update_issue(issue_tracker_object.issue_id, query)
  except integrations_errors.Error as error:
    logger.error("Unable to add comment to ticket issue ID=%d: %s",
                 issue_tracker_object.issue_id, error)
    sync_object.add_warning("Unable to update a ticket in issue tracker.")


def prepare_issue_json(issue, issue_tracker_info=None,
                       create_issuetracker=False):
  """Prepare issuetracker issue json for Issue object."""
  if not issue_tracker_info:
    issue_tracker_info = issue.issuetracker_issue.to_dict()

  builder = issue_tracker_params_builder.IssueParamsBuilder()
  issue_tracker_params = builder.build_create_issue_tracker_params(
      issue,
      issue_tracker_info
  )

  if issue_tracker_params.is_empty():
    return {}

  params = issue_tracker_params.get_issue_tracker_params()
  if "type" not in params:
    params["type"] = issue_tracker_info.get("issue_type")
  return params


def prepare_issue_update_json(issue, issue_tracker_info=None):
  """Prepare issuetracker issue json for Issue object update."""
  if not issue_tracker_info:
    issue_tracker_info = issue.issue_tracker

  builder = issue_tracker_params_builder.IssueParamsBuilder()
  builder.handle_issue_tracker_info(issue, issue_tracker_info)
  issue_tracker_params = builder.params
  params = issue_tracker_params.get_issue_tracker_params()
  return params


def prepare_comment_update_json(object_, comment, author):
  """Prepare json for adding comment to IssueTracker issue"""
  builder = issue_tracker_params_builder.IssueParamsBuilder()
  params = builder.build_params_for_comment(object_, comment, author)
  return params.get_issue_tracker_params()


def prepare_disable_comment_json():
  """Prepare json for adding disable comment to IssueTracker issue"""
  builder = issue_tracker_params_builder.IssueParamsBuilder()
  builder.params.add_comment(builder.DISABLE_TMPL)
  return builder.params.get_issue_tracker_params()


def _hook_issue_post(sender, objects=None, sources=None):
  """Handle creating issue related info."""
  del sender

  for issue, _ in itertools.izip(objects, sources):
    integration_utils.update_issue_tracker_for_import(issue)


def init_hook():
  """Initializes hooks."""

  signals.Restful.collection_posted.connect(
      _hook_issue_post,
      sender=all_models.Issue
  )
