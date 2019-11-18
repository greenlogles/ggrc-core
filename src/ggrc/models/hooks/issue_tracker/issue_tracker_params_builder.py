# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""This module contains functionality for building Issue tracker query."""

# pylint: disable=invalid-name

import urlparse
import logging

from werkzeug import exceptions as flask_exceptions
import html2text

from ggrc import utils as ggrc_utils
from ggrc.integrations import constants
from ggrc.integrations import integrations_errors
from ggrc.integrations import issues
from ggrc.models.hooks.issue_tracker import integration_utils
from ggrc.models.hooks.issue_tracker import \
    issue_tracker_params_container as params_container


logger = logging.getLogger(__name__)


class BaseIssueTrackerParamsBuilder(object):
  """Base issue tracker params builder class.

  This class and all classes which inherits from it provide
  functionality for building query to issue tracker.
  So, this classes encapsulate business logic for mapping GGRC objects fields
  to issue tracker issue fields.
  """

  INITIAL_COMMENT_TMPL = (
      u"This bug was auto-generated to track a GGRC {model}. "
      u"Use the following link to find the {model} - {link}."
  )

  DELETE_TMPL = (
      u"GGRC object has been deleted. GGRC changes will "
      u"no longer be tracked within this bug."
  )

  DISABLE_TMPL = (
      u"Changes to this GGRC object will no longer be tracked within this bug."
  )

  ENABLE_TMPL = (
      u"Changes to this GGRC object will be tracked within this bug."
  )

  COMMENT_TMPL = (
      u"A new comment is added by '{author}' to the '{model}': \n\n"
      u"'{comment}'\n\n Use the following to link to get more "
      u"information from the GGRC '{model}'. Link - {link}"
  )

  ISSUE_TRACKER_INFO_FIELDS_TO_CHECK = (
      "component_id",
      "hotlist_id",
      "issue_severity",
      "issue_priority",
      "title",
  )

  def __init__(self):
    """Basic initialization."""
    self.params = params_container.IssueTrackerParamsContainer()

  @staticmethod
  def get_ggrc_object_url(obj):
    """Builds and returns URL to GGRC object."""
    return urlparse.urljoin(
        ggrc_utils.get_url_root(),
        ggrc_utils.view_url_for(obj)
    )

  def handle_issue_tracker_info(self, obj, issue_tracker_info,
                                remote_info=None):
    """Handle issue tracker information."""
    default_values = constants.DEFAULT_ISSUETRACKER_VALUES
    issue_component_id = issue_tracker_info.get("component_id")
    remote_info = remote_info or {}
    issue_state = remote_info.get("issueState", {})
    self.params.component_id = issue_component_id or \
        issue_state.get("component_id", default_values["issue_component_id"])

    issue_hotlist_id = issue_tracker_info.get("hotlist_id")
    remote_hotlist_id = issue_state.get("hotlist_ids",
                                        [default_values["issue_hotlist_id"]])
    self.params.hotlist_id = issue_hotlist_id or \
        remote_hotlist_id[0] if remote_hotlist_id else None

    issue_type = issue_tracker_info.get("issue_type")
    self.params.issue_type = issue_type or \
        default_values["issue_type"]

    issue_priority = issue_tracker_info.get("issue_priority")
    self.params.issue_priority = issue_priority or \
        issue_state.get("priority", default_values["issue_priority"])

    issue_severity = issue_tracker_info.get("issue_severity")
    self.params.issue_severity = issue_severity or \
        issue_state.get("severity", default_values["issue_severity"])

    self.params.title = issue_tracker_info.get("title") or \
        issue_state.get("title", obj.title)
    self.params.enabled = issue_tracker_info.get("enabled")

  def _update_issue_tracker_info(self, new_issue_tracker_info,
                                 current_issue_tracker_info):
    """Update issue tracker information."""

    fields_to_check = self.ISSUE_TRACKER_INFO_FIELDS_TO_CHECK

    # Handle integration toggling.
    old_enabled = current_issue_tracker_info["enabled"]
    new_enabled = new_issue_tracker_info["enabled"]
    if old_enabled != new_enabled:
      comment = self.ENABLE_TMPL if new_enabled else self.DISABLE_TMPL
      self.params.enabled = new_enabled
      self.params.add_comment(comment)

    if new_enabled:
      for field in fields_to_check:
        new_value = new_issue_tracker_info.get(field)
        old_value = current_issue_tracker_info.get(field)
        if new_value != old_value:
          setattr(self.params, field, new_value)

  def build_params_for_comment(self, sync_obj, comment, author):
    """Build query to Issue tracker for adding comment to issue."""
    comment = html2text.HTML2Text().handle(comment).strip()
    self.params.add_comment(self.COMMENT_TMPL.format(
        author=author,
        comment=comment,
        model=sync_obj.__class__.__name__,
        link=self.get_ggrc_object_url(sync_obj),
    ))
    return self.params


class IssueParamsBuilder(BaseIssueTrackerParamsBuilder):
  """Issue tracker query builder for GGRC Issue object."""

  MODEL_NAME = "Issue"

  ASSIGNED_ISSUE_STATUS = "ASSIGNED"

  DESCRIPTION_TMPL = u"Following is the issue Description from GGRC: {}"
  REMEDIATION_PLAN_TMPL = (
      u"Following is the issue Remediation Plan from GGRC: {}"
  )
  UPDATE_DESCRIPTION_TMPL = (
      u"Issue Description has been updated.\n{}"
  )
  UPDATE_REMEDIATION_PLAN_TMPL = (
      u"Issue Remediation Plan has been updated.\n{}"
  )
  EXCLUDE_REPORTER_EMAIL_ERROR_MSG = (
      u"Issue tracker integration is not activated because the reporter "
      u"is an Global auditor."
  )
  ISSUE_LINK_TMPL = (
      u"This bug was linked to a GGRC Issue. Use the following link to find "
      u"the issue - {issue_link}."
  )
  DETACH_TMPL = (
      u"Another bug {new_ticket_id} has been linked to track changes to the"
      u" GGRC Issue. Changes to the GGRC Issue will no longer be tracked"
      u" within this bug."
  )

  def _build_allowed_emails(self, obj):
    """Handle emails from object.

    Excludes auditors from emails list because no contact field should contain
             global auditors.
    Args: obj - object to collect emails
    Return: A tuple (reporter_email, allowed_emails).
    """
    all_emails = {person.email for person, _ in obj.access_control_list}

    # Add the person who triggered the event.
    all_emails |= {obj.modified_by.email}
    allowed_emails = integration_utils.exclude_auditor_emails(all_emails)

    # Don't turn on integration if 'reporter' is auditor.
    reporter_email = obj.modified_by.email
    if reporter_email not in allowed_emails:
      obj.add_warning(self.EXCLUDE_REPORTER_EMAIL_ERROR_MSG)
      return None
    return allowed_emails

  def build_create_issue_tracker_params(self, obj, issue_tracker_info):
    """Build create issue query for issue tracker."""

    allowed_emails = self._build_allowed_emails(obj)
    if allowed_emails is None:
      return self.params

    self.params.status = self.ASSIGNED_ISSUE_STATUS
    self.params.add_comment(self.INITIAL_COMMENT_TMPL.format(
        model=self.MODEL_NAME,
        link=self.get_ggrc_object_url(obj)
    ))

    self.handle_issue_tracker_info(obj, issue_tracker_info)
    self._handle_people_emails(obj, allowed_emails)
    self._handle_issue_comment_attributes(obj)

    return self.params

  def build_params_for_issue_link(self, obj, ticket_id, it_info):
    """Build update issue query for linking IssueTracker ticket to Issue"""
    allowed_emails = self._build_allowed_emails(obj)
    if allowed_emails is None:
      return self.params

    try:
      res = issues.Client().get_issue(ticket_id)
    except integrations_errors.Error as error:
      logger.error(
          "Unable to link a ticket while creating object ID=%d: %s",
          obj.id,
          error,
      )
      obj.add_warning(
          "Ticket tracker ID does not exist or you do not have access to it."
      )
    else:
      self.params.status = res["issueState"]["status"]
      self._add_link_message(obj)
      self.handle_issue_tracker_info(obj, it_info, res)
      self._populate_hotlist_id(res)
      self._populate_component_id(res)
      self._handle_emails_from_response(res)
      self.params.reporter = obj.modified_by.email

    return self.params

  def _populate_hotlist_id(self, ticket_info):
    """Extract hotlist_id form ticket info"""
    try:
      self.params.hotlist_id = ticket_info["issueState"]["hotlist_ids"][0]
    except (KeyError, IndexError):
      self.params.hotlist_id = ""

  def _populate_component_id(self, ticket_info):
    """Extract component_id form ticket info"""
    try:
      self.params.component_id = ticket_info["issueState"]["component_id"]
    except KeyError:
      raise flask_exceptions.ServiceUnavailable(
          "component_id attribute is mandatory. "
          "Please check if integration service API changed."
      )

  def build_update_issue_tracker_params(self,
                                        obj,
                                        initial_state,
                                        new_issue_tracker_info,
                                        current_issue_tracker_info):
    """Build update issue query for issue tracker."""
    self._update_issue_tracker_info(new_issue_tracker_info,
                                    current_issue_tracker_info)

    if new_issue_tracker_info["enabled"]:
      self._update_issue_comment_attributes(obj, initial_state)

    return self.params

  def build_delete_issue_tracker_params(self):
    """Build delete issue query for issue tracker."""
    self.params.add_comment(self.DELETE_TMPL.format(model="Issue"))

    return self.params

  def build_detach_comment(self, new_ticket):
    """Build request for old Issue Tracker ticket detach."""
    self.params.add_comment(self.DETACH_TMPL.format(new_ticket_id=new_ticket))
    self.params.status = constants.OBSOLETE_ISSUE_STATUS
    return self.params

  def _handle_emails_from_response(self, response):
    """Handle emails from response for GET request to IssueTracker"""
    self.params.verifier = response["issueState"]["verifier"]
    self.params.assignee = response["issueState"]["assignee"]
    self.params.cc_list = response["issueState"]["ccs"]

  def _handle_people_emails(self, obj, allowed_emails):
    """Handle emails.

    Reporter should be a person who triggers an event.
    Verifier should be first person from Admins in alphabetical order.
    Assignee should be first person from Primary Contacts
             in alphabetical order.
    CCS should be all other persons that mentioned in custom roles.

    No field should contain global auditors.
    """

    # Handle Admins list.
    admins = [
        person for person, acl in obj.access_control_list
        if acl.ac_role.name == "Admin" and person.email in allowed_emails
    ]
    admins = sorted(admins, key=lambda person: person.email)

    issue_verifier_email = admins[0].email if admins else ""
    self.params.verifier = issue_verifier_email

    # Handle Primary Contacts list.
    primary_contacts = [
        person for person, acl in obj.access_control_list
        if acl.ac_role.name == "Primary Contacts" and
        person.email in allowed_emails
    ]
    primary_contacts = sorted(primary_contacts, key=lambda p: p.email)
    assignee_email = primary_contacts[0].email if primary_contacts else ""
    self.params.assignee = assignee_email

    # Handle reporter list.
    reporter_email = obj.modified_by.email
    self.params.reporter = reporter_email

    # Handle CCS list.
    ccs = allowed_emails - {issue_verifier_email,
                            assignee_email,
                            reporter_email}
    self.params.cc_list = list(ccs)

  def _add_link_message(self, obj):
    """Adds link message to IssueTracker ticket"""
    self.params.add_comment(self.ISSUE_LINK_TMPL.format(
        issue_link=self.get_ggrc_object_url(obj),
    ))
    self._handle_issue_comment_attributes(obj)

  def _handle_issue_comment_attributes(self, obj):
    """Handle attributes from GGRC Issue object.

    This method adds Issue description and Remediation Plan as comments
    to Issue Tracker Issue.
    """
    description = obj.description
    if description:
      description = html2text.HTML2Text().handle(description).strip("\n")
      self.params.add_comment(self.DESCRIPTION_TMPL.format(description))

    test_plan = obj.test_plan
    if test_plan:
      test_plan = html2text.HTML2Text().handle(test_plan).strip("\n")
      self.params.add_comment(self.REMEDIATION_PLAN_TMPL.format(test_plan))

  def _update_issue_comment_attributes(self, obj, initial_state):
    """Update issue tracker comments"""
    description = obj.description
    initial_description = initial_state.description
    if description != initial_description:
      description = html2text.HTML2Text().handle(description).strip("\n")
      self.params.add_comment(self.UPDATE_DESCRIPTION_TMPL.format(description))

    test_plan = obj.test_plan
    initial_test_plan = initial_state.test_plan
    if test_plan != initial_test_plan:
      test_plan = html2text.HTML2Text().handle(test_plan).strip("\n")
      self.params.add_comment(
          self.UPDATE_REMEDIATION_PLAN_TMPL.format(test_plan)
      )


class AssessmentParamsBuilder(BaseIssueTrackerParamsBuilder):
  """Issue tracker query builder for GGRC Assessment object."""
  DETACH_TMPL = (
      "Another bug {new_ticket_id} has been linked to track changes to the "
      "GGRC Assessment. Changes to the GGRC Assessment will no longer be "
      "tracked within this bug."
  )

  def build_detach_comment(self, new_ticket):
    """Build request for old Issue Tracker ticket detach."""
    self.params.add_comment(self.DETACH_TMPL.format(new_ticket_id=new_ticket))
    self.params.status = constants.OBSOLETE_ISSUE_STATUS
    return self.params
