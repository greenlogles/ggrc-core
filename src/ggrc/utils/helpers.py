# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Helper methods."""
import functools
from functools import wraps

from flask import _app_ctx_stack
import sqlalchemy as sa

from ggrc.utils import structures


def without_sqlalchemy_cache(func):
  """Switch off SQLAlchemy queries cache filling.

  SQLAlchemy cache all executed queries in
  _app_ctx_stack.top.sqlalchemy_queries variable if DEBUG or
  SQLALCHEMY_RECORD_QUERIES config variable is set to True.
  This decorator make the cache empty everywhere in wrapped function.
  """
  @wraps(func)
  def wrapper(*args, **kwargs):
    """Wrapper function."""
    # Running migration can trigger this decorator, but in such case
    # no top will be there as flask-sqlalchemy haven't created it.
    # For such case just run the function without patching
    # _app_ctx_stack.top.sqlalchemy_queries.
    if not getattr(_app_ctx_stack, "top"):
      return func(*args, **kwargs)

    queries = None
    has_queries = False
    if hasattr(_app_ctx_stack.top, "sqlalchemy_queries"):
      queries = _app_ctx_stack.top.sqlalchemy_queries
      has_queries = True

    _app_ctx_stack.top.sqlalchemy_queries = structures.EmptyList()

    try:
      res = func(*args, **kwargs)
    finally:
      if has_queries:
        _app_ctx_stack.top.sqlalchemy_queries = queries
      else:
        del _app_ctx_stack.top.sqlalchemy_queries
    return res
  return wrapper


def has_attr_changes(obj, attr_name):
  # type: (db.Model, str) -> bool
  """Check if object has changes of specific attribute in current session."""
  attr_state = getattr(sa.inspect(obj).attrs, attr_name, None)
  return attr_state and attr_state.history.has_changes()


def assert_type(obj, expected_type):
  """Check if provided object has proper type."""
  if not isinstance(obj, expected_type):
    raise ValueError(
        "Object of incorrect type '{}' provided. "
        "Should be '{}'".format(type(obj), expected_type)
    )


def cached(func):
  """Memorize the return value of a function."""
  memo = {}

  @functools.wraps(func)
  def wrapper(*args):
    """Wrapper function."""
    keys = str(args)
    if keys in memo:
      return memo[keys]

    result = func(*args)
    memo[keys] = result
    return result

  return wrapper
