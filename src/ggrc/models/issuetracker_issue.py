# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Module for IssueTracker object."""

# pylint: disable=too-many-instance-attributes

from ggrc import db
from ggrc.integrations import constants
from ggrc.models.mixins import base
from ggrc.models.mixins import Base
from ggrc.models import utils


class IssuetrackerIssue(base.ContextRBAC, Base, db.Model):
  """Class representing IssuetrackerIssue."""

  __tablename__ = 'issuetracker_issues'

  object_id = db.Column(db.Integer, nullable=False)
  object_type = db.Column(db.String(250), nullable=False)
  enabled = db.Column(db.Boolean, nullable=False, default=False)

  title = db.Column(db.String(250), nullable=True)
  component_id = db.Column(db.String(50), nullable=True)
  hotlist_id = db.Column(db.String(50), nullable=True)
  issue_type = db.Column(
      db.String(50),
      nullable=True,
      default=constants.DEFAULT_ISSUETRACKER_VALUES['issue_type']
  )
  issue_priority = db.Column(db.String(50), nullable=True)
  issue_severity = db.Column(db.String(50), nullable=True)
  assignee = db.Column(db.String(250), nullable=True)
  reporter = db.Column(db.String(250), nullable=True)
  cc_list = db.Column(db.Text, nullable=False, default="")
  due_date = db.Column(db.Date, nullable=True)

  issue_id = db.Column(db.String(50), nullable=True)
  issue_url = db.Column(db.String(250), nullable=True)

  issue_tracked_obj = utils.PolymorphicRelationship("object_id", "object_type",
                                                    "{}_issue_tracked")

  people_sync_enabled = db.Column(db.Boolean, nullable=False, default=True)

  @classmethod
  def get_issue(cls, object_type, object_id):
    """Returns an issue object by given type and ID or None.

    Args:
      object_type: A string representing a model.
      object_id: An integer identifier of model's instance.

    Returns:
      An instance of IssuetrackerIssue or None.
    """
    return cls.query.filter(
        cls.object_type == object_type,
        cls.object_id == object_id).first()

  # pylint: disable=arguments-differ
  def to_dict(self, include_issue=False, include_private=False):
    """Returns representation of object as a dict.

    Args:
      include_issue: A boolean whether to include issue related properties.
      include_private: A boolean whether to include private properties.

    Returns:
      A dict representing an instance of IssuetrackerIssue.
    """
    res = {
        'enabled': self.enabled,
        'component_id': self.component_id,
        'hotlist_id': self.hotlist_id,
        'issue_type': self.issue_type,
        'issue_priority': self.issue_priority,
        'issue_severity': self.issue_severity,
        'people_sync_enabled': self.people_sync_enabled,
    }

    if include_issue:
      res['issue_id'] = self.issue_id
      res['issue_url'] = self.issue_url
      res['title'] = self.title

    if include_private:
      res['object_id'] = self.object_id
      res['object_type'] = self.object_type
      res['reporter'] = self.reporter
      res['assignee'] = self.assignee
      res['cc_list'] = self.cc_list.split(',') if self.cc_list else []

    return res

  @classmethod
  def create_or_update_from_dict(cls, obj, info):
    """Creates or updates issue with given parameters.

    Args:
      obj: An object which is an IssueTracked instance.
      info: A dict with issue properties.

    Returns:
      An instance of IssuetrackerIssue.
    """
    if not info:
      raise ValueError('Issue tracker info cannot be empty.')

    issue_obj = cls.get_issue(obj.type, obj.id)

    info = dict(info, issue_tracked_obj=obj)
    if issue_obj is not None:
      issue_obj.update_from_dict(info)
    else:
      issue_obj = cls.create_from_dict(info)
      db.session.add(issue_obj)

    return issue_obj

  @classmethod
  def create_from_dict(cls, info):
    """Creates issue with given parameters.

    Args:
      info: A dict with issue properties.

    Returns:
      An instance of IssuetrackerIssue.
    """

    cc_list = info.get('cc_list')
    if cc_list is not None:
      cc_list = ','.join(cc_list)

    return cls(
        issue_tracked_obj=info['issue_tracked_obj'],

        enabled=bool(info.get('enabled')),
        title=info.get('title'),
        component_id=info.get('component_id'),
        hotlist_id=info.get('hotlist_id'),
        issue_type=info.get('issue_type'),
        issue_priority=info.get('issue_priority'),
        issue_severity=info.get('issue_severity'),

        reporter=info.get('reporter'),
        assignee=info.get('assignee'),
        cc_list=cc_list,

        issue_id=info.get('issue_id'),
        issue_url=info.get('issue_url'),

        people_sync_enabled=bool(info.get('people_sync_enabled', True)),
    )

  def update_from_dict(self, info):
    """Updates issue with given parameters.

    Args:
      info: A dict with issue properties.

    Returns:
      An instance of IssuetrackerIssue.
    """
    cc_list = info.pop('cc_list', None)

    info = dict(
        self.to_dict(include_issue=True, include_private=True),
        **info)

    if cc_list is not None:
      info['cc_list'] = cc_list

    if info['cc_list'] is not None:
      info['cc_list'] = ','.join(info['cc_list'])

    self.object_type = info['object_type']
    self.object_id = info['object_id']
    self.enabled = info['enabled']
    self.title = info['title']
    self.component_id = info['component_id']

    self.hotlist_id = info['hotlist_id']

    self.issue_type = info['issue_type']
    self.issue_priority = info['issue_priority']
    self.issue_severity = info['issue_severity']
    self.reporter = info['reporter']
    self.assignee = info['assignee']
    self.cc_list = info['cc_list']

    self.issue_id = info['issue_id']
    self.issue_url = info['issue_url']

    self.due_date = info.get('due_date')

    self.people_sync_enabled = info['people_sync_enabled']

  @staticmethod
  def get_issuetracker_issue_stub():
    """Returns dict with all Issue Tracker fields with empty values."""
    return {
        '_is_stub': True,
        'enabled': False,
        'component_id': None,
        'hotlist_id': None,
        'issue_type': None,
        'issue_priority': None,
        'issue_severity': None,
        'title': None,
        'issue_id': None,
        'issue_url': None,
        'people_sync_enabled': True,
    }
