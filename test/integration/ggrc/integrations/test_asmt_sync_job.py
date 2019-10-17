# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Integration test for Assessment object sync cron job."""
from datetime import datetime

import ddt
import mock

from ggrc import settings
from ggrc.models import all_models
from ggrc.integrations.synchronization_jobs import assessment_sync_job
from ggrc.integrations.synchronization_jobs import sync_utils
from ggrc.integrations import constants
from integration import ggrc
from integration.ggrc.models import factories


@ddt.ddt
@mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
@mock.patch('ggrc.integrations.issues.Client.update_issue',
            return_value=mock.MagicMock())
class TestAsmtSyncJob(ggrc.TestCase):
  """Test cron job for sync Assessment object attributes."""

  @staticmethod
  def _create_asmt(people_sync_enabled):
    """Helper function creating assessment and audit."""
    with factories.single_commit():
      asmt = factories.AssessmentFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=asmt.audit,
          people_sync_enabled=people_sync_enabled,
          **TestAsmtSyncJob._issuetracker_data()
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=asmt,
          due_date=datetime.utcnow(),
          **TestAsmtSyncJob._issuetracker_data()
      )
      return asmt

  @staticmethod
  def _issuetracker_data():
    """Helper function returning default issue tracker settings."""
    return dict(
        component_id=constants.DEFAULT_ISSUETRACKER_VALUES["component_id"],
        hotlist_id=constants.DEFAULT_ISSUETRACKER_VALUES["hotlist_id"],
        issue_type=constants.DEFAULT_ISSUETRACKER_VALUES["issue_type"],
        issue_priority=constants.DEFAULT_ISSUETRACKER_VALUES["issue_priority"],
        issue_severity=constants.DEFAULT_ISSUETRACKER_VALUES["issue_severity"],
    )

  @staticmethod
  def _to_issuetrakcer_repr(asmt):
    """Return issue tracker representation of assessment."""
    return {
        asmt.issuetracker_issue.issue_id: dict(
            component_id=int(asmt.issuetracker_issue.component_id),
            status=asmt.status,
            type=asmt.issuetracker_issue.issue_type,
            priority=asmt.issuetracker_issue.issue_priority,
            severity=asmt.issuetracker_issue.issue_severity,
            reporter=asmt.issuetracker_issue.reporter or "",
            assignee=asmt.issuetracker_issue.assignee or "",
            verifier=asmt.issuetracker_issue.assignee or "",
            ccs=asmt.issuetracker_issue.cc_list or [],
        ),
    }

  @staticmethod
  def _construct_expected_upd_call(current_repr, new_audit_captains=(),
                                   new_asmt_assignees=(),
                                   people_sync_enabled=False):
    """Return expected args for client update_issue call."""
    issue_id, = current_repr.keys()
    body = dict(current_repr[issue_id])
    new_audit_captains = {a.email for a in new_audit_captains}
    new_asmt_assignees = {a.email for a in new_asmt_assignees}
    if people_sync_enabled:

      if new_audit_captains:
        body["reporter"] = min(new_audit_captains)

      if new_asmt_assignees:
        body["assignee"] = min(new_asmt_assignees)
        body["verifier"] = body["assignee"]

      body["ccs"] = list(
          (new_audit_captains | new_asmt_assignees) -
          {body["reporter"], body["assignee"]}
      )

    body["status"] = constants.STATUSES_MAPPING.get(body["status"])
    return str(issue_id), body

  @ddt.data(True, False)
  def test_assignee_people_sync(self, people_sync_enabled, update_issue_mock):
    """Test sync of Assignees when people_sync_enabled is on/off."""
    asmt = self._create_asmt(people_sync_enabled=people_sync_enabled)
    issuetracker_repr = self._to_issuetrakcer_repr(asmt)
    with factories.single_commit():
      assignee_1 = factories.PersonFactory()
      assignee_2 = factories.PersonFactory()
    expected_upd_args = self._construct_expected_upd_call(
        current_repr=issuetracker_repr,
        new_asmt_assignees=(assignee_1, assignee_2),
        people_sync_enabled=people_sync_enabled,
    )

    asmt.add_person_with_role_name(assignee_1, "Assignees")
    asmt.add_person_with_role_name(assignee_2, "Assignees")
    with mock.patch.object(sync_utils, "iter_issue_batches",
                           return_value=[issuetracker_repr]):
      assessment_sync_job.sync_assessment_attributes()

    update_issue_mock.assert_called_once_with(*expected_upd_args)

  @ddt.data(True, False)
  def test_captains_people_sync_on(self, people_sync_enabled,
                                   update_issue_mock):
    """Test sync of Audit Captain when people_sync_enabled is on/off."""
    asmt = self._create_asmt(people_sync_enabled=people_sync_enabled)
    issuetracker_repr = self._to_issuetrakcer_repr(asmt)
    with factories.single_commit():
      audit_captain_1 = factories.PersonFactory()
      audit_captain_2 = factories.PersonFactory()
    expected_upd_args = self._construct_expected_upd_call(
        current_repr=issuetracker_repr,
        new_audit_captains=(audit_captain_1, audit_captain_2),
        people_sync_enabled=people_sync_enabled,
    )

    asmt.audit.add_person_with_role_name(audit_captain_1, "Audit Captains")
    asmt.audit.add_person_with_role_name(audit_captain_2, "Audit Captains")
    with mock.patch.object(sync_utils, "iter_issue_batches",
                           return_value=[issuetracker_repr]):
      assessment_sync_job.sync_assessment_attributes()

    update_issue_mock.assert_called_once_with(*expected_upd_args)

  # pylint: disable=unused-argument
  def test_empty_due_date_sync(self, update_issue_mock):
    """Test adding empty due_date in Issue"""
    due_date = None
    assmt = self._create_asmt(True)
    assmt.start_date = due_date
    issue = assmt.issuetracker_issue
    iti = self._to_issuetrakcer_repr(assmt)
    iti[assmt.issuetracker_issue.issue_id].update({
        "custom_fields": [{
            constants.CustomFields.DUE_DATE:
                issue.due_date.strftime("%Y-%m-%d")
        }],
    })
    batches = [iti]

    with mock.patch.object(sync_utils, "iter_issue_batches",
                           return_value=batches):
      assessment_sync_job.sync_assessment_attributes()

    issue = all_models.IssuetrackerIssue.query.get(
        issue.id)
    self.assertEquals(issue.due_date, due_date)
