# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>
"""Handlers for Issue Tracker fields"""

import re

from ggrc.converters import errors
from ggrc.converters.handlers import handlers
from ggrc.integrations import constants
from ggrc.models import all_models
from ggrc.models.hooks.issue_tracker import \
    issue_tracker_params_container as params_container


_ATTR_NAME_TO_ISSUE_TRACKER_KEY = {
    "issue_title": "title",
}


class IssueTrackerColumnHandler(handlers.ColumnHandler):
  """Column handler used for Issue Tracker related fields.

  This class provides method for Issue Tracker fields export and Issue Tracker
  default values.
  """

  def _get_issue_tracker_value(self, default=None):
    """Get value by corresponding key in issue_tracker dict"""
    key = _ATTR_NAME_TO_ISSUE_TRACKER_KEY.get(self.key, self.key)

    return self.row_converter.issue_tracker.get(key, default)

  def _set_issue_tracker_value(self, value):
    """Set value by corresponding key in issue_tracker dict"""
    key = _ATTR_NAME_TO_ISSUE_TRACKER_KEY.get(self.key, self.key)

    self.row_converter.issue_tracker[key] = value

  def get_value(self):
    return self._get_issue_tracker_value("")

  def set_obj_attr(self):
    if self.dry_run or self.value is None:
      return

    initial_obj = self.row_converter.obj
    if initial_obj.issue_tracker.get('enabled') and not self.value:
      issue = initial_obj.issuetracker_issue
      self.row_converter.objects_to_comment[issue.object_type]\
          .append(issue.object_id)

    self._set_issue_tracker_value(self.value)


class IssueTrackerWithValidStates(IssueTrackerColumnHandler):
  """Column handler for columns with available valid states"""

  available_items = {
      "issue_type":
          params_container.IssueTrackerParamsContainer.AVAILABLE_TYPES,
      "issue_priority": constants.AVAILABLE_PRIORITIES,
      "issue_severity":
          params_container.IssueTrackerParamsContainer.AVAILABLE_SEVERITIES,
  }

  def __init__(self, row_converter, key, **options):
    self.valid_states = self.available_items.get(key)
    super(IssueTrackerWithValidStates, self).__init__(row_converter,
                                                      key,
                                                      **options)

  def parse_item(self):
    value = self.raw_value.upper()
    if not value:
      return None
    if value not in self.valid_states:
      self.add_warning(
          errors.WRONG_VALUE_DEFAULT_CUSTOM,
          column_name=self.display_name,
          default_value=constants.DEFAULT_ISSUETRACKER_VALUES[self.key]
      )
      return None
    return value


class IssueTrackerAddsColumnHandler(IssueTrackerColumnHandler):
  """Column handler for hotlist and components ids"""

  def parse_item(self):
    if not self.raw_value:
      return None
    try:
      value = int(self.raw_value)
    except ValueError:
      self.add_warning(errors.WRONG_VALUE_DEFAULT,
                       column_name=self.display_name)
      return None
    return value


class IssueTrackerTitleColumnHandler(IssueTrackerColumnHandler):
  """Column handler for Issue title for IssueTracked models"""

  def parse_item(self):
    """ Remove multiple spaces and new lines from text """
    value = self.raw_value or ""
    value = self.clean_whitespaces(value)
    if not value:
      value = self.row_converter.obj.title or \
          self.row_converter.attrs["title"].value
    return value

  @staticmethod
  def clean_whitespaces(value):
    """Change multiple whitespaces with single one in the value string."""
    return re.sub(r"\s+", " ", value)

  def set_obj_attr(self):
    if self.dry_run or not self.value:
      return
    self._set_issue_tracker_value(self.value)


class IssueTrackerEnabledHandler(IssueTrackerColumnHandler):
  """Column handler for ticket tracker integration column."""
  _true = "on"
  _false = "off"

  TRUE_VALUES = {_true, }
  FALSE_VALUES = {_false, }

  RESTRICTED_MODELS = (all_models.Issue, all_models.Assessment)
  NOT_ALLOWED_STATUSES = {
      "Assessment": {"In Review", "Completed", "Deprecated", "Verified"},
      "Issue": {"Fixed", "Fixed and Verified", "Deprecated"},
  }

  def get_value(self):
    """Get value for current column."""
    if super(IssueTrackerEnabledHandler, self).get_value():
      return self._true
    return self._false

  def _needs_status_check(self):
    """Check if we should check status before turn integration on.

    According to our business rules we shouldn't generate tickets for Issues
    and Assessments in some statuses.
    We can turn integration On for all already linked Issues.
    """
    has_issue_id = self.row_converter.obj.issue_tracker.get("issue_id")
    restricted_instance = isinstance(self.row_converter.obj,
                                     self.RESTRICTED_MODELS)
    if restricted_instance and not has_issue_id:
      return True
    return False

  def _get_status(self):
    """Get Issue Status.

    First it would check if status was imported during current import.
    Otherwise tries to get status from object.
    """
    imported_status = self.row_converter.attrs.get("status")
    attrs_status_value = imported_status.value if imported_status else None
    return attrs_status_value or self.row_converter.obj.status

  def _wrong_status(self):
    """Check if not status correct for setting integration On"""
    status = self._get_status()
    disallowed_statuses = self.NOT_ALLOWED_STATUSES.get(
        self.row_converter.obj.__class__.__name__,
        {}
    )
    return status in disallowed_statuses

  def _get_err_message(self):
    """Return error message template for wrong status"""
    if isinstance(self.row_converter.obj, all_models.Issue):
      return errors.WRONG_ISSUE_TICKET_STATUS
    return errors.WRONG_ASSESSMENT_TICKET_STATUS

  def parse_item(self):
    value = self.raw_value.strip().lower()
    if not value:
      return None
    if value in self.TRUE_VALUES:
      if self._needs_status_check() and self._wrong_status():
        self.add_warning(self._get_err_message(),
                         column_name=self.display_name)
        return False
      return True
    if value in self.FALSE_VALUES:
      return False

    self.add_warning(errors.WRONG_VALUE_DEFAULT,
                     column_name=self.display_name)
    return None


class PeopleSyncEnabledHandler(IssueTrackerColumnHandler):
  """Column handler for ticket tracker people sync column."""
  _true = "on"
  _false = "off"

  TRUE_VALUES = {_true, }
  FALSE_VALUES = {_false, }

  def get_value(self):
    """Get value for current column."""
    if super(PeopleSyncEnabledHandler, self).get_value():
      return self._true
    return self._false

  def parse_item(self):
    """Parse provided value."""
    value = self.raw_value.strip().lower()
    if value in self.TRUE_VALUES:
      return True
    if value in self.FALSE_VALUES:
      return False

    self.add_warning(errors.WRONG_VALUE_DEFAULT,
                     column_name=self.display_name)
    return True
