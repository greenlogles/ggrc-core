/*
    Copyright (C) 2019 Google Inc.
    Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>
*/

import Cacheable from '../cacheable';
import uniqueTitle from '../mixins/unique-title';
import caUpdate from '../mixins/ca-update';
import timeboxed from '../mixins/timeboxed';
import accessControlList from '../mixins/access-control-list';
import scopeObjectNotifications from '../mixins/scope-object-notifications';
import questionnaire from '../mixins/questionnaire';
import Stub from '../stub';

export default Cacheable('CMS.Models.OrgGroup', {
  root_object: 'org_group',
  root_collection: 'org_groups',
  category: 'scope',
  findAll: 'GET /api/org_groups',
  findOne: 'GET /api/org_groups/{id}',
  create: 'POST /api/org_groups',
  update: 'PUT /api/org_groups/{id}',
  destroy: 'DELETE /api/org_groups/{id}',
  mixins: [
    uniqueTitle,
    caUpdate,
    timeboxed,
    accessControlList,
    scopeObjectNotifications,
    questionnaire,
  ],
  is_custom_attributable: true,
  isRoleable: true,
  attributes: {
    context: Stub,
    modified_by: Stub,
  },
  tree_view_options: {
    attr_list: Cacheable.attr_list.concat([
      {attr_title: 'Reference URL', attr_name: 'reference_url'},
      {attr_title: 'Effective Date', attr_name: 'start_date'},
      {attr_title: 'Last Deprecated Date', attr_name: 'end_date'},
      {
        attr_title: 'Launch Status',
        attr_name: 'status',
        order: 40,
      }, {
        attr_title: 'Description',
        attr_name: 'description',
        disable_sorting: true,
      }, {
        attr_title: 'Notes',
        attr_name: 'notes',
        disable_sorting: true,
      }, {
        attr_title: 'Assessment Procedure',
        attr_name: 'test_plan',
        disable_sorting: true,
      },
    ]),
  },
  sub_tree_view_options: {
    default_filter: ['Program'],
  },
  defaults: {
    status: 'Draft',
  },
  statuses: ['Draft', 'Deprecated', 'Active'],
  init: function () {
    if (this._super) {
      this._super(...arguments);
    }

    this.validateNonBlank('title');
  },
}, {});
