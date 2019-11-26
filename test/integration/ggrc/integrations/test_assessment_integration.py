# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Integration tests for assessments with IssueTracker integration."""
# pylint: disable=too-many-lines
# this module will be refactored in the future when we make base testcase class
# for issuetracker integration.

from collections import OrderedDict

import mock
import ddt

import sqlalchemy as sa

from ggrc import db
from ggrc import models
from ggrc import settings
from ggrc.models import all_models
from ggrc.models.hooks.issue_tracker import assessment_integration
from ggrc.models.hooks.issue_tracker import integration_utils
from ggrc.models.hooks.issue_tracker import issue_tracker_params_builder \
    as params_builder
from ggrc.integrations import integrations_errors
from ggrc.integrations.synchronization_jobs import sync_utils
from ggrc.integrations.synchronization_jobs.assessment_sync_job import \
    ASSESSMENT_STATUSES_MAPPING
from ggrc.integrations import synchronization_jobs, constants
from ggrc.access_control.role import AccessControlRole

from integration.ggrc import api_helper
from integration.ggrc.models import factories
from integration.ggrc import generator
from integration.ggrc.access_control import acl_helper
from integration.ggrc.snapshotter import SnapshotterBaseTestCase

TICKET_ID = 123


# pylint: disable=too-many-public-methods
@ddt.ddt
class TestIssueTrackerIntegration(SnapshotterBaseTestCase):
  """Test set for IssueTracker integration functionality."""

  # pylint: disable=invalid-name

  def setUp(self):
    # pylint: disable=super-on-old-class
    # pylint seems to get confused, mro chain successfully resolves and returns
    # <type 'object'> as last entry.
    super(TestIssueTrackerIntegration, self).setUp()
    self.api = api_helper.Api()
    self.client.get('/login')

  DEFAULT_ASSESSMENT_ATTRS = {
      "title": "title1",
      "context": None,
      "status": "Draft",
      "enabled": True,
      "component_id": 1234,
      "hotlist_id": 4321,
      "issue_id": 654321,
      "issue_type": "PROCESS",
      "issue_priority": "P2",
      "issue_severity": "S1",
  }

  DEFAULT_TICKET_ATTRS = {
      "component_id": 1234,
      "hotlist_id": 4321,
      "issue_id": 654321,
      "status": "new",
      "issue_type": "Default Issue type",
      "issue_priority": "P1",
      "issue_severity": "S2",
      "title": "test title",
      "verifier": "user@example.com",
      "assignee": "user@example.com",
      "reporter": "user@example.com",
      "ccs": ["user@example.com"],
  }

  def request_payload_builder(self, issue_attrs, audit):
    """Build payload for update request to Issue Tracker"""
    payload_attrs = dict(self.DEFAULT_ASSESSMENT_ATTRS, **issue_attrs)
    payload = {"assessment": {
        "title": payload_attrs["title"],
        "context": None,
        "audit": {
            "id": audit.id,
            "type": audit.type,
        },
        "status": "Completed",
        "issue_tracker": {
            "enabled": payload_attrs["enabled"],
            "component_id": payload_attrs["component_id"],
            "hotlist_id": payload_attrs["hotlist_id"],
            "issue_id": payload_attrs["issue_id"],
            "issue_type": payload_attrs["issue_type"],
            "issue_priority": payload_attrs["issue_priority"],
            "issue_severity": payload_attrs["issue_severity"],
            "title": payload_attrs["title"],
        }
    }}
    return payload

  def response_payload_builder(self, ticket_attrs):
    """Build payload for response from Issue Tracker via get_issue method"""
    payload_attrs = dict(self.DEFAULT_TICKET_ATTRS, **ticket_attrs)
    payload = {"issueState": {
        "component_id": payload_attrs["component_id"],
        "hotlist_ids": [payload_attrs["hotlist_id"]],
        "issue_id": payload_attrs["issue_id"],
        "status": payload_attrs["status"],
        "type": payload_attrs["issue_type"],
        "priority": payload_attrs["issue_priority"],
        "severity": payload_attrs["issue_severity"],
        "title": payload_attrs["title"],
        "verifier": payload_attrs["verifier"],
        "assignee": payload_attrs["assignee"],
        "reporter": payload_attrs["reporter"],
        "ccs": payload_attrs["ccs"],
    }}
    return payload

  def post_request_payload_builder(self, issue_attrs):
    """Build payload for POST request to Issue Tracker."""
    payload_attrs = dict(self.DEFAULT_ASSESSMENT_ATTRS, **issue_attrs)
    payload = {
        "issue_tracker": {
            "enabled": payload_attrs["enabled"],
            "status": payload_attrs["status"],
            "component_id": payload_attrs["component_id"],
            "hotlist_id": payload_attrs["hotlist_id"],
            "issue_type": payload_attrs["issue_type"],
            "issue_priority": payload_attrs["issue_priority"],
            "issue_severity": payload_attrs["issue_severity"],
            "title": payload_attrs["title"],
        }
    }
    return payload

  def put_request_payload_builder(self, issue_attrs):
    """Build payload for PUT request to Issue Tracker"""
    payload_attrs = dict(self.DEFAULT_ASSESSMENT_ATTRS, **issue_attrs)
    payload = {
        "issue_tracker": {
            "enabled": payload_attrs["enabled"],
            "status": payload_attrs["status"],
            "component_id": payload_attrs["component_id"],
            "hotlist_id": payload_attrs["hotlist_id"],
            "issue_id": payload_attrs["issue_id"],
            "issue_type": payload_attrs["issue_type"],
            "issue_priority": payload_attrs["issue_priority"],
            "issue_severity": payload_attrs["issue_severity"],
            "title": payload_attrs["title"],
        }
    }
    return payload

  def check_issuetracker_issue_fields(self,
                                      issue_tracker_issue,
                                      assmt_attrs, ticket_attrs):
    """Checks issuetracker_issue were updated correctly.

    Make assertions to check if issue tracker fields were updated according
    our business logic.
    For Assessment model we should get all attributes from linked assessment.
    """
    self.assertTrue(issue_tracker_issue.enabled)
    # According to our business logic all attributes should be taken
    # from assessment attributes
    self.assertEqual(
        issue_tracker_issue.title,
        assmt_attrs["assessment"]["issue_tracker"]["title"]
    )
    self.assertEqual(
        int(issue_tracker_issue.component_id),
        ticket_attrs["issueState"]["component_id"]
    )
    self.assertEqual(
        int(issue_tracker_issue.hotlist_id),
        ticket_attrs["issueState"]["hotlist_ids"][0]
    )
    self.assertEqual(
        issue_tracker_issue.issue_priority,
        assmt_attrs["assessment"]["issue_tracker"]["issue_priority"]
    )
    self.assertEqual(
        issue_tracker_issue.issue_severity,
        assmt_attrs["assessment"]["issue_tracker"]["issue_severity"]
    )
    self.assertEqual(
        int(issue_tracker_issue.issue_id),
        assmt_attrs["assessment"]["issue_tracker"]["issue_id"]
    )
    self.assertEqual(
        issue_tracker_issue.issue_type,
        assmt_attrs["assessment"]["issue_tracker"]["issue_type"]
    )

  @ddt.data(
      ({"title": "first_title"}, {"title": "other_title"}),
      ({"issue_type": "type1"}, {"issue_type": "process"}),
      ({"issue_severity": "S0"}, {"issue_severity": "S1"}),
      ({"issue_priority": "P0"}, {"issue_priority": "P1"}),
      ({"hotlist_id": 1234}, {"hotlist_id": 4321}),
      ({"component_id": 1234}, {"component_id": 4321}),
      ({"status": "Draft"}, {"status": "fixed"}),
  )
  @ddt.unpack
  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_new_linked_assessment(self, assmt_attrs, ticket_attrs, upd_mock):
    """Test link new Assessment to IssueTracker ticket sets correct fields"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )

    assmt_request_payload = self.request_payload_builder(assmt_attrs, audit)
    response_payload = self.response_payload_builder(ticket_attrs)

    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      with mock.patch("ggrc.integrations.issues.Client.get_issue",
                      return_value=response_payload) as get_mock:
        response = self.api.post(all_models.Assessment, assmt_request_payload)

    get_mock.assert_called_once()
    upd_mock.assert_called_once()
    self.assertEqual(response.status_code, 201)
    assmt_id = response.json.get("assessment").get("id")
    it_issue = models.IssuetrackerIssue.get_issue("Assessment", assmt_id)
    expected_assmt_request_payload = assmt_request_payload.copy()
    # we do not update some values during manual linking
    expected_assmt_request_payload["assessment"]["issue_tracker"].update({
        "title": response_payload["issueState"]["title"],
        "issue_severity": response_payload["issueState"]["severity"],
        "issue_priority": response_payload["issueState"]["priority"],
    })
    self.check_issuetracker_issue_fields(
        it_issue,
        expected_assmt_request_payload,
        response_payload
    )

  @staticmethod
  def _create_issuetracked_obj(factory, acl, people_sync_enabled=True,
                               enabled=True, **issue_tracker_data):
    """Helper function creating issuetracked object with given parameters."""
    with factories.single_commit():
      obj = factory()
      factories.IssueTrackerIssueFactory(
          enabled=enabled,
          issue_tracked_obj=obj,
          people_sync_enabled=people_sync_enabled,
          **issue_tracker_data
      )
      for role_name, people in acl.iteritems():
        for email in people:
          person = factories.PersonFactory(email=email)
          obj.add_person_with_role_name(person, role_name)
    return obj

  @ddt.data(
      (
          True,
          {"Audit Captains": {"c@e.com", }},
          {"Assignees": {"1@e.com", "2@e.com"}},
          {
              "reporter": "r@e.com",
              "assignee": "",
              "verifier": "",
              "ccs": {"3@e.com", "4@e.com"},
          },
          {
              "reporter": "r@e.com",
              "assignee": "1@e.com",
              "verifier": "1@e.com",
              "ccs": {"c@e.com", "2@e.com", "3@e.com", "4@e.com"},
          },
      ),
      (
          False,
          {"Audit Captains": {"c@e.com", }},
          {"Assignees": {"1@e.com", "2@e.com"}},
          {
              "reporter": "r@e.com",
              "assignee": "",
              "verifier": "",
              "ccs": {"3@e.com", "4@e.com"},
          },
          {
              "reporter": "r@e.com",
              "assignee": "",
              "verifier": "",
              "ccs": {"3@e.com", "4@e.com"},
          },
      ),
  )
  @ddt.unpack
  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_people_sync_during_link(self, sync, audit_acl, asmt_acl,
                                   new_ticket_data, expected_ticket_data,
                                   update_mock):
    """Test people sync works correctly when linking."""
    # pylint: disable=too-many-arguments,too-many-locals
    audit = self._create_issuetracked_obj(
        factories.AuditFactory, audit_acl, people_sync_enabled=sync)
    asmt = self._create_issuetracked_obj(
        factories.AssessmentFactory, asmt_acl, issue_id=TICKET_ID)
    with factories.single_commit():
      asmt.audit = audit

    new_ticket_data.update({"issue_id": (TICKET_ID + 1)})
    issue_request_payload = self.put_request_payload_builder(new_ticket_data)
    response_payload = self.response_payload_builder(new_ticket_data)
    with mock.patch("ggrc.integrations.issues.Client.get_issue",
                    return_value=response_payload) as get_mock:
      response = self.api.put(asmt, issue_request_payload)

    get_mock.assert_called_once()
    self.assert200(response)
    for argument, expected_value in expected_ticket_data.iteritems():
      actual_value = update_mock.call_args_list[0][0][1][argument]
      if isinstance(actual_value, list):
        actual_value = set(actual_value)
      self.assertEqual(actual_value, expected_value)

  @staticmethod
  def _prepare_acl_json(obj_type, rolename_emails_map):
    """Prepate ACL json for specific object type."""
    result = []
    with factories.single_commit():
      for rolename, emails in rolename_emails_map.iteritems():
        for email in emails:
          person_id = factories.PersonFactory(email=email).id
          role_id = all_models.AccessControlRole.query.filter_by(
              name=rolename, object_type=obj_type).one().id
          result.append(acl_helper.get_acl_json(role_id, person_id))
    return result

  @ddt.data(
      (
          True,
          {"Audit Captains": {"r@e.com", }},
          {"Assignees": {"1@e.com", "2@e.com"}},
          {
              "reporter": "r@e.com",
              "assignee": "1@e.com",
              "verifier": "1@e.com",
              "ccs": {"2@e.com", },
          },
      ),
      (
          False,
          {"Audit Captains": {"r@e.com", }},
          {"Assignees": {"1@e.com", "2@e.com"}},
          {
              "reporter": "r@e.com",
              "assignee": "1@e.com",
              "verifier": "1@e.com",
              "ccs": {"2@e.com", },
          },
      ),
  )
  @ddt.unpack
  @mock.patch("ggrc.integrations.issues.Client.create_issue",
              return_value={"issueId": TICKET_ID})
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_new_assessment_people(self, sync, audit_acl, asmt_acl,
                                 expected_ticket_data, create_mock):
    """External Issue for new Assessment contains correct people."""
    # pylint: disable=too-many-arguments
    audit = self._create_issuetracked_obj(
        factories.AuditFactory, audit_acl, people_sync_enabled=sync)

    issue_tracker = self.post_request_payload_builder({})
    asmt_acl_json = self._prepare_acl_json("Assessment", asmt_acl)
    response = self.api.post(
        all_models.Assessment,
        {
            "assessment": {
                "title": "asmt",
                "audit": {"id": audit.id, "type": audit.type},
                "issue_tracker": issue_tracker["issue_tracker"],
                "access_control_list": asmt_acl_json,
            },
        },
    )

    self.assertStatus(response, 201)
    create_mock.assert_called_once()
    for argument, expected_value in expected_ticket_data.iteritems():
      actual_value = create_mock.call_args_list[0][0][0][argument]
      if isinstance(actual_value, list):
        actual_value = set(actual_value)
      self.assertEqual(actual_value, expected_value)

  def test_fill_missing_values_from_audit(self):
    """Check prepare_json_method get missed values from audit."""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit,
          component_id=213,
          hotlist_id=333,
          issue_type=constants.DEFAULT_ISSUETRACKER_VALUES['issue_type'],
          issue_priority="S0",
          issue_severity="P0",
      )
      assmt = factories.AssessmentFactory(
          audit=audit,
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=assmt
      )

    audit_issue_tracker_info = audit.issuetracker_issue.to_dict()
    assmt_issue_tracker_info = assmt.issuetracker_issue.to_dict()

    integration_utils.populate_issue_tracker_fields(assmt,
                                                    assmt_issue_tracker_info)

    fields_to_check = ["component_id", "hotlist_id", "issue_type",
                       "issue_priority", "issue_severity"]
    for field in fields_to_check:
      self.assertEqual(assmt_issue_tracker_info[field],
                       audit_issue_tracker_info[field])

  def test_prepare_update_json(self):
    """Test prepare_update_json method for Assessment."""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit,
          component_id=213,
          hotlist_id=333,
          issue_type=constants.DEFAULT_ISSUETRACKER_VALUES['issue_type'],
          issue_priority="P0",
          issue_severity="S0",
      )
      assmt = factories.AssessmentFactory(
          audit=audit,
          title="title",
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=assmt,
          title='',
      )
    tracker_handler = assessment_integration.AssessmentTrackerHandler()
    without_info = tracker_handler.prepare_issue_update_json(assmt)
    issue_info = assmt.issue_tracker
    with_info = tracker_handler.prepare_issue_update_json(
        assmt,
        issue_info
    )
    expected_info = {
        'component_id': 213,
        'severity': u'S0',
        'title': assmt.title,
        'hotlist_ids': [333, ],
        'priority': u'P0',
        'type': constants.DEFAULT_ISSUETRACKER_VALUES['issue_type'],
    }
    self.assertEqual(expected_info, with_info)
    self.assertEqual(without_info, with_info)

  def test_fill_missing_values_from_default(self):
    """Check prepare_json_method get missed values from default values."""
    with factories.single_commit():
      assmt = factories.AssessmentFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=assmt
      )

    assmt_issue_tracker_info = assmt.issuetracker_issue.to_dict()

    integration_utils.populate_issue_tracker_fields(assmt,
                                                    assmt_issue_tracker_info)

    fields_to_check = ["component_id", "hotlist_id", "issue_type",
                       "issue_priority", "issue_severity"]
    for field in fields_to_check:
      self.assertEqual(assmt_issue_tracker_info[field],
                       constants.DEFAULT_ISSUETRACKER_VALUES[field])

  def test_fill_missing_values_from_assmt(self):
    """Check prepare_json_method get missed values from default values."""
    with factories.single_commit():
      assmt = factories.AssessmentFactory(
          start_date="2015-10-09",
          status="Not Started",
          title="title",
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=assmt
      )

    assmt_issue_tracker_info = assmt.issuetracker_issue.to_dict()

    integration_utils.populate_issue_tracker_fields(assmt,
                                                    assmt_issue_tracker_info)

    self.assertEqual(assmt_issue_tracker_info["title"],
                     assmt.title)
    self.assertEqual(assmt_issue_tracker_info["due_date"],
                     assmt.start_date)
    self.assertEqual(assmt_issue_tracker_info["status"],
                     ASSESSMENT_STATUSES_MAPPING[assmt.status])

  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_existing_assmt_link(self, update_mock):
    """Test Assessment link to another ticket """
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_id=TICKET_ID,
          issue_tracked_obj=factories.AssessmentFactory(audit=audit)
      )

    new_ticket_id = TICKET_ID + 1
    new_data = {"issue_id": new_ticket_id}
    issue_request_payload = self.put_request_payload_builder(new_data)
    response_payload = self.response_payload_builder(new_data)
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      with mock.patch("ggrc.integrations.issues.Client.get_issue",
                      return_value=response_payload) as get_mock:
        response = self.api.put(iti.issue_tracked_obj, issue_request_payload)
    get_mock.assert_called_once()
    self.assert200(response)

    # check if data was changed in our DB
    issue_id = response.json.get("assessment").get("id")
    issue_tracker_issue = models.IssuetrackerIssue.get_issue(
        "Assessment",
        issue_id,
    )
    self.assertEqual(int(issue_tracker_issue.issue_id), new_ticket_id)

    # check detach comment was sent
    detach_comment_tmpl = params_builder.AssessmentParamsBuilder.DETACH_TMPL
    comment = detach_comment_tmpl.format(new_ticket_id=new_ticket_id)
    expected_args = (
        str(TICKET_ID),
        {"status": "OBSOLETE", "comment": comment}
    )
    self.assertEqual(expected_args, update_mock.call_args[0])

  # pylint: disable=protected-access
  @ddt.data("test comment",
            "  \n\ntest comment\n\n"
            "  \n\n  \n\n")
  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_adding_comment_to_assessment(self, desc, mocked_update_issue):
    """Test adding comment with blank lines in the end."""
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          component_id="11111",
          hotlist_id="222222",
          issue_type="PROCESS",
          issue_priority="P2",
          issue_severity="S2"
      )
      iti_title = iti.title
      iti_issue_id = iti.issue_id
      asmt = iti.issue_tracked_obj
      comment = factories.CommentFactory(description=desc)
      self.api.put(asmt, {
          "actions": {"add_related": [{"id": comment.id,
                                       "type": "Comment"}]},
      })

    tracker_handler = assessment_integration.AssessmentTrackerHandler()
    asmt = db.session.query(models.Assessment).get(asmt.id)
    builder_class = params_builder.BaseIssueTrackerParamsBuilder
    expected_comment = builder_class.COMMENT_TMPL.format(
        author=None,
        comment="test comment",
        model="Assessment",
        link=tracker_handler._get_assessment_page_url(asmt),
    )
    kwargs = {'status': 'ACCEPTED',
              'component_id': 11111,
              'severity': "S2",
              'title': iti_title,
              'hotlist_ids': [222222],
              'priority': "P2",
              'comment': expected_comment}
    mocked_update_issue.assert_called_once_with(iti_issue_id, kwargs)

  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_cc_list_during_link(self, update_mock):
    """Test assessment linking functionality sync appropriate cc list"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      reporter = factories.PersonFactory(email="r@e.com")
      audit.add_person_with_role_name(
          reporter,
          "Audit Captains",
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      assmt = factories.AssessmentFactory(audit=audit)
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_id=TICKET_ID,
          issue_tracked_obj=assmt,
      )
      user = factories.PersonFactory(email="1@e.com")
      factories.AccessControlPersonFactory(
          ac_list=assmt.acr_name_acl_map["Assignees"],
          person=user,
      )
      user = factories.PersonFactory(email="2@e.com")
      factories.AccessControlPersonFactory(
          ac_list=assmt.acr_name_acl_map["Assignees"],
          person=user,
      )
    new_ticket_id = TICKET_ID + 1
    new_data = {"issue_id": new_ticket_id}
    issue_request_payload = self.put_request_payload_builder(new_data)
    new_data["ccs"] = ["3@e.com", "4@e.com"]
    response_payload = self.response_payload_builder(new_data)
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      with mock.patch("ggrc.integrations.issues.Client.get_issue",
                      return_value=response_payload) as get_mock:
        response = self.api.put(iti.issue_tracked_obj, issue_request_payload)
    get_mock.assert_called_once()
    self.assertEqual(set(update_mock.call_args_list[0][0][1]['ccs']),
                     {"2@e.com", "3@e.com", "4@e.com", "r@e.com"})
    self.assert200(response)

  @mock.patch.object(
      assessment_integration.AssessmentTrackerHandler,
      '_is_tracker_enabled',
      return_value=True
  )
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_already_linked_ticket(self, enabled_mock):
    """Test Assessment w/o IT couldn't be linked to already linked ticket"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_id=TICKET_ID,
          issue_tracked_obj=factories.AssessmentFactory(audit=audit)
      )
      new_assmt = factories.AssessmentFactory()

    issue_data = {"issue_id": TICKET_ID}
    issue_request_payload = self.put_request_payload_builder(issue_data)
    response = self.api.put(new_assmt, issue_request_payload)
    self.assert200(response)
    self.assertTrue(response.json["assessment"]["issue_tracker"]["_warnings"])
    issue_id = response.json.get("assessment").get("id")
    issue_tracker_issue = models.IssuetrackerIssue.get_issue(
        "Assessment",
        issue_id,
    )
    self.assertFalse(issue_tracker_issue)
    enabled_mock.assert_called()

  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_creating_new_ticket_for_linked_issue(self, update_mock):
    """Test create new ticket for already linked assessment"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_id=TICKET_ID,
          issue_tracked_obj=factories.AssessmentFactory(audit=audit)
      )
    new_data = {"issue_id": ''}
    issue_request_payload = self.put_request_payload_builder(new_data)
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      with mock.patch("ggrc.integrations.issues.Client.create_issue",
                      return_value={"issueId": TICKET_ID + 1}) as create_mock:
        response = self.api.put(iti.issue_tracked_obj, issue_request_payload)
    self.assert200(response)

    # Detach comment should be sent to previous ticket
    update_mock.assert_called_once()
    self.assertEqual(TICKET_ID, int(update_mock.call_args[0][0]))
    create_mock.assert_called_once()

    # check if data was changed in our DB
    issue_id = response.json.get("assessment").get("id")
    issue_tracker_issue = models.IssuetrackerIssue.get_issue(
        "Assessment",
        issue_id,
    )
    self.assertNotEqual(int(issue_tracker_issue.issue_id), TICKET_ID)

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_link_to_existing_ticket(self):
    """Check linking to existing ticket"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      assess = factories.AssessmentFactory(audit=audit)

    issue_request_payload = self.put_request_payload_builder(
        {"issue_id": 9999,
         "component_id": None,
         "hotlist_id": None}
    )

    get_issue_resp = self.response_payload_builder(
        {"component_id": 77, "hotlist_id": 44}
    )

    get_issue_mock_cm = mock.patch(
        "ggrc.integrations.issues.Client.get_issue",
        return_value=get_issue_resp
    )
    update_issue_mock_cm = mock.patch(
        "ggrc.integrations.issues.Client.update_issue"
    )

    with get_issue_mock_cm, update_issue_mock_cm as update_issue_mock:
      response = self.api.put(assess, issue_request_payload)
      self.assert200(response)
      called_issue_id, call_payload = update_issue_mock.call_args[0]
      self.assertEquals(9999, called_issue_id)
      self.assertEquals(77, call_payload["component_id"])
      self.assertEquals(44, call_payload["hotlist_ids"][0])

      self.assertEquals(
          get_issue_resp["issueState"]["priority"],
          call_payload["priority"]
      )
      self.assertEquals(
          get_issue_resp["issueState"]["severity"],
          call_payload["severity"]
      )
      self.assertEquals(
          issue_request_payload["issue_tracker"]["issue_type"],
          call_payload["type"]
      )
      self.assertEquals("ASSIGNED", call_payload["status"])
      self.assertEquals(
          get_issue_resp["issueState"]["title"],
          call_payload["title"]
      )

  @ddt.data(
      ('Completed', 'VERIFIED'),
      ('In Progress', 'ASSIGNED'),
      ('Rework Needed', 'ASSIGNED'),
      ('Deprecated', 'OBSOLETE'),
      ('In Review', 'FIXED'),
  )
  @ddt.unpack
  @mock.patch('ggrc.integrations.issues.Client.create_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_complete_assessment_create_issue(self,
                                            assmt_status,
                                            expected_status,
                                            mock_create_issue):
    """Test the creation of ticket for assessment in {0} state."""
    audit = factories.AuditFactory()
    # self._create_assessment_via_api(audit, assmt_status)
    response = self.api.post(all_models.Assessment, {
        'assessment': {
            'title': 'Assessment1',
            'context': None,
            'audit': {
                'id': audit.id,
                'type': audit.type,
            },
            'status': assmt_status,
        }
    })
    self.assert201(response)
    asmt = all_models.Assessment.query.one()

    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      issue_params = {
          'enabled': True,
          'component_id': 123123,
          'hotlist_id': 123123,
          'issue_type': 'PROCESS',
          'issue_priority': 'P2',
          'issue_severity': 'S2',
          'title': 'Default Title'
      }
      self.api.put(asmt, {'issue_tracker': issue_params})
      mock_create_issue.assert_called_once()
      self.assertEqual(mock_create_issue.call_args[0][0]['status'],
                       expected_status)

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_update_issuetracker_info(self):
    """Test that Issue Tracker issues are updated by the utility."""
    cli_patch = mock.patch.object(sync_utils.issues, 'Client')
    hook_patch = mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    )
    with cli_patch, hook_patch:
      iti_issue_id = []
      for _ in xrange(2):
        iti = factories.IssueTrackerIssueFactory()
        iti_issue_id.append(iti.issue_id)
        asmt = iti.issue_tracked_obj
        asmt_id = asmt.id
        audit = asmt.audit
        self.api.modify_object(audit, {
            'issue_tracker': {
                'enabled': True,
                'component_id': '11111',
                'hotlist_id': '222222',
            },
        })
        asmt = db.session.query(models.Assessment).get(asmt_id)
        self.api.modify_object(asmt, {
            'issue_tracker': {
                'enabled': True,
                'component_id': '11111',
                'hotlist_id': '222222',
                'issue_type': 'PROCESS',
                'issue_priority': 'P2',
                'issue_severity': 'S2',
                'title': 'Default Title',
                'issue_id': iti.issue_id,
                'issue_url': integration_utils.build_issue_tracker_url(
                    iti.issue_id
                )
            },
        })
        asmt = db.session.query(models.Assessment).get(asmt_id)
        self.api.modify_object(asmt, {
            'issue_tracker': {
                'enabled': True,
                'component_id': '11111',
                'hotlist_id': '222222',
                'issue_priority': 'P4',
                'issue_type': 'PROCESS',
                'issue_severity': 'S3',
                'title': 'Default Title',
                'issue_id': iti.issue_id,
                'issue_url': integration_utils.build_issue_tracker_url(
                    iti.issue_id
                )
            },
        })
      self.api.delete(asmt)

    cli_mock = mock.MagicMock()
    cli_mock.update_issue.return_value = None
    cli_mock.search.return_value = {
        'issues': [
            {
                'issueId': iti_issue_id[0],
                'issueState': {
                    'status': 'FIXED', 'type': 'bug2',
                    'priority': 'P2', 'severity': 'S2',
                },
            },
        ],
        'next_page_token': None,
    }

    with mock.patch.object(sync_utils.issues, 'Client', return_value=cli_mock):
      synchronization_jobs.sync_assessment_attributes()
      cli_mock.update_issue.assert_called_once_with(
          iti_issue_id[0], {
              'status': 'ASSIGNED',
              'priority': u'P4',
              'type': constants.DEFAULT_ISSUETRACKER_VALUES['issue_type'],
              'severity': u'S3',
              'assignee': '',
              'verifier': '',
              'reporter': '',
              'ccs': [],
              'component_id': 11111
          })

  # pylint: disable=unused-argument
  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_update_issuetracker_assignee(self, mocked_update_issue):
    """Assigneee shouldn't be update, only on synchronization"""
    email1 = "email1@example.com"
    email2 = "email2@example.com"
    assignee_role_id = AccessControlRole.query.filter_by(
        object_type="Assessment",
        name="Assignees"
    ).first().id
    assignees = [factories.PersonFactory(email=email2),
                 factories.PersonFactory(email=email1)]
    iti_issue_id = []
    iti = factories.IssueTrackerIssueFactory(
        enabled=True,
        component_id=12345,
        issue_type="PROCESS",
        issue_severity="S4",
        issue_priority="P4"
    )
    iti_issue_id.append(iti.issue_id)
    asmt = iti.issue_tracked_obj
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      acl = [acl_helper.get_acl_json(assignee_role_id, assignee.id)
             for assignee in assignees]
      self.api.put(asmt, {
          "access_control_list": acl
      })
      kwargs = {'status': 'ASSIGNED',
                'component_id': 12345,
                'severity': "S4",
                'title': iti.title,
                'hotlist_ids': [],
                'priority': "P4"}
      mocked_update_issue.assert_called_once_with(iti_issue_id[0], kwargs)

  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_update_issuetracker_title(self, mocked_update_issue):
    """Test title sync in case it has been updated."""
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      iti_issue_id = []
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          component_id='123123',
          issue_severity='S2',
          issue_priority='P2'
      )
      iti_issue_id.append(iti.issue_id)
      asmt = iti.issue_tracked_obj
      new_title = "New Title"
      self.api.put(
          asmt,
          {
              "issue_tracker": {
                  "component_id": iti.component_id,
                  "enabled": True,
                  "issue_priority": iti.issue_priority,
                  "issue_severity": iti.issue_severity,
                  "issue_type": constants.DEFAULT_ISSUETRACKER_VALUES[
                      "issue_type"
                  ],
                  "title": new_title,
                  "issue_id": iti.issue_id,
                  "issue_url": integration_utils.build_issue_tracker_url(
                      iti.issue_id
                  )
              }
          })
      kwargs = {'status': 'ASSIGNED',
                'component_id': int(iti.component_id),
                'severity': iti.issue_severity,
                'title': new_title,
                'hotlist_ids': [],
                'priority': iti.issue_priority}
      mocked_update_issue.assert_called_once_with(iti_issue_id[0], kwargs)

      issue = db.session.query(models.IssuetrackerIssue).get(iti.id)
      self.assertEqual(issue.title, new_title)

  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_update_issuetracker_due_date(self, mocked_update_issue):
    """Test title sync in case it has been updated."""
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      iti_issue_id = []
      iti = factories.IssueTrackerIssueFactory(
          enabled=True,
          component_id="123123",
          issue_type="PROCESS",
          issue_priority="P2",
          issue_severity="S2"
      )
      iti_issue_id.append(iti.issue_id)
      asmt = iti.issue_tracked_obj
      new_due_date = '2018-09-25'
      custom_fields = [{
          'name': 'Due Date',
          'value': new_due_date,
          'type': 'DATE',
          'display_string': 'Due Date',
      }]
      self.api.put(asmt, {
          'start_date': new_due_date,
          'title': 'title'
      })
      kwargs = {'status': 'ASSIGNED',
                'component_id': 123123,
                'severity': "S2",
                'title': iti.title,
                'hotlist_ids': [],
                'priority': "P2",
                'custom_fields': custom_fields}
      mocked_update_issue.assert_called_once_with(iti_issue_id[0], kwargs)

      issue = db.session.query(models.IssuetrackerIssue).get(iti.id)
      self.assertEqual(issue.due_date.strftime("%Y-%m-%d"), new_due_date)

  # pylint: disable=protected-access
  # pylint: disable=too-many-locals
  @ddt.data(
      ('Not Started', {
          'status': 'ASSIGNED'
      }),
      ('In Progress', {
          'status': 'ACCEPTED'
      }),
      ('In Review', {
          'status': 'FIXED'
      }),
      ('Rework Needed', {
          'status': 'ACCEPTED'
      }),
      ('Completed', {
          'status': 'VERIFIED',
          'comment': constants.STATUS_CHANGE_TMPL
      }),
      ('Deprecated', {
          'status': 'OBSOLETE',
          'comment': constants.STATUS_CHANGE_TMPL
      }),
  )
  @ddt.unpack
  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_change_assessment_status(self, status,
                                    additional_kwargs,
                                    mocked_update_issue):
    """Issue status should be changed for assessment with {status} status."""
    iti_issue_id = []
    iti = factories.IssueTrackerIssueFactory(
        enabled=True,
        component_id="123123",
        issue_type="PROCESS",
        issue_priority="P2",
        issue_severity="S2"
    )
    iti_issue_id.append(iti.issue_id)
    asmt = iti.issue_tracked_obj
    tracker_handler = assessment_integration.AssessmentTrackerHandler()
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      self.api.put(
          asmt,
          {
              "status": status,
          },
      )
      kwargs = {'component_id': 123123,
                'severity': "S2",
                'title': iti.title,
                'hotlist_ids': [],
                'priority': "P2"}
      asmt_link = tracker_handler._get_assessment_page_url(asmt)
      if 'comment' in additional_kwargs:
        try:
          additional_kwargs['comment'] = \
              additional_kwargs['comment'] % (status, asmt_link)
        except TypeError:
          pass
      kwargs.update(additional_kwargs)
      mocked_update_issue.assert_called_once_with(iti_issue_id[0], kwargs)

      issue = db.session.query(models.IssuetrackerIssue).get(iti.id)
      self.assertEqual(issue.cc_list, "")

  @staticmethod
  def _prepare_acl(configurations):
    """Prepare ACL payload.
    Args:
      - configurations: Dictionary with key - ACR id,
      and value - list of persons regarding ACR id
    Returns:
      -
    """
    acl_data = [
        {
            "ac_role_id": role_id,
            "person": {"id": person.id, "type": "Person"}
        } for role_id, persons in configurations.items()
        for person in persons
    ]
    return acl_data

  @mock.patch('ggrc.integrations.issues.Client.create_issue')
  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_basic_import(self, mock_create_issue, mock_update_issue):
    """Test basic import functionality."""
    # update existing object
    iti = factories.IssueTrackerIssueFactory(enabled=True)
    asmt = iti.issue_tracked_obj
    audit = asmt.audit
    response = self.import_data(OrderedDict([
        ('object_type', 'Assessment'),
        ('Code*', asmt.slug),
        ('Audit', audit.slug),
    ]))
    self._check_csv_response(response, {})

    # import new object
    response = self.import_data(OrderedDict([
        ('object_type', 'Assessment'),
        ('Code*', ''),
        ('Audit', audit.slug),
        ('Creators', 'user@example.com'),
        ('Assignees*', 'user@example.com'),
        ('Title', 'Some Title'),
    ]))
    self._check_csv_response(response, {})

  @mock.patch('ggrc.integrations.issues.Client.create_issue')
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_audit_delete(self, mock_create_issue):
    """Test deletion of an audit."""
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=audit,
          component_id="some id",
          hotlist_id="some host id",
      )
      result = self.api.delete(audit)
      self.assert200(result)

      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=audit,
          component_id="some id",
          hotlist_id="some host id",
      )
      result = self.api.delete(audit)
      self.assert200(result)

  @mock.patch("ggrc.integrations.issues.Client.update_issue")
  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  def test_update_ccs_many_audit_captains(self, mock_update_issue):
    """CCS shouldn't be calculated on update, only synchronization"""
    del mock_update_issue  # Unused
    with mock.patch.object(
        assessment_integration.AssessmentTrackerHandler,
        '_is_tracker_enabled',
        return_value=True
    ):
      audit = factories.AuditFactory()
      audit_captains = factories.PersonFactory.create_batch(2)
      audit_captain_role = all_models.AccessControlRole.query.filter_by(
          name="Audit Captains",
          object_type="Audit"
      ).one()
      self.api.put(
          audit,
          {
              "access_control_list": [
                  {
                      "ac_role_id": audit_captain_role.id,
                      "person": {
                          "id": audit_captain.id,
                          "type": "Person"
                      }
                  } for audit_captain in audit_captains]
          }
      )
      assessment = factories.AssessmentFactory(
          audit=audit
      )
      assessment_issue = factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=assessment,
          component_id="11111",
          hotlist_id="222222",
      )
      assessment_persons = factories.PersonFactory.create_batch(3)
      assignee_role = all_models.AccessControlRole.query.filter_by(
          name="Assignees",
          object_type="Assessment"
      ).one()
      creator_role = all_models.AccessControlRole.query.filter_by(
          name="Creators",
          object_type="Assessment"
      ).one()
      response_assessment = self.api.put(
          assessment,
          {
              "issue_tracker": {
                  "component_id": "11111",
                  "enabled": True,
                  "hotlist_id": "222222",
                  "issue_priority": "P2",
                  "issue_severity": "S2",
                  "title": "Assessment Title",
                  "issue_type": "PROCESS",
                  "issue_id": assessment_issue.issue_id,
                  "issue_url": integration_utils.build_issue_tracker_url(
                      assessment_issue.issue_id
                  )
              },
              "access_control_list": [
                  {
                      "ac_role_id": creator_role.id
                      if not index else assignee_role.id,
                      "id": index + 1,
                      "person": {
                          "context_id": None,
                          "href": "/api/people/{}".format(
                              person.id
                          ),
                          "id": person.id,
                          "type": "Person"
                      },
                      "person_email": person.email,
                      "person_id": person.id,
                      "person_name": person.name,
                      "type": "AccessControlList"
                  }
                  for index, person in enumerate(assessment_persons)]
          }
      )
      self.assert200(response_assessment)
      issue_tracker_assessment = all_models.IssuetrackerIssue.query.filter_by(
          object_id=assessment.id,
          object_type=assessment.type
      ).one()
      issue_tracker_cc = issue_tracker_assessment.cc_list.split(',')[0]
      self.assertIn(issue_tracker_cc, "")

  @mock.patch.object(
      assessment_integration.AssessmentTrackerHandler,
      '_is_tracker_enabled',
      return_value=True
  )
  @ddt.data((True, 123),)
  @ddt.unpack
  def test_issue_tracker_error(self, issue_tracker_enabled, issue_id,
                               issue_enabled):
    """Test that issue tracker does not change state
    in case receiving an error."""
    del issue_enabled  # Unused
    with mock.patch.object(
        sync_utils,
        'update_issue'
    ) as update_issue_mock:
      error_data = integrations_errors.Error('data')
      update_issue_mock.side_effect = error_data
      issue = factories.IssueTrackerIssueFactory(
          enabled=issue_tracker_enabled,
          issue_id=issue_id
      )
      src = {
          'issue_tracker': {
              'enabled': issue_tracker_enabled,
              'issue_id': issue_id,
              'issue_url': integration_utils.build_issue_tracker_url(
                  issue_id
              ),
              'component_id': '1111',
              'issue_type': 'PROCESS',
              'issue_priority': 'P2',
              'issue_severity': 'S2',
              'title': 'Title'
          }
      }
      assessment_integration._hook_assmt_issue_update(
          sender=None,
          obj=issue.issue_tracked_obj,
          src=src,
          initial_state=issue.issue_tracked_obj
      )
      update_issue_mock.assert_called_once()
      issue_obj = models.IssuetrackerIssue.get_issue(
          "Assessment",
          issue.issue_tracked_obj.id
      )
      self.assertEqual(issue_obj.enabled, issue_tracker_enabled)

  def test_values_updated_from_audit(self):
    """Check assessment values updated with latest audit values"""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=audit,
          component_id=self.DEFAULT_ASSESSMENT_ATTRS["component_id"],
          hotlist_id=self.DEFAULT_ASSESSMENT_ATTRS["hotlist_id"],
          issue_priority=self.DEFAULT_ASSESSMENT_ATTRS["issue_priority"],
          issue_severity=self.DEFAULT_ASSESSMENT_ATTRS["issue_severity"],
          issue_type=self.DEFAULT_ASSESSMENT_ATTRS["issue_type"],
      )
      asmt = factories.AssessmentFactory(audit=audit)
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=asmt,
          component_id=4321,
          hotlist_id=1234,
          issue_priority="P2",
          issue_severity="S1",
      )
    audit_issue_tracker_info = audit.issuetracker_issue.to_dict()
    assmt_issue_tracker_info = asmt.issuetracker_issue.to_dict()

    integration_utils.populate_issue_tracker_fields(asmt,
                                                    assmt_issue_tracker_info,
                                                    with_update=True)

    fields_to_check = ["component_id", "hotlist_id", "issue_type",
                       "issue_priority", "issue_severity"]
    for field in fields_to_check:
      self.assertEqual(assmt_issue_tracker_info[field],
                       audit_issue_tracker_info[field])

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  @mock.patch('ggrc.integrations.issues.Client.update_issue')
  def test_sync_child_assessments(self, upd_mock):
    """
      Test send comment to child Assessments when disable issue tracker
      for parent Audit
    """
    with factories.single_commit():
      audit = factories.AuditFactory()
      audit_issue = factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )
      assessment = factories.AssessmentFactory(audit=audit)
      assessment_issue = factories.IssueTrackerIssueFactory(
          enabled=True, issue_tracked_obj=assessment
      )
    issue_id = assessment_issue.issue_id
    self.api.put(audit_issue.issue_tracked_obj,
                 {"issue_tracker": {"enabled": False,
                                    "title": audit_issue.title,
                                    "issue_id": audit_issue.issue_id}})
    expected_comment = {"comment": "Changes to this GGRC Assessment will no "
                                   "longer be tracked within this bug."}
    upd_mock.assert_called_with(issue_id, expected_comment)

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  @mock.patch("ggrc.integrations.issues.Client.create_issue",
              side_effect=[integrations_errors.HotlistPermissionError()])
  def test_no_permissions_hotlist(self, _):
    """Test warning message if no permissions to Hotlist."""
    with factories.single_commit():
      audit = factories.AuditFactory()
      factories.IssueTrackerIssueFactory(
          enabled=True,
          issue_tracked_obj=audit
      )

    issue_data = {"issue_id": None}
    issue_tracker_attrs = self.post_request_payload_builder(issue_data)

    response = self.api.post(all_models.Assessment, {
        "assessment": {
            "title": "asmt",
            "audit": {"id": audit.id, "type": audit.type},
            "issue_tracker": issue_tracker_attrs["issue_tracker"],
        },
    })

    self.assertIn(constants.WarningsDescription.HOTLIST_PERMISSIONS_ERROR,
                  response.json.get(
                      "assessment", {}).get("issue_tracker",
                                            {}).get("_warnings", []))

  @staticmethod
  def _build_revision_query(resource):
    """Build query for revisions of provided resource."""
    return all_models.Revision.query.filter(
        all_models.Revision.resource_type == resource.__class__.__name__,
        all_models.Revision.resource_id == resource.id,
    ).order_by(
        all_models.Revision.id.desc(),
    )

  def _map(self, source, destination):
    """Map `source` object to `destination` one."""
    return self.api.post(
        all_models.Relationship,
        {
            "relationship": {
                "source": {
                    "type": source.type,
                    "id": source.id,
                },
                "destination": {
                    "type": destination.type,
                    "id": destination.id,
                },
                "context": None
            },
        },
    )

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  @mock.patch("ggrc.integrations.issues.Client.update_issue",
              return_value=mock.MagicMock())
  def test_snapshot_map_test_plan(self, update_issue_mock):
    """Test comment with updated test plan is added when mapping snapshot."""
    asmt_issue_id, control_test_plan = 1, factories.random_str().strip()
    comment = constants.ASMT_TEST_PLAN_UPD_TMPL.format(control_test_plan)

    with factories.single_commit():
      control = factories.ControlFactory(
          test_plan=control_test_plan,
      )
      asmt = factories.AssessmentFactory(
          assessment_type=control.type,
          test_plan_procedure=True,
      )
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=asmt.audit,
          enabled=True,
      )
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=asmt,
          issue_id=asmt_issue_id,
      )

    rev_c = self._build_revision_query(control).first()
    with factories.single_commit():
      snapshot = factories.SnapshotFactory(
          parent=asmt.audit,
          child_type=control.__class__.__name__,
          child_id=control.id,
          revision_id=rev_c.id,
      )

    response = self._map(snapshot, asmt)
    self.assert201(response)

    update_issue_mock.assert_called_once_with(
        str(asmt_issue_id),
        {
            "comment": comment,
        },
    )

  @mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
  @mock.patch("ggrc.integrations.issues.Client.update_issue",
              return_value=mock.MagicMock())
  def test_snapshot_map_no_test_plan(self, update_issue_mock):
    """Test no attempt to add comment if not linked to Issue Tracker issue."""
    asmt_issue_id, control_test_plan = None, factories.random_str().strip()

    with factories.single_commit():
      control = factories.ControlFactory(
          test_plan=control_test_plan,
      )
      asmt = factories.AssessmentFactory(
          assessment_type=control.type,
          test_plan_procedure=True,
      )
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=asmt.audit,
          enabled=True,
      )
      factories.IssueTrackerIssueFactory(
          issue_tracked_obj=asmt,
          issue_id=asmt_issue_id,
      )

    rev_c = self._build_revision_query(control).first()
    with factories.single_commit():
      snapshot = factories.SnapshotFactory(
          parent=asmt.audit,
          child_type=control.__class__.__name__,
          child_id=control.id,
          revision_id=rev_c.id,
      )

    response = self._map(snapshot, asmt)
    self.assert201(response)

    update_issue_mock.assert_not_called()


@mock.patch('ggrc.models.hooks.issue_tracker.'
            'assessment_integration.AssessmentTrackerHandler.'
            '_is_tracker_enabled',
            return_value=True)
@mock.patch.object(settings, "ISSUE_TRACKER_ENABLED", True)
@mock.patch('ggrc.integrations.issues.Client')
class TestIssueTrackerIntegrationPeople(SnapshotterBaseTestCase):
  """Test people used in IssueTracker Issues."""

  EMAILS = {
      ("Audit", "Audit Captains"): {
          "audit_captain_1@example.com",
          "audit_captain_2@example.com"
      },
      ("Audit", "Auditors"): {
          "auditor_1@example.com",
          "auditor_2@example.com"
      },
      ("Assessment", "Creators"): {
          "creator_1@example.com",
          "creator_2@example.com"
      },
      ("Assessment", "Assignees"): {
          "assignee_1@example.com",
          "assignee_2@example.com"
      },
      ("Assessment", "Verifiers"): {
          "verifier_1@example.com",
          "verifier_2@example.com"
      },
      ("Assessment", "Custom Role"): {
          "curom_role_1@example.com"
      },
      ("Assessment", "Primary Contacts"): {
          "primary_contact_1@example.com",
          "primary_contact_2@example.com"
      },
      ("Assessment", "Secondary Contacts"): {
          "secondary_contact_1@example.com",
          "secondary_contact_2@example.com"
      },
  }

  ROLE_NAMES = (
      'Creators',
      'Assignees',
      'Verifiers',
      'Primary Contacts',
      'Secondary Contacts',
      'Custom Role'
  )

  def setUp(self):
    super(TestIssueTrackerIntegrationPeople, self).setUp()
    self.generator = generator.ObjectGenerator()

    factories.AccessControlRoleFactory(
        name='Custom Role',
        internal=False,
        object_type='Assessment',
    )

    # fetch all roles mentioned in self.EMAILS
    self.roles = {
        role.name: role
        for role in all_models.AccessControlRole.query.filter(
            sa.tuple_(
                all_models.AccessControlRole.object_type,
                all_models.AccessControlRole.name,
            ).in_(
                self.EMAILS.keys(),
            ),
        )
    }

    with factories.single_commit():
      self.audit = factories.AuditFactory()

      self.people = {
          role_name: [factories.PersonFactory(email=email)
                      for email in emails]
          for (_, role_name), emails in self.EMAILS.iteritems()
      }

  def setup_audit_people(self, role_name_to_people):
    """Assign roles to people provided."""
    with factories.single_commit():
      for role_name, people in role_name_to_people.iteritems():
        for person in people:
          self.audit.add_person_with_role_name(person, role_name)

  def create_asmt_with_issue_tracker(self, role_name_to_people,
                                     issue_tracker=None):
    """Create Assessment with issue_tracker parameters and ACL."""
    access_control_list = acl_helper.get_acl_list({
        person.id: self.roles[role_name].id
        for role_name, people in role_name_to_people.iteritems()
        for person in people
    })
    issue_tracker_with_defaults = {
        'enabled': True,
        'component_id': 123123,
        'hotlist_id': 123123,
        'issue_type': 'PROCESS',
        'issue_priority': 'P2',
        'issue_severity': 'S2',
        'title': 'Default Title'
    }
    issue_tracker_with_defaults.update(issue_tracker or {})

    _, asmt = self.generator.generate_object(
        all_models.Assessment,
        data={
            'audit': {'id': self.audit.id, 'type': self.audit.type},
            'issue_tracker': issue_tracker_with_defaults,
            'access_control_list': access_control_list,
        }
    )

    return asmt

  def test_missing_audit_captains(self, client_mock, _):
    """Reporter email is None is no Audit Captains present."""
    client_instance = client_mock.return_value
    client_instance.create_issue.return_value = {'issueId': 42}

    self.setup_audit_people({
        'Audit Captains': [],
        'Auditors': self.people['Auditors'],
    })

    self.create_asmt_with_issue_tracker(
        role_name_to_people={
            role_name: people for role_name, people in self.people.items()
            if role_name in ('Creators', 'Assignees', 'Verifiers')
        },
    )

    client_instance.create_issue.assert_called_once()
    self.assertIs(client_instance.create_issue.call_args[0][0]['reporter'], '')
