# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Relationship creation/modification hooks."""

from datetime import datetime
import functools
import logging
import itertools

import sqlalchemy as sa
from werkzeug.exceptions import BadRequest

from ggrc import db
from ggrc.login import is_external_app_user
from ggrc.models import all_models
from ggrc.models import exceptions
from ggrc.models.comment import Commentable, ExternalCommentable
from ggrc.models.hooks import assessment as asmt_hooks
from ggrc.models.hooks.issue_tracker import assessment_integration
from ggrc.models.mixins.base import ChangeTracked
from ggrc.services import signals


LOGGER = logging.getLogger(__name__)


def _handle_del_audit_issue_mapping(audit, issue):
  """Unset audit_id and context_id from issue if allowed else fail."""
  if issue in db.session.deleted:
    # the issue is being removed, no action needed
    return
  if audit not in db.session.deleted and not issue.allow_unmap_from_audit:
    raise exceptions.ValidationError("Issue#{issue.id} can't be unmapped "
                                     "from Audit#{audit.id}: common mappings."
                                     .format(issue=issue, audit=audit))

  issue.audit = None
  issue.context = None


def _handle_new_audit_issue_mapping(audit, issue):
  """Set audit_id and context_id on issue if allowed else fail."""
  if not issue.allow_map_to_audit:
    raise exceptions.ValidationError(
        "Issue#{issue.id} can't be mapped to Audit#{audit.id}: already mapped "
        "to Audit#{mapped_audit_id}"
        .format(issue=issue, audit=audit,
                mapped_audit_id=issue.audit_id or issue.audit.id))

  issue.audit = audit
  issue.context = audit.context


def _order(src, dst):
  """Sort parameters by classname and id."""
  return ((src, dst) if (src.type, src.id) < (dst.type, dst.id) else
          (dst, src))


def _is_audit_issue(src, dst):
  """Return True if src.type == "Audit" and dst.type == "Issue"."""
  return ((src.type, dst.type) ==
          (all_models.Audit.__name__, all_models.Issue.__name__))


def from_session(*models, **set_selectors):
  """Wrap session listener to run only for required models.

  Args:
    models: list of model classes, only instances of the provided types will
      be passed to the decorated function;
    set_selectors: allowed keys are "new", "dirty", "deleted", allowed values
      are bool, if you pass {"new": True} the matching objects from session.new
      will be passed to the decorated function.
  """

  new = set_selectors.get("new", False)
  dirty = set_selectors.get("dirty", False)
  deleted = set_selectors.get("deleted", False)

  def decorator(function):
    """Decorate function as inner."""
    @functools.wraps(function)
    def inner(session, flush_context, instances):
      """Call function with iterator of only needed items from session.

      flush_context and instances are ignored in the common case.
      """
      # pylint: disable=unused-argument
      sets = []
      if new:
        sets.append(session.new)
      if dirty:
        sets.append(session.dirty)
      if deleted:
        sets.append(session.deleted)

      items = (o for o in itertools.chain(*sets) if isinstance(o, models))

      return function(items)

    return inner

  return decorator


@from_session(all_models.Relationship, new=True, dirty=True)
def handle_new_audit_issue_mapping(instances):
  """Check and process new Audit-Issue mapping rules.

  Triggers rule processing functions for creation of Audit-Issue Relationships.
  """
  # pylint: disable=unused-argument

  for instance in instances:
    if not instance.source:
      # TODO: fix actions to make this impossible
      LOGGER.error(u"Relationship.source is not filled in properly: "
                   u"id=%r, source_id=%r, source_type=%r, "
                   u"destination_id=%r, destination_type=%r.",
                   instance.id, instance.source_id, instance.source_type,
                   instance.destination_id, instance.destination_type)
      continue
    if not instance.destination:
      # TODO: fix Document imports to make this impossible
      LOGGER.error(u"Relationship.destination is not filled in properly: "
                   u"id=%r, source_id=%r, source_type=%r, "
                   u"destination_id=%r, destination_type=%r.",
                   instance.id, instance.source_id, instance.source_type,
                   instance.destination_id, instance.destination_type)
      continue
    src, dst = _order(instance.source, instance.destination)
    if _is_audit_issue(src, dst):
      _handle_new_audit_issue_mapping(audit=src, issue=dst)


@from_session(all_models.Relationship, deleted=True)
def handle_del_audit_issue_mapping(instances):
  """Check and process deleted Audit-Issue mapping rules.

  Triggers rule processing functions for deletion of Audit-Issue Relationships.
  """
  for instance in instances:
    src, dst = _order(instance.source, instance.destination)
    if _is_audit_issue(src, dst):
      _handle_del_audit_issue_mapping(audit=src, issue=dst)


def related_condition(obj, type_):
  """Get condition to select all relationships between objects"""
  return sa.or_(
      sa.and_(
          all_models.Relationship.source_id == obj.id,
          all_models.Relationship.source_type == obj.type,
          all_models.Relationship.destination_type == type_,
      ),
      sa.and_(
          all_models.Relationship.destination_id == obj.id,
          all_models.Relationship.destination_type == obj.type,
          all_models.Relationship.source_type == type_,
      )
  )


def related_to_other_condition(obj, other):
  """Get condition to select all relationships between
   objects except passed one"""
  return sa.or_(
      sa.and_(
          all_models.Relationship.source_id == obj.id,
          all_models.Relationship.source_type == obj.type,
          all_models.Relationship.destination_type == other.type,
          all_models.Relationship.destination_id != other.id,
      ),
      sa.and_(
          all_models.Relationship.destination_id == obj.id,
          all_models.Relationship.destination_type == obj.type,
          all_models.Relationship.source_type == other.type,
          all_models.Relationship.source_id != other.id,
      )
  )


def related_id(related_to_type, label):
  """Get source_id/destination_id related to provided type"""
  return sa.case(
      [(
          all_models.Relationship.source_type == related_to_type,
          all_models.Relationship.destination_id
      )],
      else_=all_models.Relationship.source_id
  ).label(label)


def unmap_issue_cascade(asmnt, issue):
  """Unmap issue cascade: unmap all Audits/Snapshots mapped both to Issue and
  Assessment and not mapped to any other Assessment
  """
  # All relationships of current Issue and Assessments except current one
  other_asmnts = db.session.query(related_id("Issue", "asmnt_id")).filter(
      related_to_other_condition(issue, asmnt)
  )

  other_asmnt_snap_ids = db.session.query(
      related_id("Assessment", "snapshot_id"),
  ).filter(
      sa.or_(
          sa.and_(
              all_models.Relationship.source_id.in_(other_asmnts),
              all_models.Relationship.source_type == "Assessment",
              all_models.Relationship.destination_type == "Snapshot",
          ),
          sa.and_(
              all_models.Relationship.destination_id.in_(other_asmnts),
              all_models.Relationship.destination_type == "Assessment",
              all_models.Relationship.source_type == "Snapshot",
          )
      )
  ).subquery()

  asmnt_snap_ids = db.session.query(
      related_id("Assessment", "snapshot_id"),
  ).filter(
      related_condition(asmnt, "Snapshot")
  ).subquery()

  # Find relationships between Snapshot and Issue to unmap
  snapshot_rels = all_models.Relationship.query.filter(
      sa.and_(
          related_condition(issue, "Snapshot"),
          related_id("Issue", "snapshot_id").in_(asmnt_snap_ids),
          ~related_id("Issue", "snapshot_id").in_(other_asmnt_snap_ids),
          all_models.Relationship.automapping_id.isnot(None),
      )
  )

  # If there are not any relationships between Issue and Assessment
  # except one we received in request, we can unmap Issue from Audit
  if not db.session.query(other_asmnts.exists()).scalar():
    audit_rels = all_models.Relationship.query.filter(
        sa.or_(
            sa.and_(
                all_models.Relationship.source_id == issue.id,
                all_models.Relationship.source_type == issue.type,
                all_models.Relationship.destination_type == issue.audit.type,
                all_models.Relationship.destination_id == issue.audit.id,
                all_models.Relationship.automapping_id.isnot(None),
            ),
            sa.and_(
                all_models.Relationship.source_id == issue.audit.id,
                all_models.Relationship.source_type == issue.audit.type,
                all_models.Relationship.destination_id == issue.id,
                all_models.Relationship.destination_type == issue.type,
                all_models.Relationship.automapping_id.isnot(None),
            )
        )
    )
  else:
    audit_rels = []

  for instance in itertools.chain(snapshot_rels, audit_rels):
    # this loop makes sure that before_flush hooks are executed
    db.session.delete(instance)


def related(base_objects, rel_cache):
  """Get stubs of related objects

  Args:
    base_objects(list): Stubs of objects for which related objects
    should be collected
    rel_cache(RelationshipsCache): Instance of relationship cache

  Returns:
    Dict of base objects with their mappings
  """
  stubs = [o for o in base_objects if o not in rel_cache.cache]
  if stubs:
    rel_cache.populate_cache(stubs)

  return {o: (rel_cache.cache[o] if o in rel_cache.cache else set())
          for o in base_objects}


def copy_snapshot_test_plan(objects):
  """Append snapshot test plan into assessment test plan"""
  for obj in objects:
    if (obj.source_type == "Assessment" and
        obj.destination_type == "Snapshot") or (
        obj.source_type == "Snapshot" and
        obj.destination_type == "Assessment"
    ):
      asmnt, snapshot = obj.source, obj.destination
      if asmnt.type != "Assessment":
        asmnt, snapshot = snapshot, asmnt

      # Test plan of snapshotted object should be copied to
      # Assessment test plan in case of proper snapshot type
      # and if test_plan_procedure was set to True
      types_match = asmnt.assessment_type == snapshot.child_type
      if types_match and asmnt.test_plan_procedure:
        asmt_hooks.set_test_plan(
            assessment=asmnt,
            template=None,
            snapshot_rev_content=snapshot.revision.content,
        )

      handler = assessment_integration.AssessmentTrackerHandler()
      handler.handle_assmt_test_plan_update(asmnt)


def delete_comment_notification(comment):
  """Remove notification for external model comments."""
  all_models.Notification.query.filter(
      all_models.Notification.object_type == "Comment",
      all_models.Notification.object_id == comment.id
  ).delete()


def init_hook():  # noqa
  """Initialize Relationship-related hooks."""
  # pylint: disable=unused-variable

  sa.event.listen(sa.orm.session.Session, "before_flush",
                  handle_new_audit_issue_mapping)
  sa.event.listen(sa.orm.session.Session, "before_flush",
                  handle_del_audit_issue_mapping)

  # Event listener for relationship delete operation validate.
  sa.event.listen(
      all_models.Relationship,
      'before_delete',
      all_models.Relationship.validate_delete)

  @signals.Restful.model_deleted.connect_via(all_models.Relationship)
  def handle_cascade_delete(sender, obj, service):
    """Process cascade removing of relationship."""
    # pylint: disable=unused-argument
    is_asmnt_issue_rel = (
        obj.source_type == "Assessment" and
        obj.destination_type == "Issue"
    ) or (
        obj.source_type == "Issue" and
        obj.destination_type == "Assessment"
    )
    cascade = service.request.args.get("cascade")
    if cascade and cascade.lower() == "true" and is_asmnt_issue_rel:
      asmnt, issue = obj.source, obj.destination
      if asmnt.type != "Assessment":
        asmnt, issue = issue, asmnt

      unmap_issue_cascade(asmnt, issue)

  @signals.Restful.collection_posted.connect_via(all_models.Relationship)
  def handle_asmnt_plan(sender, objects=None, sources=None, **kwargs):
    """Handle assessment test plan"""
    # pylint: disable=unused-argument
    copy_snapshot_test_plan(objects)

  @signals.Restful.collection_posted.connect_via(all_models.Relationship)
  def handle_comment_mapping(sender, objects=None, **kwargs):
    """Update Commentable.updated_at when Comment mapped."""
    # pylint: disable=unused-argument

    for obj in objects:
      if (obj.source_type not in ("Comment", "ExternalComment") and
         obj.destination_type not in ("Comment", "ExternalComment")):
        continue

      comment, other = obj.source, obj.destination
      if comment.type not in ("Comment", "ExternalComment"):
        comment, other = other, comment

      if isinstance(other, (Commentable, ExternalCommentable, ChangeTracked)):
        other.updated_at = datetime.utcnow()

  @signals.Restful.model_posted_after_commit.connect_via(
      all_models.Relationship
  )
  def handle_posted_after_commit(obj_class, obj, **kwargs):
    """Send people mentions on comment posted."""
    # pylint: disable=unused-argument
    from ggrc.notifications import people_mentions

    if (obj.source_type not in ("Comment", "ExternalComment") and
       obj.destination_type not in ("Comment", "ExternalComment")):
      return

    comment, other = obj.source, obj.destination
    if comment.type not in ("Comment", "ExternalComment"):
      comment, other = other, comment

    if isinstance(other, (Commentable, ExternalCommentable, ChangeTracked)):
      people_mentions.handle_comment_mapped(obj=other, comments=[comment])

  @signals.Restful.collection_posted.connect_via(all_models.Relationship)
  def forbid_self_relationship(sender, objects=None, **kwargs):
    """Validates that created Relationship doesn't have same object
    as source and destination"""
    # pylint: disable=unused-argument
    for obj in objects:
      if (obj.source_type == obj.destination_type and
              obj.source_id == obj.destination_id):
        raise BadRequest("The mapping of object on itself is not possible")

  # pylint: disable=unused-argument
  @signals.Restful.collection_posted.connect_via(all_models.Relationship)
  def validate_external_creation(sender, objects=None, **kwargs):
    """
        Validate that external user can't create regular relationships.
        Validate that local user can't create external relationships.
    """
    error_message = (
        u"You do not have the necessary permissions to "
        u"create {} relationships."
    )
    for obj in objects:
      if is_external_app_user() and not obj.is_external:
        raise BadRequest(error_message.format("regular"))

      elif not is_external_app_user() and obj.is_external:
        raise BadRequest(error_message.format("external"))
