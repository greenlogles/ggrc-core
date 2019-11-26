# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""This module contains common utils for integration functionality."""

import sqlalchemy as sa

from ggrc import db
from ggrc import settings
from ggrc.models import exceptions
from ggrc.models import all_models
from ggrc.integrations import constants
from ggrc.utils import referenced_objects


def is_already_linked(ticket_id):
  """Checks if ticket with ticket_id is already linked to GGRC object"""
  if ticket_id is None:
    return False
  exists_query = db.session.query(
      all_models.IssuetrackerIssue.issue_id
  ).filter_by(issue_id=ticket_id).exists()
  return db.session.query(exists_query).scalar()


def normalize_issue_tracker_info(info):
  """Insures that component ID and hotlist ID are integers."""
  # TODO(anushovan): remove data type casting once integration service
  #   supports strings for following properties.
  component_id = info.get('component_id')
  if component_id:
    try:
      info['component_id'] = int(component_id)
    except (TypeError, ValueError):
      raise exceptions.ValidationError('Component ID must be a number.')

  hotlist_id = info.get('hotlist_id')
  if hotlist_id:
    try:
      info['hotlist_id'] = int(hotlist_id)
    except (TypeError, ValueError):
      raise exceptions.ValidationError('Hotlist ID must be a number.')


def populate_issue_tracker_fields(assmt, issue_tracker_info,
                                  with_update=False, create_mode=False):
  """Populate issue tracker fields values.

  Current list of fields with default values: component_id, hotlist_id,
    issue_type, priority, severity. They would be taken from default values if
    they are empty in appropriate audit.
  Current list of values that would be taken from assessment: title, status,
    due date.

  Args
    assmt (Assessment): Assessment instance
    issue_tracker_info (dict): dictionary with issue information
    with_update (bool): overwrite existing value
  """
  audit_info = assmt.audit.issue_tracker or {}
  default_values = constants.DEFAULT_ISSUETRACKER_VALUES
  if not issue_tracker_info.get("component_id") or with_update:
    issue_tracker_info["component_id"] = audit_info.get("component_id") or\
        default_values["component_id"]

  if not issue_tracker_info.get("hotlist_id") or with_update:
    issue_tracker_info["hotlist_id"] = audit_info.get("hotlist_id") or\
        default_values["hotlist_id"]

  if not issue_tracker_info.get("issue_type") or with_update:
    issue_tracker_info["issue_type"] = audit_info.get("issue_type") or\
        default_values["issue_type"]

  if not issue_tracker_info.get("issue_priority") or with_update:
    issue_tracker_info["issue_priority"] = audit_info.get("issue_priority") or\
        default_values["issue_priority"]

  if not issue_tracker_info.get("issue_severity") or with_update:
    issue_tracker_info["issue_severity"] = audit_info.get("issue_severity") or\
        default_values["issue_severity"]

  if not issue_tracker_info.get("title"):
    issue_tracker_info["title"] = assmt.title

  if not issue_tracker_info.get("status"):
    status_mapping = constants.CREATE_STATUSES_MAPPING if create_mode else \
        constants.STATUSES_MAPPING
    issue_tracker_info["status"] = status_mapping.get(assmt.status)

  if not issue_tracker_info.get('due_date'):
    issue_tracker_info['due_date'] = assmt.start_date


def build_issue_tracker_url(issue_id):
  """Build issue tracker URL by issue id."""
  issue_tracker_tmpl = settings.ISSUE_TRACKER_BUG_URL_TMPL
  url_tmpl = issue_tracker_tmpl if issue_tracker_tmpl else 'http://issue/%s'
  return url_tmpl % issue_id


def exclude_auditor_emails(emails):
  """Returns new email set with excluded auditor emails."""
  acl = all_models.AccessControlList
  acr = all_models.AccessControlRole
  acp = all_models.AccessControlPerson

  if not isinstance(emails, set):
    emails = set(emails)

  auditor_emails = db.session.query(
      all_models.Person.email
  ).join(
      acp
  ).join(
      acl
  ).join(
      acr
  ).filter(
      acr.name == "Auditors",
      all_models.Person.email.in_(emails)
  ).distinct().all()

  emails_to_exlude = {line.email for line in auditor_emails}
  return emails - emails_to_exlude


def build_created_objects_args(revision_ids, mail_data):
  """Build params for bulk ticket create."""
  objects = _collect_created_objects(revision_ids)

  return _create_args(objects, mail_data)


def build_updated_objects_args(revision_ids, mail_data):
  """Build params for bulk ticket update."""
  objects = _collect_update_objects(revision_ids)

  return _create_args(objects, mail_data)


def build_comments_args(revision_ids, mail_data):
  """Build params for bulk Issue Tracker comments update."""
  comments = _collect_comments(revision_ids)
  arg_list = {}
  arg_list["comments"] = [{
      "type": obj[0],
      "id": int(obj[1]),
      "comment_description": obj[2]
  } for obj in comments]

  arg_list["mail_data"] = mail_data
  return arg_list


def _create_args(objects, mail_data):
  """Create args to call bulk update/create"""

  if not objects:
    return {}

  arg_list = {}
  arg_list["objects"] = [
      {"type": obj[0], "id": int(obj[1])} for obj in objects
  ]
  arg_list["mail_data"] = mail_data
  return arg_list


def _collect_created_objects(revision_ids):
  """Collect all issuetracked models and issuetracker issues for create.

  Args:
      revision_ids: list of revision ids we should look through.
  Returns:
      list of issuetracked objects that were imported.
  """

  revisions = all_models.Revision
  iti = all_models.IssuetrackerIssue

  issue_tracked_objects = db.session.query(
      revisions.resource_type,
      revisions.resource_id,
  ).join(
      iti,
      sa.and_(
          revisions.resource_type == iti.object_type,
          revisions.resource_id == iti.object_id
      )
  ).filter(
      revisions.resource_type.in_(constants.ISSUE_TRACKED_MODELS),
      revisions.id.in_(revision_ids),
      iti.issue_id.is_(None),
      iti.enabled != 0,
  )

  iti_related_objects = db.session.query(
      iti.object_type,
      iti.object_id,
  ).join(
      revisions,
      revisions.resource_id == iti.id,
  ).filter(
      revisions.resource_type == "IssuetrackerIssue",
      revisions.id.in_(revision_ids),
      iti.object_type.in_(constants.ISSUE_TRACKED_MODELS),
      iti.issue_id.is_(None),
      iti.enabled != 0,
  )

  return issue_tracked_objects.union(iti_related_objects).all()


def _collect_update_objects(revision_ids):
  """Collect all issuetracked models and issuetracker issues for update.

  Args:
      revision_ids: list of revision ids we should look through.
  Returns:
      list of issuetracked objects that were imported.
  """

  revisions = all_models.Revision
  iti = all_models.IssuetrackerIssue

  issue_tracked_objects = db.session.query(
      revisions.resource_type,
      revisions.resource_id,
  ).join(
      iti,
      sa.and_(
          revisions.resource_type == iti.object_type,
          revisions.resource_id == iti.object_id
      )
  ).filter(
      revisions.resource_type.in_(constants.ISSUE_TRACKED_MODELS),
      revisions.id.in_(revision_ids),
      iti.issue_id.isnot(None),
      iti.enabled != 0,
  )

  iti_related_objects = db.session.query(
      iti.object_type,
      iti.object_id,
  ).join(
      revisions,
      revisions.resource_id == iti.id,
  ).filter(
      revisions.resource_type == "IssuetrackerIssue",
      revisions.id.in_(revision_ids),
      iti.object_type.in_(constants.ISSUE_TRACKED_MODELS),
      iti.issue_id.isnot(None),
      iti.enabled != 0,
  )

  return issue_tracked_objects.union(iti_related_objects).all()


def _collect_comments(revision_ids):
  """Collect all comments to issuetracked models.

     Args:
         revision_ids: list of revision ids we should look through
     Returns:
         list of comments that were imported.
   """
  revisions = all_models.Revision
  comments = all_models.Comment

  imported_comments_src = db.session.query(
      revisions.destination_type,
      revisions.destination_id,
      comments.description,
  ).join(
      comments,
      revisions.source_id == comments.id,
  ).filter(
      revisions.resource_type == "Relationship",
      revisions.source_type == "Comment",
      revisions.id.in_(revision_ids),
      revisions.destination_type.in_(constants.ISSUE_TRACKED_MODELS)
  )

  imported_comments_dst = db.session.query(
      revisions.source_type,
      revisions.source_id,
      comments.description,
  ).join(
      comments,
      revisions.destination_id == comments.id,
  ).filter(
      revisions.resource_type == "Relationship",
      revisions.destination_type == "Comment",
      revisions.id.in_(revision_ids),
      revisions.source_type.in_(constants.ISSUE_TRACKED_MODELS)
  )
  return imported_comments_dst.union(imported_comments_src).all()


def _get_assessment_template(obj_src):
  """Get asmt template object which is referenced by current row if exists"""

  tmpl_info = obj_src.get('template')
  if not tmpl_info or not isinstance(tmpl_info, dict):
    return None

  return referenced_objects.get(tmpl_info.get('type'), tmpl_info.get('id'))


def _get_issue_tracker(obj):
  """Get issue_tracker dict from obj if dict is based on existing tracker"""

  if not obj:
    return None

  ret = obj.issue_tracker
  if not ret.get('_is_stub'):
    return ret

  return None


def _get_default_issue_tracker(obj):
  """Return default issue_tracker info populated with obj specific data"""

  # get default values from constants
  # add to issue_tracker all required fields which exist in constants
  defaults = constants.DEFAULT_ISSUETRACKER_VALUES
  keys = all_models.IssuetrackerIssue.get_issuetracker_issue_stub()
  ret = dict(
      (key, defaults[key]) for key in keys if key in defaults)

  # for Issue hotlist_id and component_id are placed to issue specific keys
  if isinstance(obj, all_models.Issue):
    ret["hotlist_id"] = defaults['issue_hotlist_id']
    ret["component_id"] = defaults['issue_component_id']
  # for Assessment default issue title is assessment title
  if isinstance(obj, all_models.Assessment):
    ret["title"] = obj.title

  return ret


def _get_disabled_for_audit_or_app(obj):
  """Return dict with disabled integration if it's disabled in audit or app"""

  if not settings.ISSUE_TRACKER_ENABLED:
    return {"enabled": False}

  is_asmt = isinstance(obj, all_models.Assessment)
  is_tmpl = isinstance(obj, all_models.AssessmentTemplate)

  # disable integration for asmt and tmpl if audit integration is OFF

  if not is_asmt and not is_tmpl:
    return None

  audit_issue_tracker = _get_issue_tracker(obj.audit) or dict()
  if not audit_issue_tracker.get('enabled'):
    return {'enabled': False}

  return None


def _get_all_issue_trackers(obj, obj_src):
  """Get ordered list of issue tracker dicts to be used for object

  For Issue:
     1) * only for "enabled" key - set OFF if enabled=OFF for APP
     2) data which is requested by user
     3) default values, default hotlist_id/component_id are stored
        in specific keys.
  For Audit:
     1) * only for "enabled" key - set OFF if enabled=OFF for APP
     2) data which is requested by user
     3) default values
  For Assessment the list is the following:
     1) * only for "enabled" key - set OFF if enabled=OFF for Audit or APP
     2) data by user + set enabled=OFF if enabled=OFF for Audit
     3) assessment template if available
     4) audit if available
     5) default values, default title is Assessment title
  For Assessment Template the list is the following:
     1) * only for "enabled" key - set OFF if enabled=OFF for Audit or APP
     2) data which is requested by user
     3) audit if available
     4) default values

  :return: list of issue_tracker dicts. The list has at least 1 item
  """
  ret = list()

  is_asmt = isinstance(obj, all_models.Assessment)
  is_tmpl = isinstance(obj, all_models.AssessmentTemplate)

  # disable integration for asmt and tmpl if audit integration is OFF
  ret.append(_get_disabled_for_audit_or_app(obj))

  # issue_tracker related data source depends on the way user make request
  # for API call it is obj_src, for import - dict is stored in IssueTracked obj
  src = obj.issue_tracker_to_import if obj.is_import else obj_src

  obj_issue_tracker = src.get('issue_tracker')
  if isinstance(obj_issue_tracker, dict) and obj_issue_tracker:
    ret.append(obj_issue_tracker)

  if is_asmt:
    # get issue tracker values from assessment template if available
    dct = _get_issue_tracker(_get_assessment_template(src))
    ret.append(dct)

  if is_asmt or is_tmpl:
    # get issue tracker values from audit if available
    ret.append(_get_issue_tracker(obj.audit))

  ret.append(_get_default_issue_tracker(obj))

  # remove None or empty values
  ret = list(a for a in ret if a)

  return ret


def _get_all_issue_tracker_keys(all_issue_trackers):
  """Collect all keys in all dicts."""

  ret = set()
  for dct in all_issue_trackers:
    ret.update(dct.keys())

  return ret


def collect_issue_tracker_info(obj, obj_src):
  """Set predefined values for issue tracker based on object type

  For each key we have to get first not-None value from all defined
  issue tracker dicts
  """

  all_issue_trackers = _get_all_issue_trackers(obj, obj_src)

  keys = _get_all_issue_tracker_keys(all_issue_trackers)
  ret = dict()
  for key in keys:
    # get first non-null value for specified key
    values = list(dct.get(key) for dct in all_issue_trackers)
    values = list(val for val in values if val is not None)
    value = values[0] if values else None

    ret[key] = value

  return ret


def update_issue_tracker_for_import(obj):
  """Update issue_tracker info in DB for obj which is modified in import

  objects not from import will be skipped

  :param obj: IssueTracked object for which issue_tracker have to be updated
  """

  if not obj.is_import:
    return

  issue_tracker_info = collect_issue_tracker_info(obj, dict())
  all_models.IssuetrackerIssue.create_or_update_from_dict(
      obj,
      issue_tracker_info
  )
