# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""A collection of hooks to process IssueTracker related events."""
# pylint: disable=too-many-lines
# this module will be refactored in the future when we will merge two sync
# mechanisms into generic one

import datetime
import itertools
import logging
import urlparse
from werkzeug import exceptions as flask_exceptions
import sqlalchemy as sa
import html2text


from ggrc import db
from ggrc import utils
from ggrc.models import all_models
from ggrc.integrations import issues, constants
from ggrc.integrations import integrations_errors
from ggrc.integrations.synchronization_jobs import sync_utils
from ggrc.models.hooks.issue_tracker import integration_utils
from ggrc.models.hooks.issue_tracker import common_handlers
from ggrc.models import exceptions

from ggrc.rbac import permissions
from ggrc.services import signals
from ggrc.utils import referenced_objects
from ggrc.utils import helpers as helper_utils


logger = logging.getLogger(__name__)


# pylint: disable=too-many-public-methods
class AssessmentTrackerHandler(object):
  """Module that used for integration Assessment with IssueTracker.

  Include all necessary validators, builders and handlers
  """

  def __init__(self):
    self._client = issues.Client()

  @staticmethod
  def _validate_integer_fields(issue_info):
    """Validate integer fields for issue.

    Args:
        issue_info: dictionary with issue
        payload information
    """
    # Component ID
    try:
      component_id = issue_info["component_id"]
    except KeyError:
      raise exceptions.ValidationError("Component ID is mandatory.")
    else:
      try:
        int(component_id)
      except (ValueError, TypeError):
        raise exceptions.ValidationError("Component ID must be a number.")

    # Hotlist ID
    hotlist_id = issue_info.get("hotlist_id", "")
    if hotlist_id:
      try:
        int(hotlist_id)
      except ValueError:
        raise exceptions.ValidationError("Hotlist ID must be a number.")

  @staticmethod
  def _validate_string_fields(issue_info):
    """Validate string fields for issue.

    Args:
        issue_info: dictionary with issue
        payload information
    """
    # Ticket Type
    try:
      issue_type = issue_info["issue_type"]
    except KeyError:
      raise exceptions.ValidationError("Ticket Type is mandatory.")
    else:
      if not issue_type.strip():
        raise exceptions.ValidationError("Ticket Type can not be blank.")

    # Ticket Priority
    try:
      issue_priority = issue_info["issue_priority"]
    except KeyError:
      raise exceptions.ValidationError("Ticket Priority is mandatory.")
    else:
      if issue_priority not in constants.AVAILABLE_PRIORITIES:
        raise exceptions.ValidationError("Ticket Priority is incorrect.")

    # Ticket Severity
    try:
      issue_severity = issue_info["issue_severity"]
    except KeyError:
      raise exceptions.ValidationError("Ticket Severity is mandatory.")
    else:
      if issue_severity not in constants.AVAILABLE_SEVERITIES:
        raise exceptions.ValidationError("Ticket Severity is incorrect.")

  @classmethod
  def _validate_generic_fields(cls, issue_info):
    """Validate generic fields for issue.

    Args:
        issue_info: dictionary with issue payload information
    """
    cls._validate_integer_fields(issue_info)
    cls._validate_string_fields(issue_info)

  def _validate_assessment_fields(self, issue_info):
    """Validate assessment fields for issue

      Args:
          issue_info: dictionary with issue payload information
    """
    self._validate_generic_fields(issue_info)
    self._validate_assessment_title(issue_info)

  @staticmethod
  def _validate_issue_id(issue_info):
    """Validate issue id field. It should be mandatory numeric field.

    Args:
        issue_info: dictionary with issue
        payload information
    """
    try:
      issue_id = issue_info["issue_id"]
    except KeyError:
      raise exceptions.ValidationError("Issue ID is mandatory.")
    else:
      try:
        int(issue_id)
      except (ValueError, TypeError):
        raise exceptions.ValidationError("Issue ID must be a number.")

  @staticmethod
  def _validate_assessment_title(issue_info):
    """Validate assessment fields for issue

      Args:
          issue_info: dictionary with issue payload information
    """

    try:
      title = issue_info["title"]
    except KeyError:
      raise exceptions.ValidationError("Title is mandatory.")

    if title is None or not title.strip():
      raise exceptions.ValidationError("Title can not be blank.")

  @classmethod
  def prepare_issue_json(cls, assessment, issue_tracker_info=None,
                         create_issuetracker=False):
    """Prepare parameters for issue create in bulk mode.

    Args:
      assessment: Instance of Assessment.
      issue_tracker_info: Dict with IssueTracker info.
      create_issuetracker: Bool indicator for crate issuetracker state.
    Returns:
        Dict with IssueTracker issue info.
    """
    del create_issuetracker  # Unused

    integration_utils.populate_issue_tracker_fields(
        assessment,
        issue_tracker_info,
        create_mode=True,
    )
    issue_info = cls._update_with_assmt_data_for_ticket_create(
        assessment,
        issue_tracker_info
    )
    issue_payload = cls._build_payload_ticket_create(
        assessment,
        issue_info
    )

    return issue_payload

  @classmethod
  def prepare_issue_update_json(cls, assessment, issue_tracker_info=None):
    """Prepare parameters for issue update in bulk mode.

    Args:
        assessment: Instance of Assessment.
        issue_tracker_info: Dict with IssueTracker info.
    Returns:
        Dict with IssueTracker issue info.
    """
    if not issue_tracker_info:
      issue_tracker_info = assessment.issue_tracker

    integration_utils.populate_issue_tracker_fields(
        assessment,
        issue_tracker_info
    )
    issue_info = cls._update_with_assmt_data_for_ticket_update(
        assessment,
        issue_tracker_info
    )
    issue_payload = cls._collect_payload_bulk_update(
        assessment,
        issue_info
    )

    return issue_payload

  @classmethod
  def prepare_comment_update_json(cls, assessment, comment, author):
    """Prepare parameters for comment update in bulk mode

    Args:
        assessment: Instance of Assessment.
        comment: comment for issue
        author: dictionary with issue information
    Returns:
        Dict with IssueTracker issue info.
    """
    issue_payload = cls._collect_payload_bulk_comment(
        assessment,
        comment,
        author
    )

    return issue_payload

  @classmethod
  def prepare_disable_comment_json(cls):
    """Prepare parameters for disable comment update in bulk mode

    Returns:
        Dict with IssueTracker issue info.
    """
    issue_payload = cls._collect_payload_disable()

    return issue_payload

  def _get_issuetracker_info(self, assessment, assessment_src):
    """Get default issue information.

    This function MUST NOT be called during import background task.

    Args:
        assessment_src: dictionary with issue information
    Returns:
        default information for Issue Tracker
    """

    # get from API dict if available
    issue_tracker_info_default = assessment_src.get("issue_tracker", {})
    if issue_tracker_info_default:
      return issue_tracker_info_default

    # get from template dict if available
    issue_tracker_info_default = self._get_issue_from_assmt_template(
        assessment_src.get("template", {})
    )
    if issue_tracker_info_default:
      issue_tracker_info_default["title"] = assessment.title
      return issue_tracker_info_default

    # get from audit
    if not issue_tracker_info_default:
      issue_tracker_info_default = self._get_issue_info_from_audit(
          assessment_src.get("audit", {})
      )
      issue_tracker_info_default["title"] = assessment.title
    return issue_tracker_info_default

  @staticmethod
  def _prepare_issue_delete(assessment):
    """Prepare parameters for issue delete.

    Args:
        assessment: object from Assessment model

    Returns:
        issue_obj: object from IssueTracker model
    """

    issue_obj = assessment.issuetracker_issue
    if issue_obj and issue_obj.enabled and issue_obj.issue_id:
      issue_info = {
          "status": "OBSOLETE",
          "comment": (
              "Assessment has been deleted. Changes to this GGRC "
              "Assessment will no longer be tracked within this bug."
          )
      }
    else:
      issue_info = {}

    return issue_obj, issue_info

  def handle_assessment_create(self, assessment, assessment_src):
    """Handle assessment issue create.

    Do not perform any actions if one of the following is true:
    * this is import background task
    * this is POST request and integration is disabled on audit or app level

    In case of import the work will be done by bulk_sync background task
    to speed up import

    Args:
        assessment: object from Assessment model
        assessment_src: dictionary with issue information
    """

    if assessment.is_import or not self._is_tracker_enabled(assessment.audit):
      return

    if not self._is_issue_on_create_enabled(assessment_src):
      self._create_disabled_record(assessment, assessment_src)
      return

    issue_id = assessment_src.get("issue_tracker", {}).get("issue_id")
    if issue_id:
      if not self._is_already_linked(assessment, issue_id):
        issue_info, _ = self._link_ticket(
            assessment,
            issue_id,
            assessment_src
        )

        all_models.IssuetrackerIssue.create_or_update_from_dict(
            assessment,
            issue_info
        )
    else:
      issue_info, _ = self._create_ticket(
          assessment,
          assessment_src
      )
      all_models.IssuetrackerIssue.create_or_update_from_dict(
          assessment,
          issue_info
      )

  @staticmethod
  def _create_disabled_record(assessment, assessment_src):
    """Create IssueTrackerIssue model for assessment with values from Audit.

    Args:
      assessment (Assessment): Assessment instance
      assessment_src (dict): dictionary with issue information
    """
    integration_utils.populate_issue_tracker_fields(
        assessment,
        assessment_src,
        with_update=True,
    )
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        assessment,
        assessment_src,
    )

  def handle_assessment_delete(self, assessment):
    """Handle assessment issue delete.

    Args:
        assessment: object from Assessment model
    """
    issue_obj, issue_info = self._prepare_issue_delete(
        assessment
    )

    if issue_info and issue_obj:
      sync_result = self._send_issue_delete(
          issue_obj.issue_id,
          issue_info
      )
      self._ticket_warnings_for_delete(
          sync_result,
          assessment
      )

    if issue_obj:
      db.session.delete(issue_obj)

  def handle_assessment_update(self, assessment, assessment_src,
                               initial_state):
    """Handle issue update.

    Args:
        assessment: object from Assessment model
        assessment_src: dictionary with issue information
        initial_state: initial state of Assessment before update
    """
    issue_obj = assessment.issuetracker_issue
    issue_initial_obj = issue_obj.to_dict() if issue_obj else {}
    issue_id_stored = issue_obj.issue_id if issue_obj else None

    issue_info = assessment_src.get("issue_tracker", {})
    issue_id_sent = issue_info.get("issue_id")

    if self._is_tracker_enabled(assessment.audit):
      if self._is_create_issue_mode(issue_id_stored, issue_id_sent):
        if self._is_issue_enabled(assessment_src):
          issue_db_info, _ = self._create_ticket(assessment, assessment_src)
          all_models.IssuetrackerIssue.create_or_update_from_dict(
              assessment,
              issue_db_info
          )
      elif self._is_create_detach_issue_mode(issue_id_stored, issue_id_sent):
        self._create_and_disable_ticket(
            assessment,
            issue_id_stored,
            issue_id_sent,
            assessment_src
        )
      elif self._is_link_issue_mode(issue_id_stored, issue_id_sent):
        if self._is_issue_enabled(assessment_src):
          if not self._is_already_linked(assessment, issue_id_sent):
            issue_db_info, _ = self._link_ticket(
                assessment,
                issue_id_sent,
                assessment_src
            )
            all_models.IssuetrackerIssue.create_or_update_from_dict(
                assessment,
                issue_db_info
            )
      elif (self._is_update_issue_mode(issue_id_stored, issue_id_sent) and
            not assessment_src["issue_tracker"].get("is_linking")):
        self._update_ticket(
            assessment,
            initial_state,
            issue_initial_obj,
            issue_id_stored,
            assessment_src
        )
      elif self._is_link_detach_issue_mode(issue_id_stored, issue_id_sent):
        self._link_and_detach_ticket(
            assessment,
            issue_id_sent,
            issue_id_stored,
            assessment_src
        )

  def handle_assessment_sync(self, assessment_src, issue_id,
                             issue_tracker_info):
    """Handle assessment synchronization with IssueTracker.

    Args:
        assessment_src: Dictionary with Issue Information from ggrc.
        issue_id: issue id for Issue Tracker
        issue_tracker_info: Dictionary with Issue Information from tracker
    """
    assessment, issue_info = assessment_src["object"], assessment_src["state"]
    if self._is_tracker_enabled(assessment.audit):
      issue_db_info = self._collect_assessment_sync_info(
          assessment,
          issue_info
      )
      issue_payload = self._collect_payload_sync(
          issue_db_info,
          issue_tracker_info
      )
      if self._is_need_sync(assessment.id, issue_payload, issue_tracker_info):
        sync_result = self._send_issue_sync(
            issue_id,
            issue_payload
        )
        self._ticket_warnings_for_sync(sync_result, assessment)
        if sync_result.status == SyncResult.SyncResultStatus.SYNCED:
          all_models.IssuetrackerIssue.create_or_update_from_dict(
              assessment,
              issue_db_info
          )

  def handle_audit_create(self, audit, audit_src):
    """Handle audit create for Issue Tracker.

    This function MUST NOT be called during import background task execution

    Args:
        audit: object from Audit model
        audit_src: dictionary with issue information
    """
    issue_db_info = self._collect_audit_info(
        audit,
        audit_src
    )
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        audit,
        issue_db_info
    )

  def handle_audit_update(self, audit, audit_src):
    """Handle audit update for Issue Tracker. Send comments to Issue for
    child Assessments if parent Audit was disabled

    This function MUST NOT be called during import background task execution

    Args:
        audit: object from Audit model
        audit_src: dictionary with issue information
    """
    issue_db_info = self._collect_audit_info(
        audit,
        audit_src
    )
    issue_initial_state = audit.issuetracker_issue and \
        audit.issuetracker_issue.enabled
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        audit,
        issue_db_info
    )
    self._handle_related_assmts_update(audit, issue_initial_state)

  @staticmethod
  def _handle_related_assmts_update(audit, issue_initial_state):
    """Send comments to Issues for child Assessments if parent Audit was
    disabled.

    Args:
      audit: parent audit object
      issue_initial_state: initial state of issue. True if it was enabled or
        False if it was disabled
    """
    if (audit.issuetracker_issue and
            audit.assessments and
            not audit.issuetracker_issue.enabled and
            audit.issuetracker_issue.enabled != issue_initial_state):

      import ggrc
      # run background task for send comment to issues,
      # associated with assessments
      ggrc.views.start_update_children_issues(audit.type, audit.id,
                                              audit.assessments[0].type)

  def handle_audit_issues_update(self, audit, initial_state):
    """Handle audit issues update for Issue Tracker.

    Args:
        audit: object from Audit model
        initial_state: object with previous Assessment state
    """
    is_start_update_audit = bool(
        self._is_audit_initial_exist(initial_state) and
        not self._is_audit_archive_the_same(audit, initial_state) and
        audit.issue_tracker.get("enabled")
    )
    if is_start_update_audit:
      import ggrc
      # run background task for update issues,
      # associated with audit
      ggrc.views.start_update_audit_issues(
          audit.id,
          constants.ARCHIVED_TMPL
          if audit.archived else constants.UNARCHIVED_TMPL
      )

  @staticmethod
  def handle_audit_delete(audit):
    """Handle audit issue delete.

    Args:
        audit: object from Audit model
    """
    if audit.issuetracker_issue:
      db.session.delete(audit.issuetracker_issue)

  def handle_assmt_template_create(self, assessment_template,
                                   assmt_template_src):
    """Handle assessment template create for Issue Tracker.

    This function MUST NOT be called during import background task execution

    Args:
        assessment_template: object from
        Assessment Template model
        assmt_template_src: dictionary with issue information
    """
    issue_db_info = self._collect_template_info(
        assessment_template,
        assmt_template_src
    )
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        assessment_template,
        issue_db_info
    )

  def handle_assmt_template_update(self, assessment_template,
                                   assmt_template_src):
    """Handle assessment template update for Issue Tracker.

    Args:
        assessment_template: object from
        Assessment Template model
        assmt_template_src: dictionary with issue information
    """
    issue_db_info = self._collect_template_info(
        assessment_template,
        assmt_template_src
    )
    all_models.IssuetrackerIssue.create_or_update_from_dict(
        assessment_template,
        issue_db_info
    )

  def handle_assmt_test_plan_update(self, assessment):
    """Handle assessment's test plan update.

    Args:
        assessment (models.Assessment): Assessment instance.
    """
    if (
        self._is_tracker_enabled(assessment.audit) and
        helper_utils.has_attr_changes(assessment, "test_plan")
    ):
      issue_obj = assessment.issuetracker_issue
      issue_id = issue_obj.issue_id if issue_obj else None
      if issue_id and integration_utils.is_already_linked(issue_id):
        self._add_update_test_plan_comment(assessment)

  @staticmethod
  def handle_template_delete(assessment_template):
    """Handle assessment template delete.

    Args:
        assessment_template: object
        from Assessment Template model
    """
    if assessment_template.issuetracker_issue:
      db.session.delete(assessment_template.issuetracker_issue)

  def _create_ticket(self, assessment, assessment_src):
    """Create Issue by Assessment info.

    Args:
        assessment: object from Assessment model
        assessment_src: dictionary with payload information

    Returns:
        issuetracker_info: dictionary with information
        for issue store
        sync_result.status: status of request to Issue Tracker
    """
    issuetracker_info = self._get_issuetracker_info(
        assessment,
        assessment_src
    )
    self._validate_assessment_fields(issuetracker_info)
    issuetracker_info = self._update_with_assmt_data_for_ticket_create(
        assessment,
        issuetracker_info
    )
    issue_payload = self._build_payload_ticket_create(
        assessment,
        issuetracker_info
    )
    sync_result = self._send_issue_create(
        issue_payload
    )
    self._update_issue_info(
        sync_result,
        issuetracker_info
    )
    self._ticket_warnings_for_create(
        sync_result,
        assessment
    )

    return issuetracker_info, sync_result.status

  def _update_ticket(self, assessment, initial_state,
                     issue_initial_obj, issue_id_stored,
                     assessment_src):
    # pylint: disable=too-many-arguments
    """Update Issue by Assessment info.

    Args:
        assessment: object from Assessment model
        initial_state: object with previous Assessment state
        issue_initial_obj: issue information from previous Assessment
        issue_id_stored: Issue id that stored for Assessment
        assessment_src: dictionary with payload information

    Returns:
        issue_db_info: dictionary with information
        for issue store
    """
    issue_info = assessment_src.get("issue_tracker", {})
    self._validate_assessment_fields(issue_info)
    issue_db_info = self._update_with_assmt_data_for_ticket_update(
        assessment,
        issue_info
    )
    issue_payload = self._build_payload_ticket_update(
        assessment,
        initial_state,
        issue_initial_obj,
        issue_db_info,
        assessment_src
    )
    sync_result = self._send_issue_update(
        issue_id_stored,
        issue_payload
    )
    self._ticket_warnings_for_update(
        sync_result,
        assessment
    )
    if sync_result.status == SyncResult.SyncResultStatus.SYNCED:
      all_models.IssuetrackerIssue.create_or_update_from_dict(
          assessment,
          issue_db_info
      )
    return issue_db_info

  def _link_ticket(self, assessment, issue_id, assessment_src):
    """Link Issue with Assessment info.

    Args:
        assessment: object from Assessment model
        issue_id: Issue Id for Assessment linking
        assessment_src: dictionary with payload information

    Returns:
        issuetracker_info: dictionary with information
        for issue store
        sync_result.status: status of request to Issue Tracker
    """
    issuetracker_info = self._get_issuetracker_info(
        assessment,
        assessment_src
    )
    self._validate_issue_id(issuetracker_info)
    self._handle_people_emails(
        assessment,
        issuetracker_info
    )
    issue_payload = self._build_payload_ticket_link(
        assessment,
        issuetracker_info
    )
    sync_result = self._send_issue_link(
        issue_id,
        issue_payload
    )
    self._update_issue_info(
        sync_result,
        issuetracker_info
    )
    self._ticket_warnings_for_link(
        sync_result,
        assessment
    )

    return issuetracker_info, sync_result.status

  def _detach_ticket(self, assessment, issue_id_stored,
                     issue_id_sent, issue_info):
    """Send detach comment to Issue.

    Args:
        assessment: object from Assessment model
        issue_id_stored: previous Issue Id, stored into database
        issue_id_sent: current Issue Id for Assessment
        issue_info: dictionary with information for Issue store

    Returns:
        issue_info: dictionary with information
        for issue store
    """
    issue_payload = self._build_payload_ticket_detach(
        issue_id_sent
    )
    sync_result = self._send_issue_detach(
        issue_id_stored,
        issue_payload
    )
    self._update_issue_info_on_detach(
        sync_result,
        issue_info
    )
    self._ticket_warnings_for_detach(
        sync_result,
        assessment
    )

    return issue_info

  def add_disable_comment(self, assessment, issue_id):
    """Send disable comment to Issue.

    Args:
        assessment: object from Assessment model
        issue_id: Issue Id for Assessment

    Returns:
        sync_result.status: status of request to Issue Tracker
    """
    issue_payload = self._collect_payload_disable()
    sync_result = self._send_issue_update(
        issue_id,
        issue_payload
    )
    self._ticket_warnings_for_update(
        sync_result,
        assessment
    )

    return sync_result.status

  def _create_and_disable_ticket(self, assessment, issue_id_stored,
                                 issue_id_sent, assessment_src):
    """'Create-disable' ticket on update

    Args:
        assessment: object from Assessment model
        assessment_src: dictionary with issue information
        issue_id_stored: issue id stored into db
        issue_id_sent: issue id send from client
    """
    if self._is_issue_enabled(assessment_src):
      issue_db_info, sync_status = self._create_ticket(
          assessment,
          assessment_src
      )
      if sync_status == SyncResult.SyncResultStatus.SYNCED:
        self._detach_ticket(
            assessment,
            issue_id_stored,
            issue_id_sent,
            issue_db_info
        )
        all_models.IssuetrackerIssue.create_or_update_from_dict(
            assessment,
            issue_db_info
        )
    elif assessment.issue_tracker.get("enabled") and assessment_src:
      sync_status = self.add_disable_comment(
          assessment,
          issue_id_stored
      )
      if sync_status == SyncResult.SyncResultStatus.SYNCED:
        all_models.IssuetrackerIssue.create_or_update_from_dict(
            assessment,
            {"enabled": False}
        )

  def _link_and_detach_ticket(self, assessment, issue_id_sent,
                              issue_id_stored, assessment_src):
    """'Link-detach' action on update

    Args:
        assessment: object from Assessment model
        assessment_src: dictionary with issue information
        issue_id_stored: issue id stored into db
        issue_id_sent: issue id send from client
    """
    if self._is_issue_enabled(assessment_src):
      if not self._is_already_linked(assessment, issue_id_sent):
        issue_db_info, sync_status = self._link_ticket(
            assessment,
            issue_id_sent,
            assessment_src
        )
        if sync_status == SyncResult.SyncResultStatus.SYNCED:
          issue_db_info = self._detach_ticket(
              assessment,
              issue_id_stored,
              issue_id_sent,
              issue_db_info
          )
          all_models.IssuetrackerIssue.create_or_update_from_dict(
              assessment,
              issue_db_info
          )

  def _add_update_test_plan_comment(self, assessment):
    """Send test plan update comment to issue.

    Args:
        assessment (models.Assessment): Assessment instance.
    """
    issue_id = assessment.issuetracker_issue.issue_id
    issue_payload = self._collect_payload_test_plan_upd(assessment.test_plan)
    sync_result = self._send_issue_update(issue_id, issue_payload)
    self._ticket_warnings_for_update(sync_result, assessment)

  @staticmethod
  def _get_issue_from_assmt_template(template_info):
    """Get assessment template information.

    Args:
        template_info: dictionary with assessment
        template information
    """
    if template_info:
      assessment_template = referenced_objects.get(
          template_info.get('type'),
          template_info.get('id')
      )
      if assessment_template \
              and hasattr(assessment_template, "issue_tracker"):
        return assessment_template.issue_tracker
    return {}

  @staticmethod
  def _get_issue_info_from_audit(audit_info):
    """Get audit issue information.

    Args:
        audit_info: dictionary with audit information
        for issue
    """
    if audit_info:
      audit = referenced_objects.get(
          audit_info.get('type'),
          audit_info.get('id')
      )
      if audit and hasattr(audit, "issue_tracker"):
        return audit.issue_tracker
    return {}

  @staticmethod
  def _get_reporter(audit):
    """Get reporter for issue tracker.

    Args:
        audit: object from Audit model

    Returns:
        reporter_email: email of audit captain.
    """

    captains = audit.get_persons_for_rolename("Audit Captains")
    reporter_email = sorted([captain.email for captain in captains])[0] \
        if captains else ""

    return reporter_email

  @staticmethod
  def _get_assignee(assessment):
    """Get assignee for issue tracker.

    Args:
        assessment: object from Assessment model
    Returns:
        assignee_email: email of assessment assignee.
    """
    assignees = assessment.get_persons_for_rolename("Assignees")
    assignee_email = sorted([assignee.email for assignee in assignees])[0] \
        if assignees else ""

    return assignee_email

  @staticmethod
  def _is_assignee_exists(assessment, assignee):
    """Check that current assignee exists.

    Args:
        assessment: assessment from Assessment model
        assignee: assignee email stored for Issue Tracker
    Returns:
        Boolean indicator for assignee exists
    """
    assignees = assessment.get_persons_for_rolename("Assignees")
    return bool([person.email for person in assignees
                 if person.email == assignee])

  @staticmethod
  def _is_reporter_exists(audit, reporter):
    """Check that current reporter exists.

    Args:
        audit: audit from Audit model
        reporter: reporter email stored for Issue Tracker
    Returns:
        Boolean indicator for reporter exists
    """
    captains = audit.get_persons_for_rolename("Audit Captains")
    return bool([person.email for person in captains
                 if person.email == reporter])

  def _get_assignee_on_sync(self, assessment, assignee_db):
    """Get assignee for issue tracker on synchronization.

    Args:
        assessment: assessment from Assessment model
        assignee_db: assignee email stored for Issue Tracker
    Returns:
        Assignee for Issue Tracker
    """
    if self._is_assignee_exists(assessment, assignee_db):
      assignee = assignee_db
    else:
      assignee = self._get_assignee(assessment)

    return assignee

  def _get_reporter_on_sync(self, audit, reporter_db):
    """Get reporter for issue tracker on synchronization.

    Args:
        audit: audit from Audit model
        reporter_db: reporter email stored for Issue Tracker
    Returns:
        Reporter for Issue Tracker
    """
    if self._is_reporter_exists(audit, reporter_db):
      reporter = reporter_db
    else:
      reporter = self._get_reporter(audit)

    return reporter

  @staticmethod
  def _get_ccs(reporter, assignee, assessment):
    """Get ccs for issue tracker.

    Args:
        reporter: reporter email for Issue Tracker
        assignee: assignee email for Issue Tracker
        assessment: object from Assessment model

    Returns:
        ccs: CC emails for Issue Tracker
    """
    captains = assessment.audit.get_persons_for_rolename(
        "Audit Captains"
    )
    captain_emails = [captain.email for captain in captains]

    assignees = assessment.get_persons_for_rolename("Assignees")
    assignee_emails = [person.email for person in assignees]

    emails = assignee_emails + captain_emails

    return [email for email in emails
            if email not in (reporter, assignee)]

  @staticmethod
  def _get_assessment_page_url(assessment):
    """Returns string URL for assessment view page.

    Args:
        assessment: object from Assessment model

    Returns:
        URL for assessment view page
    """
    return urlparse.urljoin(
        utils.get_url_root(),
        utils.view_url_for(assessment)
    )

  @staticmethod
  def _generate_custom_fields(fields):
    """Generate custom fields for issue.

    Args:
        fields: dictionary with parameters

    Returns:
        List with "custom fields" for issue tracker
    """
    custom_fields = []
    due_date_value = None

    if fields.get('due_date'):
      due_date_value = fields["due_date"].strftime("%Y-%m-%d")

    custom_fields.append(
        {
            "name": constants.CustomFields.DUE_DATE,
            "value": due_date_value,
            "type": "DATE",
            "display_string": constants.CustomFields.DUE_DATE
        }
    )

    return custom_fields

  @classmethod
  def _get_create_comment(cls, assessment):
    """Get comment for create issue.

    Args:
        assessment: object from Assessment model

    Returns:
        String for assessment comment
    """
    return cls._generate_comment(
        assessment,
        constants.INITIAL_COMMENT_TMPL
    )

  def _get_link_comment(self, assessment):
    """Get comment for link issue.

    Args:
        assessment: object from Assessment model

    Returns:
        String for assessment comment
    """
    return self._generate_comment(
        assessment,
        constants.LINK_COMMENT_TMPL
    )

  @staticmethod
  def _get_roles(assessment):
    """Returns emails associated with an assessment grouped by role.

    Args:
      assessment: An instance of Assessment model.

    Returns:
      A dict of {'role name': {set of emails}}.
    """
    audit = assessment.audit
    roles = {
        role: {
            acp.person.email for acp in acl.access_control_people
        } for role, acl in audit.acr_name_acl_map.items()
    }

    return roles

  @staticmethod
  def _get_state_comment(issue_initial_obj, issue_info):
    """Get comment for enabled/disabled assessment.

    Args:
        issue_initial_obj: issue information for previous Assessment state
        issue_info: issue information for Assessment state
    Returns:
        String representation of comment
    """
    if issue_initial_obj.get("enabled") != issue_info.get("enabled"):
      template = constants.ENABLED_TMPL \
          if issue_info.get("enabled") else constants.DISABLED_TMPL
    else:
      template = ""

    return template

  def _get_update_comment(self, assessment, initial_state,
                          issue_initial_obj, assessment_src):

    """Get comment for update issue.

    Args:
        assessment: object from Assessment model
        initial_state: object for Assessment previous state
        issue_initial_obj: issue information for Assessment previous state
        assessment_src: dictionary with payload information from client
    Returns:
        status for issue payload
        string representation of comment
    """
    comments = []

    enabled_comment = self._get_state_comment(
        issue_initial_obj,
        assessment_src["issue_tracker"]
    )
    if enabled_comment:
      comments.append(enabled_comment)

    status, status_comment = self._get_status_comment(
        assessment,
        initial_state
    )
    if status:
      comments.append(status_comment)

    comment_text, comment_author = self._get_added_comment_text(
        assessment_src
    )
    if comment_text is not None:
      comments.append(
          constants.COMMENT_TMPL.format(
              author=comment_author,
              comment=comment_text,
              model=assessment.__class__.__name__,
              link=self._get_assessment_page_url(assessment)
          )
      )

    return status, "\n\n".join(comments) if comments else ""

  @staticmethod
  def _get_added_comment_id(assessment_src):
    """Returns comment ID from given request.

    Args:
        assessment_src: dictionary with payload
        information from client

    Returns:
        Comment ID from given request
    """
    if not assessment_src:
      return None

    actions = assessment_src.get("actions") or {}
    related = actions.get("add_related") or []

    if not related:
      return None

    related_obj = related[0]

    if related_obj.get("type") != "Comment":
      return None

    return related_obj.get("id")

  def _get_status_comment(self, assessment, initial_state):
    """Return comment by current status.

    Args:
        assessment: current state of Assessment
        initial_state: initial state of Assessment

    Returns:
        Status for issue payload
        Comment regarding status
    """
    if initial_state.status == assessment.status:
      return None, None

    verifiers = self._get_roles(assessment).get("Verifiers")
    status_text = assessment.status
    if verifiers:
      status = constants.VERIFIER_STATUSES.get(
          (initial_state.status, assessment.status, assessment.verified)
      )
      # Corner case for custom status text.
      if assessment.verified and assessment.status == "Completed":
        status_text = "%s and Verified" % status_text
    else:
      status = constants.NO_VERIFIER_STATUSES.get(
          (initial_state.status, assessment.status)
      )

    if status:
      return status, constants.STATUS_CHANGE_TMPL % (
          status_text, self._get_assessment_page_url(assessment)
      )

    return None, None

  def _get_added_comment_text(self, assessment_src):
    """Returns text of added comment.

    Args:
        assessment_src: dictionary with payload
        information from client

    Returns:
        Comment description
        Comment creator name
    """
    comment_id = self._get_added_comment_id(assessment_src)
    if comment_id is not None:
      comment_row = db.session.query(
          all_models.Comment.description,
          all_models.Person.email,
          all_models.Person.name
      ).outerjoin(
          all_models.Person,
          all_models.Person.id == all_models.Comment.modified_by_id,
      ).filter(
          all_models.Comment.id == comment_id
      ).first()
      if comment_row is not None:
        desc, creator_email, creator_name = comment_row
        if not creator_name:
          creator_name = creator_email
        return html2text.HTML2Text().handle(desc).strip(), creator_name
    return None, None

  @classmethod
  def _generate_comment(cls, assessment, template):
    """Generate comment by template.

    Args:
        assessment: object from Assessment model
        template: String for template generation

    Returns:
        String for assessment comment
    """
    comments = [template % cls._get_assessment_page_url(assessment)]
    test_plan = assessment.test_plan
    if test_plan:
      comments.extend([
          "Following is the assessment Requirements/Assessment Procedure "
          "from GGRC:",
          html2text.HTML2Text().handle(test_plan).strip("\n"),
      ])

    return "\n".join(comments)

  @classmethod
  def _generate_common_comment(cls, assessment, comment, author):
    """Generate comment with author.

    Args:
        assessment: object from Assessment model
        comment: comment for issue
        author: dictionary with issue information
    Returns:
        String for assessment comment
    """
    comment = html2text.HTML2Text().handle(comment).strip()
    template = constants.COMMENT_TMPL.format(
        author=author,
        comment=comment,
        model=assessment.__class__.__name__,
        link=cls._get_assessment_page_url(assessment),
    )

    return template

  @staticmethod
  def _generate_detach_comment(issue_id):
    """Generate detach comment.

    Args:
        issue_id: issue id for Issue Tracker.

    Returns:
        String for detach comment
    """
    template = (
        "Another bug {issue_id} has been linked to track changes to the "
        "GGRC Assessment. Changes to the GGRC Assessment will no longer be "
        "tracked within this bug."
    )

    return template.format(issue_id=issue_id)

  @staticmethod
  def _generate_test_plan_upd_comment(test_plan):
    """Generate test plan update comment.

    Args:
      test_plan (str): New test plan to include into comment.

    Returns:
      String representing comment for test plan update.
    """
    test_plan = html2text.HTML2Text().handle(test_plan).strip("\n")
    return constants.ASMT_TEST_PLAN_UPD_TMPL.format(test_plan)

  @classmethod
  def _handle_people_emails(cls, assessment, issue_db_info):
    """Handle emails.

    Args:
      assessment: object from Assessment model
      issue_db_info: dictionary with issue information
    """
    reporter = cls._get_reporter(assessment.audit)
    assignee = cls._get_assignee(assessment)
    ccs = cls._get_ccs(
        reporter,
        assignee,
        assessment
    )
    issue_db_info.update({
        "assignee": assignee,
        "reporter": reporter,
        "cc_list": ccs,
        "people_sync_enabled": cls._is_people_sync_enabled(assessment.audit),
    })

  @classmethod
  def _update_with_assmt_data_for_ticket_create(cls, assessment, assmt_src):
    # pylint: disable=invalid-name
    """Update issue information with assessment data.

    Args:
        assessment: object from Assessment model
        assmt_src: dictionary with issue information

    Returns:
        issue_db_info: dictionary with information
        for store issue into db
    """

    issue_db_info = {
        "object_id": assessment.id,
        "object_type": assessment.__class__.__name__,
        "title": assmt_src["title"],
        "component_id": assmt_src["component_id"],
        "hotlist_id": assmt_src.get("hotlist_id"),
        "issue_tracked_obj": assessment,
        "issue_type": assmt_src["issue_type"],
        "issue_priority": assmt_src["issue_priority"],
        "issue_severity": assmt_src["issue_severity"],
        "enabled": True,
        "due_date": assessment.start_date,
        "issue_id": assmt_src.get("issue_id"),
    }

    cls._handle_people_emails(assessment, issue_db_info)

    return issue_db_info

  @classmethod
  def _update_with_assmt_data_for_ticket_update(cls, assessment, assmt_src):
    # pylint: disable=invalid-name
    """Collect issue information for assessment update.

    Args:
        assessment: object from Assessment model
        assmt_src: dictionary with issue information

    Returns:
        issue_db_info: dictionary with information
        for update issue db
    """
    issue_db_info = {
        "object_id": assessment.id,
        "object_type": assessment.__class__.__name__,
        "title": assmt_src["title"],
        "component_id": assmt_src["component_id"],
        "hotlist_id": assmt_src.get("hotlist_id"),
        "issue_tracked_obj": assessment,
        "issue_type": assmt_src["issue_type"],
        "issue_priority": assmt_src["issue_priority"],
        "issue_severity": assmt_src["issue_severity"],
        "enabled": True,
        "due_date": assessment.start_date,
        "issue_id": assmt_src["issue_id"],
        "issue_url": assmt_src["issue_url"],
        "people_sync_enabled": cls._is_people_sync_enabled(assessment.audit),
    }

    return issue_db_info

  def _collect_assessment_sync_info(self, assessment, issue_info):
    """Collect issue information for sync.

    Args:
        issue_info: dictionary with issue information for sync.
    Returns:
        issue_db_info: dictionary with information
        for synchronization issue db
    """
    reporter = self._get_reporter_on_sync(
        assessment.audit,
        issue_info["reporter"]
    )
    assignee = self._get_assignee_on_sync(
        assessment,
        issue_info["assignee"]
    )
    ccs = self._get_ccs(
        reporter,
        assignee,
        assessment
    )

    issue_db_info = {
        "object_id": assessment.id,
        "object_type": assessment.__class__.__name__,
        "issue_tracked_obj": assessment,
        "issue_type": issue_info["type"],
        "component_id": issue_info["component_id"],
        "issue_priority": issue_info["priority"],
        "issue_severity": issue_info["severity"],
        "enabled": True,
        "status": issue_info["status"],
        "assignee": assignee,
        "reporter": reporter,
        "cc_list": ccs,
        "due_date": assessment.start_date,
        "people_sync_enabled": self._is_people_sync_enabled(assessment.audit),
    }

    return issue_db_info

  @staticmethod
  def _collect_audit_info(audit, audit_src):
    """Collect issue database information for audit.

    Args:
        audit: object from Audit model
        audit_src: dictionary with issue information

    Returns:
        issue_db_info: dictionary with information
        for store issue into db
    """
    issue_db_info = {
        "object_id": audit.id,
        "object_type": audit.__class__.__name__,
        "component_id": audit_src.get(
            "component_id", audit.issue_tracker.get(
                "component_id",
                constants.DEFAULT_ISSUETRACKER_VALUES["component_id"])
        ),
        "hotlist_id": audit_src.get(
            "hotlist_id", audit.issue_tracker.get(
                "hotlist_id",
                constants.DEFAULT_ISSUETRACKER_VALUES["hotlist_id"])
        ),
        "issue_type": audit_src.get(
            "issue_type", audit.issue_tracker.get(
                "issue_type",
                constants.DEFAULT_ISSUETRACKER_VALUES["issue_type"])
        ),
        "issue_priority": audit_src.get(
            "issue_priority", audit.issue_tracker.get(
                "issue_priority",
                constants.DEFAULT_ISSUETRACKER_VALUES["issue_priority"])
        ),
        "issue_severity": audit_src.get(
            "issue_severity", audit.issue_tracker.get(
                "issue_severity",
                constants.DEFAULT_ISSUETRACKER_VALUES["issue_severity"])
        ),
        "enabled": audit_src["enabled"],
        "people_sync_enabled": audit_src.get("people_sync_enabled", True),
    }

    return issue_db_info

  @staticmethod
  def _collect_template_info(assessment_template, assmt_template_src):
    """Collect issue information for assessment template.

    Args:
        assessment_template: object from Assessment Template model
        assmt_template_src: dictionary with issue information

    Returns:
        issue_db_info: dictionary with information
        for store issue into db
    """
    issue_db_info = {
        "object_id": assessment_template.id,
        "object_type": assessment_template.__class__.__name__,
        "component_id": assmt_template_src.get(
            "component_id",
            constants.DEFAULT_ISSUETRACKER_VALUES["component_id"]),
        "hotlist_id": assmt_template_src.get(
            "hotlist_id",
            constants.DEFAULT_ISSUETRACKER_VALUES["hotlist_id"]),
        "issue_type": assmt_template_src.get(
            "issue_type",
            constants.DEFAULT_ISSUETRACKER_VALUES["issue_type"]),
        "issue_priority": assmt_template_src.get(
            "issue_priority",
            constants.DEFAULT_ISSUETRACKER_VALUES["issue_priority"]),
        "issue_severity": assmt_template_src.get(
            "issue_severity",
            constants.DEFAULT_ISSUETRACKER_VALUES["issue_severity"]),
        "enabled": assmt_template_src.get("enabled")
    }

    return issue_db_info

  @classmethod
  def _build_payload_ticket_create(cls, assessment, issue_info):
    """Build payload for Issue create.

    Args:
        assessment: object from Assessment model
        issue_info: dictionary with issue information

    Returns:
        issue_payload: dictionary with information
        for issue payload
    """
    issue_payload = {
        "component_id": int(issue_info["component_id"]),
        "hotlist_ids": [int(issue_info["hotlist_id"])]
        if issue_info["hotlist_id"] else [],
        "title": issue_info["title"],
        "type": issue_info["issue_type"],
        "priority": issue_info["issue_priority"],
        "severity": issue_info["issue_severity"],
        "reporter": issue_info["reporter"],
        "assignee": issue_info["assignee"],
        "verifier": issue_info["assignee"],
        "status": constants.CREATE_STATUSES_MAPPING.get(assessment.status),
        "ccs": issue_info["cc_list"],
        "comment": cls._get_create_comment(assessment)
    }

    custom_fields = cls._generate_custom_fields(
        {"due_date": assessment.start_date}
    )
    if custom_fields:
      issue_payload["custom_fields"] = custom_fields

    return issue_payload

  def _build_payload_ticket_link(self, assessment, issue_info):
    """Build payload for link Issue ticket.

    Args:
        assessment: object from Assessment model
        issue_info: dictionary with issue information

    Returns:
        issue_payload: dictionary with information
        for issue link
    """
    issue_id = issue_info["issue_id"]
    issue_tracker_info, error = self._get_issue(
        issue_id
    )

    if error:
      logger.error(
          "Unable to link a ticket while creating object ID=%d: %s",
          assessment.id,
          error,
      )
      assessment.add_warning(
          "Ticket tracker ID does not exist or you do not have access to it."
      )
      issue_payload = {}
    else:
      issue_info = self._merge_issue_information(
          issue_info,
          issue_tracker_info["issueState"]
      )

      issue_payload = {
          "component_id": int(issue_info["component_id"]),
          "hotlist_ids": [int(issue_info["hotlist_id"])]
          if issue_info["hotlist_id"] else [],
          "title": issue_info["title"],
          "type": constants.DEFAULT_ISSUETRACKER_VALUES["issue_type"],
          "priority": issue_info["issue_priority"],
          "severity": issue_info["issue_severity"],
          "reporter": issue_info["reporter"],
          "assignee": issue_info["assignee"],
          "verifier": issue_info["assignee"],
          "status": constants.STATUSES_MAPPING.get(
              assessment.status
          ),
          "ccs": issue_info["cc_list"],
          "comment": self._get_link_comment(assessment)
      }

      custom_fields = self._generate_custom_fields(
          {"due_date": assessment.start_date}
      )
      if custom_fields:
        issue_payload["custom_fields"] = custom_fields

    return issue_payload

  def _build_payload_ticket_update(self, assessment, initial_state,
                                   issue_initial_obj, issue_info,
                                   assessment_src):
    # pylint: disable=too-many-arguments
    """Collect issue update payload for assessment.

    Args:
        assessment: object from Assessment model
        initial_state: previous state of Assessment object
        issue_initial_obj: issue information for previous Assessment state
        issue_info: dictionary with issue information
        assessment_src: dictionary with payload information from client

    Returns:
        issue_payload: dictionary with information
        for issue update payload
    """
    status_comment, comment = self._get_update_comment(
        assessment,
        initial_state,
        issue_initial_obj,
        assessment_src
    )
    issue_payload = {
        "component_id": int(issue_info["component_id"]),
        "hotlist_ids": [int(issue_info["hotlist_id"])]
        if issue_info["hotlist_id"] else [],
        "title": issue_info["title"],
        "priority": issue_info["issue_priority"],
        "severity": issue_info["issue_severity"],
        "status": constants.STATUSES_MAPPING.get(
            assessment.status
        ) if not status_comment else status_comment
    }

    custom_fields = self._generate_custom_fields(
        {"due_date": assessment.start_date}
    )

    if custom_fields:
      issue_payload["custom_fields"] = custom_fields

    if comment:
      issue_payload["comment"] = comment

    return issue_payload

  def _collect_payload_sync(self, issue_db_info, issue_tracker_info):
    """Collect issue synchronization payload for assessment.

    Args:
        issue_db_info: Dictionary with Issue Information from db.
        issue_tracker_info: Dictionary with Issue Information from tracker
    Returns:
        issue_payload: Dictionary with information
        for Issue payload.
    """
    reporter = self._merge_reporter(
        issue_db_info["reporter"],
        issue_tracker_info["reporter"],
        people_sync_enabled=issue_db_info["people_sync_enabled"],
    )
    assignee = self._merge_assignee(
        issue_db_info["assignee"],
        issue_tracker_info["assignee"],
        people_sync_enabled=issue_db_info["people_sync_enabled"],
    )
    ccs = self._merge_ccs(
        issue_db_info["cc_list"],
        issue_tracker_info["ccs"],
        people_sync_enabled=issue_db_info["people_sync_enabled"],
    )
    reporters_differ = self._is_reporters_not_equals(
        issue_db_info["reporter"], issue_tracker_info["reporter"])
    if issue_db_info["people_sync_enabled"] and reporters_differ:
      ccs.add(issue_db_info["reporter"])

    issue_payload = {
        "component_id": int(issue_db_info["component_id"]),
        "type": issue_db_info["issue_type"],
        "priority": issue_db_info["issue_priority"],
        "severity": issue_db_info["issue_severity"],
        "reporter": reporter,
        "assignee": assignee,
        "verifier": assignee,
        "status": constants.STATUSES_MAPPING.get(
            issue_db_info.pop("status", None)
        ),
        "ccs": list(ccs)
    }

    custom_fields = self._generate_custom_fields(
        {"due_date": issue_db_info["due_date"]}
    )
    if custom_fields:
      issue_payload["custom_fields"] = custom_fields

    return issue_payload

  @classmethod
  def _collect_payload_bulk_update(cls, assessment, issue_info):
    """Collect issue bulk update payload for assessment.

    Args:
        assessment: object from Assessment model
        issue_info: dictionary with issue information
    Returns:
        issue_payload: dictionary with information
        for issue bulk update payload
    """
    issue_payload = {
        "component_id": int(issue_info["component_id"]),
        "hotlist_ids": [int(issue_info["hotlist_id"])]
        if issue_info["hotlist_id"] else [],
        "title": issue_info["title"],
        "type": issue_info["issue_type"],
        "priority": issue_info["issue_priority"],
        "severity": issue_info["issue_severity"]
    }

    custom_fields = cls._generate_custom_fields(
        {"due_date": assessment.start_date}
    )
    if custom_fields:
      issue_payload["custom_fields"] = custom_fields

    return issue_payload

  @classmethod
  def _collect_payload_bulk_comment(cls, assessment, comment, author):
    """Collect issue bulk comment payload

    Args:
        assessment: object from Assessment model
        comment: comment for issue
        author: dictionary with issue information
    Returns:
        issue_payload: dictionary with information
        for issue bulk update payload
    """
    issue_payload = {
        "comment": cls._generate_common_comment(
            assessment,
            comment,
            author
        )
    }

    return issue_payload

  def _build_payload_ticket_detach(self, issue_id):
    """Collect detach assessment payload.

    Args:
        issue_id: issue id for assessment

    Returns:
        issue_payload: dictionary with information
        for assessment detach
    """
    issue_payload = {
        "comment": self._generate_detach_comment(issue_id),
        "status": constants.OBSOLETE_ISSUE_STATUS
    }

    return issue_payload

  @staticmethod
  def _collect_payload_disable():
    """Collect disable assessment payload.

    Returns:
        issue_payload: dictionary with information
        for assessment disable
    """
    issue_payload = {
        "comment": constants.DISABLED_TMPL
    }

    return issue_payload

  @classmethod
  def _collect_payload_test_plan_upd(cls, test_plan):
    """Collect assessment's test plan update payload."""
    return {
        "comment": cls._generate_test_plan_upd_comment(test_plan)
    }

  def _merge_issue_information(self, issue_info_db, issue_tracker_info):
    """Merge issue information with Issue Tracker.

    Args:
        issue_info_db: issue information from ggrc system
        issue_tracker_info: issue information from Issue Tracker

    Returns:
        issue_info_db: updated issue information
    """
    reporter = self._merge_reporter(
        issue_info_db["reporter"],
        issue_tracker_info["reporter"],
        people_sync_enabled=issue_info_db["people_sync_enabled"],
    )
    assignee = self._merge_assignee(
        issue_info_db["assignee"],
        issue_tracker_info["assignee"],
        people_sync_enabled=issue_info_db["people_sync_enabled"],
    )
    ccs = self._merge_ccs(
        issue_info_db["cc_list"],
        issue_tracker_info.get("ccs", []),
        people_sync_enabled=issue_info_db["people_sync_enabled"],
    )

    reporters_differ = self._is_reporters_not_equals(
        issue_info_db["reporter"], issue_tracker_info["reporter"])
    if issue_info_db["people_sync_enabled"] and reporters_differ:
      ccs.add(issue_info_db["reporter"])

    issue_info_db.update({
        "assignee": assignee,
        "reporter": reporter,
        "cc_list": list(ccs)
    })

    # use component_id and hotlist_id form the linked issue
    issue_info_db["component_id"] = self._extract_component_id(
        issue_tracker_info
    )
    issue_info_db["hotlist_id"] = self._extract_hotlist_id(
        issue_tracker_info
    )
    issue_info_db["issue_priority"] = issue_tracker_info.get("priority")
    issue_info_db["issue_severity"] = issue_tracker_info.get("severity")
    issue_info_db["title"] = issue_tracker_info.get("title")

    return issue_info_db

  @staticmethod
  def _extract_component_id(issue_tracker_info):
    """Extract component_id from issue_tracker_info"""
    try:
      component_id = issue_tracker_info["component_id"]
    except KeyError:
      raise flask_exceptions.ServiceUnavailable(
          "component_id attribute is mandatory. "
          "Please check if integration service API changed."
      )
    return component_id

  @staticmethod
  def _extract_hotlist_id(issue_tracker_info):
    """Extract hotlist_id from issue_tracker_info"""
    try:
      hotlist_id = issue_tracker_info["hotlist_ids"][0]
    except (KeyError, IndexError):
      hotlist_id = ""
    return hotlist_id

  @staticmethod
  def _merge_reporter(reporter_db, reporter_tracker,
                      people_sync_enabled=True):
    """Merge reporter with Issue Tracker.

    Args:
        reporter_db: reporter from ggrc system.
        reporter_tracker: reporter from Issue Tracker.
        people_sync_enabled: flag indicating whether assignees from GGRC
          system should be synced with Issue Tracker or not.

    Returns:
        Reporter regarding business rules
    """
    # pylint: disable=unused-argument
    result = reporter_tracker or ""
    if people_sync_enabled:
      result = result or reporter_db
    return result

  @staticmethod
  def _merge_assignee(assignee_db, assignee_tracker,
                      people_sync_enabled=True):
    """Merge assignee with Issue Tracker.

    Args:
        assignee_db: assignee from ggrc system.
        assignee_tracker: assignee from Issue Tracker.
        people_sync_enabled: flag indicating whether assignees from GGRC
          system should be synced with Issue Tracker or not.

    Returns:
        Assignee regarding business rules
    """
    result = assignee_tracker or ""
    if people_sync_enabled:
      result = assignee_db or result
    return result

  @staticmethod
  def _merge_ccs(ccs_db, ccs_tracker, people_sync_enabled=True):
    """Merge ccs with Issue Tracker.

    Args:
        ccs_db: ccs from ggrc system.
        ccs_tracker: ccs from Issue Tracker.
        people_sync_enabled: flag indicating whether assignees from GGRC
          system should be synced with Issue Tracker or not.

    Returns:
        Ccs regarding business rules
    """
    ccs = set(ccs_tracker)
    if people_sync_enabled:
      ccs |= set(ccs_db)
    return ccs

  def _send_issue_create(self, issue_payload):
    """Create issue in Issue Tracker.

    Args:
        issue_payload: dictionary with information for issue payload

    Returns:
        SyncResult object with request status
    """
    try:
      response = sync_utils.create_issue(
          self._client,
          issue_payload
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED,
          issue_id=response["issueId"]
      )

  def _send_issue_update(self, issue_id, issue_payload):
    """Update issue in Issue Tracker.

    Args:
        issue_id: issue id for Issue Tracker
        issue_payload: dictionary with information
        for issue payload

    Returns:
        SyncResult object with request status
    """
    try:
      sync_utils.update_issue(
          self._client,
          issue_id,
          issue_payload
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error,
          issue_id=issue_id
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED,
          issue_id=issue_id
      )

  def _send_issue_sync(self, issue_id, issue_payload):
    """Sync issue in Issue Tracker.

    Args:
        issue_id: issue id for Issue Tracker
        issue_payload: dictionary with information
        for issue payload
    """
    try:
      sync_utils.update_issue(
          self._client,
          issue_id,
          issue_payload
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error,
          issue_id=issue_id
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED,
          issue_id=issue_id
      )

  def _send_issue_delete(self, issue_id, issue_info):
    """Delete issue from Issue Tracker.

    Args:
        issue_id: issue id for Issue Tracker
        issue_info: dictionary with issue information
    """
    try:
      sync_utils.update_issue(
          self._client,
          issue_id,
          issue_info
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error,
          issue_id=issue_id
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED,
          issue_id=issue_id
      )

  def _send_issue_link(self, issue_id, issue_payload):
    """Link assessment to issue in Issue Tracker.

    Args:
        issue_id: issue id for Issue Tracker
        issue_payload: dictionary with information
        for issue payload
    """
    try:
      sync_utils.update_issue(
          self._client,
          issue_id,
          issue_payload
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED,
          issue_id=issue_id
      )

  def _send_issue_detach(self, issue_id, issue_payload):
    """Detach assessment issue from Issue Tracker.

    Args:
        issue_id: previous issue id for Issue Tracker
        issue_payload: dictionary with information
        for issue payload
    """
    try:
      sync_utils.update_issue(
          self._client,
          issue_id,
          issue_payload
      )
    except integrations_errors.Error as error:
      return SyncResult(
          status=SyncResult.SyncResultStatus.ERROR,
          error=error,
          issue_id=issue_id
      )
    else:
      return SyncResult(
          status=SyncResult.SyncResultStatus.SYNCED
      )

  @staticmethod
  def bulk_children_gen_allowed(assessment):
    """Check if user has permissions to synchronize issuetracker issue.

    Args:
      assessment: Assessment instance for which issue
      should be generated/updated.

    Returns:
      True if it's allowed, False if not allowed.
    """
    return all([
        permissions.is_allowed_update_for(assessment),
        permissions.is_allowed_update_for(assessment.audit)
    ])

  # pylint: disable=invalid-name
  @staticmethod
  def create_missing_issuetrackerissues(parent_type, parent_id):
    """We need to create issue_tracker_info for related assessments.

    Assessment created without issuetracker_issue if parent Audit's
    issuetracker_issue is disabled. But load_issuetracked_objects assumes that
    each Assessment has issuetracker_issue.

    Returns:
        List with Issuetracker issues.
    """
    new_issuetracker_issues = []
    if parent_type == "Audit":
      audit = all_models.Audit.query.get(parent_id)
      if audit.issuetracker_issue and audit.assessments:
        issue_tracker_info = audit.issuetracker_issue.get_issue(
            parent_type, parent_id
        ).to_dict()
        for assessment in audit.assessments:
          if assessment.issuetracker_issue is None:
            new_issuetracker_issues.append(
                all_models.IssuetrackerIssue.create_or_update_from_dict(
                    assessment, issue_tracker_info
                )
            )
            # flush is needed here to
            # 'load_issuetracked_objects' be able to load
            # missing assessments
            db.session.flush()
    return new_issuetracker_issues

  @staticmethod
  def load_issuetracked_objects(parent_type, parent_id, is_synced=False):
    """
      Fetch issuetracked objects from db.

      Args:
        parent_id: id of a parent object
        parent_type: type of a parent object (should be Audit)
        is_synced: flag indicates that issue was created (True if issue id
          is not none)

      Returns:
        list of Assessment objects
    """
    if parent_type != "Audit":
      return []
    query = all_models.Assessment.query.join(
        all_models.IssuetrackerIssue,
        sa.and_(
            all_models.IssuetrackerIssue.object_type == "Assessment",
            all_models.IssuetrackerIssue.object_id == all_models.Assessment.id
        )
    ).join(
        all_models.Audit,
        all_models.Audit.id == all_models.Assessment.audit_id
    ).filter(all_models.Audit.id == parent_id)

    if is_synced:
      query = query.filter(
          all_models.IssuetrackerIssue.issue_id.isnot(None)
      )
    else:
      query = query.filter(
          all_models.IssuetrackerIssue.issue_id.is_(None)
      )
    return query.options(
        sa.orm.Load(all_models.Assessment).undefer_group(
            "Assessment_complete",
        ).subqueryload(
            all_models.Assessment.audit
        ).subqueryload(
            all_models.Audit.issuetracker_issue
        )
    )

  @staticmethod
  def _is_tracker_enabled(audit):
    """Returns a boolean whether issue tracker integration feature is enabled.

    Args:
      audit: object from Audit model

    Returns:
      A boolean, True if feature is enabled or False otherwise.
    """
    if not common_handlers.global_synchronization_enabled():
      return False

    audit_tracker_info = audit.issue_tracker or {}
    if not audit_tracker_info.get("enabled"):
      return False
    return True

  @staticmethod
  def _is_state_updated(audit, initial_state):
    """Returns a boolean whether issue tracker integration feature that is
    updated.

    Args:
      audit: object from Audit model
      initial_state: object state before update

    Returns:
      A boolean, True if feature state is changed or False otherwise.
    """
    return audit.issue_tracker.get("enabled") != \
        initial_state.issue_tracker.get("enabled")

  @staticmethod
  def _is_people_sync_enabled(audit):
    """Returns a boolean whether people sync feature is enabled.

    Args:
      audit: Audit model instance.

    Returns:
      A boolean, True if feature is enabled or False otherwise.
    """
    audit_tracker_info = audit.issue_tracker or {}
    if not audit_tracker_info.get("people_sync_enabled"):
      return False
    return True

  @staticmethod
  def _is_audit_initial_exist(initial_state):
    """Check that audit initial state exists.

    Args:
        initial_state: initial state of Assessment
    Returns:
        Boolean indicator for state
    """
    if not initial_state:
      logger.debug(
          "Initial state of an Audit is not provided, "
          "skipping IssueTracker update."
      )
      return False
    return True

  @staticmethod
  def _is_audit_archive_the_same(audit, initial_state):
    """Check that archive for audit the same.

    Args:
        audit: object from Audit model
        initial_state: initial state of Assessment
    Returns:
        Boolean indicator for state
    """
    return audit.archived == initial_state.archived

  def _is_need_sync(self, assessment_id, issue_payload, issue_tracker_info):
    """Check that synchronization necessary for assessment.

    Args:
        issue_payload: issue payload information for sync
        issue_tracker_info: issue information from Issue Tracker
    Returns:
        Boolean indicator for issue synchronization
    """
    if not issue_payload.get("status"):
      logger.error(
          "Inexistent Issue Tracker status for assessment ID=%d "
          "with status: %s.",
          assessment_id,
          issue_payload.get("status")
      )

      return False

    is_custom_fields_same, remove_fields = self.custom_fields_processing(
        issue_payload,
        issue_tracker_info,
    )
    if remove_fields:
      issue_payload.pop("custom_fields", [])

    is_ccs_same = self._is_ccs_same(
        issue_payload,
        issue_tracker_info,
    )

    is_common_fields_same = self._is_common_fields_same(
        issue_payload,
        issue_tracker_info
    )

    return not all([is_ccs_same, is_common_fields_same, is_custom_fields_same])

  @staticmethod
  def _is_creation_mode(issue_obj, issue_id):
    """Indicator for assessment update mode.

    Issue for assessment should be created in Issue Tracker
    in some cases (turn on issue tracker sync and etc.).
    This method is detect create or update mode for assessment,
    that will be updated

    Args:
        issue_obj: Issue object for Assessment
        issue_id: issue id from issue tracker payload

    Returns:
        Boolean indicator for update/create mode
    """
    return not (issue_obj and issue_obj.issue_id and issue_id)

  @staticmethod
  def _is_create_issue_mode(issue_id_stored, issue_id_sent):
    """Update mode when issue will be created.

    Args:
        issue_id_stored: issue id from database
        issue_id_sent: issue id from client

    Returns:
        Boolean indicator mode
    """
    return not issue_id_stored and not issue_id_sent

  @staticmethod
  def _is_create_detach_issue_mode(issue_id_stored, issue_id_sent):
    """Update mode when issue will be created and detached another.

    Args:
        issue_id_stored: issue id from database
        issue_id_sent: issue id from client

    Returns:
        Boolean indicator mode
    """
    return issue_id_stored and not issue_id_sent

  @staticmethod
  def _is_link_issue_mode(issue_id_stored, issue_id_sent):
    """Update mode when issue will be linked.

    Args:
        issue_id_stored: issue id from database
        issue_id_sent: issue id from client

    Returns:
        Boolean indicator mode
    """
    return not issue_id_stored and issue_id_sent

  @staticmethod
  def _is_update_issue_mode(issue_id_stored, issue_id_sent):
    """Update mode when issue will be updated.

    Args:
        issue_id_stored: issue id from database
        issue_id_sent: issue id from client

    Returns:
        Boolean indicator mode
    """
    return (issue_id_stored and
            issue_id_sent and
            int(issue_id_stored) == int(issue_id_sent))

  @staticmethod
  def _is_link_detach_issue_mode(issue_id_stored, issue_id_sent):
    """Update mode when issue will be linked and detached another.

    Args:
        issue_id_stored: issue id from database
        issue_id_sent: issue id from client

    Returns:
        Boolean indicator mode
    """
    return (issue_id_stored and
            issue_id_sent and
            issue_id_stored != issue_id_sent)

  @staticmethod
  def _is_reporters_not_equals(reporter_db, reporter_tracker):
    """Check that current reports not equals

    Args:
        reporter_db: reporter from database
        reporter_tracker: reporter from Issue Tracker

    Returns:
        Boolean indicator that reporter was changed
    """
    if reporter_db and reporter_tracker:
      if reporter_db != reporter_tracker:
        return True
    return False

  @staticmethod
  def _is_already_linked(assessment, issue_id):
    """Check that issue id already linked to object.

    Args:
        assessment: object from Assessment model
        issue_id: issue id from issue tracker payload

    Returns:
        Boolean indicator for update/create mode
    """
    if integration_utils.is_already_linked(issue_id):
      logger.error(
          "Unable to link a ticket while creating "
          "object ID=%d: %s ticket ID is "
          "already linked to another GGRC object",
          assessment.id,
          issue_id
      )
      assessment.add_warning(
          "This ticket was already linked to another "
          "GGRC issue, assessment or "
          "review object. Linking the same ticket "
          "to multiple objects is not "
          "allowed due to potential for conflicting updates."
      )
      return True

    return False

  @staticmethod
  def _is_issue_enabled(assessment_src):
    """Check that issue tracker enabled.

    Args:
      assessment_src: dictionary with issue information

    Returns:
      Boolean indicator that issue enabled
    """
    return assessment_src.get(
        "issue_tracker", {}
    ).get(
        "enabled", False
    )

  def _is_issue_on_create_enabled(self, assessment_src):
    """Check that issue tracker on create enabled.

    Args:
      assessment_src: dictionary with issue information

    Returns:
      Boolean indicator that issue enabled
    """

    # Get enable flag from API request if available
    issue_tracker_info = assessment_src.get(
        "issue_tracker", {}
    )

    if issue_tracker_info:
      return issue_tracker_info.get("enabled", False)

    # Get enable flag from assessment template if available
    template_info = assessment_src.get("template", {})
    template_issue_info = self._get_issue_from_assmt_template(template_info)

    if template_issue_info:
      return template_issue_info.get("enabled", False)

    # enable flag was not found. Allow to use issue tracker,
    # as it is enabled in audit
    return True

  @staticmethod
  def _is_ccs_same(issue_payload, issue_tracker_info):
    # type: (Dict[str, Any], Dict[str, Any]) -> bool
    """Check that issue's CCS is the same in GGRC and Issue Tracker.

    Args:
        issue_payload (Dict[str, Any]): Issue information from GGRC.
        issue_tracker_info (Dict[str, Any]): Issue information from
          Issue Tracker.

    Returns:
        Boolean indicator for CCs validation
    """
    ccs_ggrc_payload = issue_payload.get("ccs") or []
    ccs_tracker_payload = issue_tracker_info.get("ccs") or []

    ccs_ggrc = set(cc.strip() for cc in ccs_ggrc_payload)
    ccs_tracker = set(cc.strip() for cc in ccs_tracker_payload)

    return ccs_ggrc.issubset(ccs_tracker)

  @staticmethod
  def _is_common_fields_same(issue_payload, issue_tracker_info):
    # type: (Dict[str, Any], Dict[str, Any]) -> bool
    """Check that issue's common fields are the same in GGRC and Issue Tracker.

    Args:
        issue_payload (Dict[str, Any]): Issue information from GGRC.
        issue_tracker_info (Dict[str, Any]): Issue information from
          Issue Tracker.

    Returns:
        Boolean indicator for common fields validation
    """
    return all(
        issue_payload.get(field) == issue_tracker_info.get(field)
        for field in constants.COMMON_SYNCHRONIZATION_FIELDS
    )

  @staticmethod
  def _update_issue_info(sync_result, issuetracker_info):
    """Update issue tracker info after request to Tracker.

    Args:
        sync_result: object from SyncResult model
        issuetracker_info: information for Issue tracker

    Returns:
        Updated dictionary with issue information
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      issuetracker_info["enabled"] = False
    else:
      issuetracker_info.update(
          {
              "issue_id": sync_result.issue_id,
              "issue_url": integration_utils.build_issue_tracker_url(
                  sync_result.issue_id
              )
          }
      )

  @staticmethod
  def _update_issue_info_on_detach(sync_result, issuetracker_info):
    """Update issue tracker info on detach request

    Args:
        sync_result: object from SyncResult model
        issuetracker_info: information for Issue tracker

    Returns:
        Updated dictionary with issue information
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      issue_url = integration_utils.build_issue_tracker_url(
          sync_result.issue_id
      )
      issuetracker_info.update({
          "issue_id": sync_result.issue_id,
          "issue_url": issue_url
      })

  @staticmethod
  def _ticket_warnings_for_create(sync_result, assessment):
    """Add warning for create ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      if isinstance(sync_result.error,
                    integrations_errors.HotlistPermissionError):
        assessment.add_warning(constants.WarningsDescription.
                               HOTLIST_PERMISSIONS_ERROR)
      else:
        assessment.add_warning(constants.WarningsDescription.CREATE_ASSESSMENT)
      logger.error(
          constants.ErrorsDescription.CREATE_ASSESSMENT,
          assessment.id,
          sync_result.error
      )

  @staticmethod
  def _ticket_warnings_for_link(sync_result, assessment):
    """Add warning for link ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      assessment.add_warning(constants.WarningsDescription.LINK_ASSESSMENT)
      logger.error(
          constants.ErrorsDescription.LINK_ASSESSMENT,
          assessment.id,
          sync_result.error
      )

  @staticmethod
  def _ticket_warnings_for_update(sync_result, assessment):
    """Add warning for update ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      assessment.add_warning(constants.WarningsDescription.UPDATE_ASSESSMENT)
      logger.error(
          constants.ErrorsDescription.UPDATE_ASSESSMENT,
          sync_result.issue_id,
          assessment.id,
          sync_result.error
      )

  @staticmethod
  def _ticket_warnings_for_sync(sync_result, assessment):
    """Add warning for synchronization ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      assessment.add_warning(constants.WarningsDescription.SYNC_ASSESSMENT)
      logger.error(
          constants.ErrorsDescription.SYNC_ASSESSMENT,
          sync_result.issue_id,
          assessment.id,
          sync_result.error
      )

  @staticmethod
  def _ticket_warnings_for_delete(sync_result, assessment):
    """Add warning for delete ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      logger.error(
          constants.ErrorsDescription.DELETE_ASSESSMENT,
          sync_result.issue_id,
          assessment.id,
          sync_result.error
      )

  @staticmethod
  def _ticket_warnings_for_detach(sync_result, assessment):
    """Add warning for detach ticket.

    Args:
        sync_result: object from SyncResult model
        assessment: object from Assessment model
    """
    if sync_result.status == SyncResult.SyncResultStatus.ERROR:
      assessment.add_warning(
          constants.WarningsDescription.DETACH_ASSESSMENT %
          sync_result.issue_id
      )
      logger.error(
          constants.ErrorsDescription.DETACH_ASSESSMENT,
          sync_result.issue_id,
          sync_result.error
      )

  @staticmethod
  def custom_fields_processing(issue_payload, issue_tracker_info):
    # type: (Dict[str, Any], Dict[str, Any]) -> bool
    """Check that issue's custom fields are the same in GGRC and Issue Tracker.

    Args:
        issue_payload (Dict[str, Any]): Issue information from GGRC.
        issue_tracker_info (Dict[str, Any]): Issue information from
          Issue Tracker.

    Returns:
        Boolean indicator for custom fields validation.
    """
    custom_fields_ggrc = issue_payload.get("custom_fields") or []
    custom_fields_tracker = issue_tracker_info.get("custom_fields") or []

    if any(custom_fields_ggrc) and custom_fields_ggrc[0]["value"]:
      due_date_payload = datetime.datetime.strptime(
          custom_fields_ggrc[0]["value"].strip(),
          "%Y-%m-%d"
      )
    else:
      due_date_payload = None

    if any(custom_fields_tracker):
      due_date_raw = sync_utils.parse_due_date(
          custom_fields_tracker
      )
      if due_date_raw is None:
        # due date is empty after processing,
        # in that case we shouldn't synchronize custom fields
        return True, True
      else:
        due_date_issuetracker = datetime.datetime.strptime(
            due_date_raw,
            "%Y-%m-%d"
        )
    else:
      # custom fields is empty from issue tracker,
      # in that case we shouldn't synchronize custom fields
      return True, True

    return due_date_payload == due_date_issuetracker, False

  def _get_issue(self, issue_id):
    """Get issue from Issue Tracker.

    Args:
        issue_id: issue id from issue tracker payload

    Returns:
        Issue Tracker information
        Exceptions
    """
    try:
      issue_tracker_info = self._client.get_issue(
          issue_id
      )
      return issue_tracker_info, None
    except integrations_errors.Error as error:
      return {}, error


class SyncResult(object):
  # pylint: disable=too-few-public-methods
  """Synchronization responses for Issue Tracker."""

  class SyncResultStatus(object):
    # pylint: disable=too-few-public-methods
    """Synchronization statuses for Issue Tracker."""
    SYNCED = "SYNCED"
    ERROR = "ERROR"

  def __init__(self, status, issue_id=None, error=None):
    self.status = status
    self.issue_id = issue_id
    self.error = error


def _hook_assmt_template_post(sender, objects=None, sources=None):
  """Handles create event to AssessmentTemplate model."""
  del sender

  tracker_handler = AssessmentTrackerHandler()
  for assessment_template, assmt_template_src in itertools.izip(objects,
                                                                sources):
    integration_utils.update_issue_tracker_for_import(assessment_template)
    issue_info = assmt_template_src.get('issue_tracker')
    if issue_info:
      # this part will be run for API calls only, as src is empty for imports
      tracker_handler.handle_assmt_template_create(
          assessment_template,
          issue_info
      )


def _hook_assmt_template_put(sender, obj=None, src=None,
                             service=None, initial_state=None):
  """Handles update event to AssessmentTemplate model."""
  del sender, service, initial_state

  issue_info = src.get('issue_tracker')
  if issue_info:
    tracker_handler = AssessmentTrackerHandler()
    tracker_handler.handle_assmt_template_update(
        obj,
        issue_info
    )


def _hook_assmt_template_delete(sender, obj=None,
                                service=None, event=None):
  """Handle deleting assessment template."""
  del sender, service, event

  tracker_handler = AssessmentTrackerHandler()
  tracker_handler.handle_template_delete(obj)


def _hook_audit_issue_post(sender, objects=None, sources=None):
  """Handle creating audit issue related info."""
  del sender

  tracker_handler = AssessmentTrackerHandler()
  for audit, audit_src in itertools.izip(objects, sources):
    integration_utils.update_issue_tracker_for_import(audit)
    issue_info = audit_src.get('issue_tracker')
    if issue_info:
      # this part will be run for API calls only, as src is empty for imports
      tracker_handler.handle_audit_create(audit, issue_info)


def _hook_audit_issue_put(sender, obj=None, src=None, service=None):
  """Handle updating audit issue related info."""
  del sender, service

  issue_info = src.get('issue_tracker')
  if issue_info:
    tracker_handler = AssessmentTrackerHandler()
    tracker_handler.handle_audit_update(obj, issue_info)


def _hook_audit_issue_delete(sender, obj=None, service=None, event=None):
  """Handle deleting audit issue related info."""
  del sender, service, event

  tracker_handler = AssessmentTrackerHandler()
  tracker_handler.handle_audit_delete(obj)


def _hook_assmt_issue_update(sender, obj=None, src=None,
                             initial_state=None, **kwargs):
  """Handle updating assessment issue related info."""
  del sender, kwargs

  tracker_handler = AssessmentTrackerHandler()
  tracker_handler.handle_assessment_update(
      obj,
      src,
      initial_state
  )


def _hook_audit_issues_update(sender, obj=None, **kwargs):
  """Handle updating related issues for audit"""
  del sender  # Unused

  tracker_handler = AssessmentTrackerHandler()
  tracker_handler.handle_audit_issues_update(
      obj,
      kwargs.get("initial_state")
  )


def _hook_assmt_delete(sender, obj=None, service=None):
  """Handle assessment delete event."""
  del sender, service

  tracker_handler = AssessmentTrackerHandler()
  tracker_handler.handle_assessment_delete(obj)


def init_hook():
  """Initializes hooks."""

  signals.Restful.collection_posted.connect(
      _hook_audit_issue_post,
      sender=all_models.Audit
  )
  signals.Restful.model_put.connect(
      _hook_audit_issue_put,
      sender=all_models.Audit
  )
  signals.Restful.model_put_after_commit.connect(
      _hook_audit_issues_update,
      sender=all_models.Audit
  )
  signals.Restful.model_deleted_after_commit.connect(
      _hook_audit_issue_delete,
      sender=all_models.Audit
  )
  signals.Restful.collection_posted.connect(
      _hook_assmt_template_post,
      sender=all_models.AssessmentTemplate
  )
  signals.Restful.model_put.connect(
      _hook_assmt_template_put,
      sender=all_models.AssessmentTemplate
  )
  signals.Restful.model_deleted_after_commit.connect(
      _hook_assmt_template_delete,
      sender=all_models.AssessmentTemplate
  )
  signals.Restful.model_put_before_commit.connect(
      _hook_assmt_issue_update,
      sender=all_models.Assessment
  )
  signals.Restful.model_deleted.connect(
      _hook_assmt_delete,
      sender=all_models.Assessment
  )
