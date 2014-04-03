/*!
    Copyright (C) 2013 Google Inc., authors, and contributors <see AUTHORS file>
    Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>
    Created By: brad@reciprocitylabs.com
    Maintained By: brad@reciprocitylabs.com
*/

(function(can, $) {

can.Control("CMS.Controllers.Dashboard", {
    defaults: {
        model_descriptors: null
      , menu_tree_spec: null
      , widget_descriptors: null
      //, widget_listeners: null
      //, widget_containers: null
      //
    }

}, {
    init: function(el, options) {
      this.init_page_title();
      this.init_page_help();
      this.init_page_header();
      this.init_widget_descriptors();
      if (!this.options.menu_tree)
        this.init_menu_tree();
      if (!this.inner_nav_controller)
        this.init_inner_nav();
      this.update_inner_nav();
      if (!this.add_widget_controller)
        this.init_add_widget();

      // Before initializing widgets, hide the container to not show
      // loading state of multiple widgets before reducing to one.
      this.hide_widget_area();
      this.init_default_widgets();
      if (!this.widget_area_controller)
        this.init_widget_area();
    }

  , init_page_title: function() {
      var page_title = null;
      if (typeof(this.options.page_title) === "function")
        page_title = this.options.page_title(this);
      else if (this.options.page_title)
        page_title = this.options.page_title;
      if (page_title)
        $("head > title").text(page_title);
    }

  , init_page_help: function() {
      var page_help = null;
      if (typeof(this.options.page_help) === "function")
        page_help = this.options.page_help(this);
      else if (this.options.page_help)
        page_help = this.options.page_help;
      if (page_help)
        this.element.find("#page-help").attr("data-help-slug", page_help);
    }

  , init_page_header: function() {
      var that = this;
      if (this.options.header_view) {
        can.view(this.options.header_view, this.options, function(frag) {
          that.element.find("#page-header").html(frag);
        });
      }
    }

  , init_widget_area: function() {
      this.widget_area_controller = new CMS.Controllers.SortableWidgets(
          this.element.find('.widget-area'), {
              dashboard_controller: this
          });
      if (!this.inner_nav_controller) {
        //  If there is no inner-nav, then ensure widgets are shown
        //  FIXME: This is a workaround because widgets and widget-areas are
        //    hidden, assuming InnerNav controller will show() them
        this.get_active_widget_containers()
          .show()
          .find('.widget').show()
          .find('> section.content').show();
      }
    }

  , init_inner_nav: function() {
      var $internav = this.element.find('.internav');
      if ($internav.length > 0) {
        this.inner_nav_controller = new CMS.Controllers.InnerNav(
            this.element.find('.internav'), {
                dashboard_controller: this
            });
      }
    }

  , init_add_widget: function() {
      this.add_widget_controller = new CMS.Controllers.AddWidget(
          this.element.find('.add-nav-item'), {
              dashboard_controller : this
            //, widget_descriptors : this.options.widget_descriptors
            , menu_tree : this.options.menu_tree
          });
      }

  , init_widget_descriptors: function() {
      var that = this;

      this.options.widget_descriptors = this.options.widget_descriptors || {};

      can.each(this.options.model_descriptors, function(descriptor, key) {
        that.options.widget_descriptors[key] =
          that.make_list_view_descriptor_from_model_descriptor(descriptor);
      });
    }

  , init_menu_tree: function() {
      var that = this
        , menu_tree = { categories: [] };

      can.each(this.options.menu_tree_spec, function(category) {
        var objects = can.map(category.objects, function(widget_name) {
          return that.options.widget_descriptors[widget_name];
        });

        menu_tree.categories.push({
            title: category.title
          , objects: objects
        });
      });

      this.options.menu_tree = menu_tree;
    }

  , init_default_widgets: function() {
      var that = this
        ;

      can.each(this.options.default_widgets, function(name) {
        var descriptor = that.options.widget_descriptors[name]
          ;

        that.add_dashboard_widget_from_descriptor(descriptor);
      });
    }

  , hide_widget_area: function() {
      this.get_active_widget_containers().hide();
    }

  , show_widget_area: function() {
      this.get_active_widget_containers().show();
    }

  , " widgets_updated" : "update_inner_nav"

  , " updateCount": function(el, ev, count) {
      this.inner_nav_controller.update_widget_count($(ev.target), count);
    }

  , " inner_nav_sort_updated": function(el, ev, widget_ids) {
        this.apply_widget_sort(widget_ids);
      }

  , apply_widget_sort: function(widget_ids) {
      var that = this
        ;

      can.each(this.get_active_widget_containers().toArray(), function(elem) {
        $(elem).trigger("apply_widget_sort", [widget_ids])
      });
    }

  , update_inner_nav: function(el, ev, data) {
      if (this.inner_nav_controller) {
        if(data) {
          this.inner_nav_controller.update_widget(data.widget || data, data.index);
        } else {
          this.inner_nav_controller.update_widget_list(
            this.get_active_widget_elements());
        }
      }
    }

  , get_active_widget_containers: function() {
      return this.element.find(".widget-area");
    }

  , get_active_widget_elements: function() {
      return this.element.find("section.widget[id]:not([id=])").toArray();
    }

  , add_widget_from_descriptor: function() {
      var descriptor = {}
        , that = this
        ;

      // Construct the final descriptor from one or more arguments
      can.each(arguments, function(name_or_descriptor) {
        if (typeof(name_or_descriptor) === "string")
          name_or_descriptor =
            that.options.widget_descriptors[name_or_descriptor];
        $.extend(descriptor, name_or_descriptor || {});
      });

      // Create widget in container?
      //return this.options.widget_container[0].add_widget(descriptor);

      if ($('#' + descriptor.controller_options.widget_id + '_widget').length > 0)
        return;

      // FIXME: This should be in some Widget superclass
      if (descriptor.controller_options.widget_guard
          && !descriptor.controller_options.widget_guard())
        return;

      var $element = $("<section class='widget'>")
        , control = new descriptor.controller(
            $element, descriptor.controller_options)
        ;

      // FIXME: This should be elsewhere -- currently required so TreeView can
      //   initialize ObjectNav with counts
      control.prepare();

      // FIXME: Abstraction violation: Sortable/DashboardWidget/ResizableWidget
      //   controllers should maybe handle this?
      var $container = this.get_active_widget_containers().eq(0)
        , $last_widget = $container.find('section.widget').last()
        ;

      if ($last_widget.length > 0)
        $last_widget.after($element);
      else
        $container.append($element);

      $element
        .trigger("sortreceive")
        .trigger("section_created")
        .trigger("widgets_updated", $element);

      return control;
    }

  , add_dashboard_widget_from_descriptor: function(descriptor) {
      return this.add_widget_from_descriptor({
        controller: CMS.Controllers.DashboardWidgets,
        controller_options: $.extend(descriptor, { dashboard_controller: this })
      });
    }

  , add_dashboard_widget_from_name: function(name) {
      var descriptor = this.options.widget_descriptors[name];
      if (!descriptor)
        console.debug("Unknown descriptor: ", name);
      else
        return this.add_dashboard_widget_from_descriptor(descriptor);
    }

  , make_tree_view_descriptor_from_model_descriptor: function(descriptor) {
      return {
        content_controller: CMS.Controllers.TreeView,
        content_controller_options: descriptor,
        content_controller_selector: "ul",
        widget_initial_content: '<ul class="tree-structure new-tree"></ul>',
        widget_id: descriptor.model.table_singular,
        widget_name: descriptor.widget_name || function() {
          var $objectArea = $(".object-area");
          if ( $objectArea.hasClass("dashboard-area") ) {
            return descriptor.model.title_plural;
          } else if (/people/.test(window.location)) {
            return "My " + descriptor.model.title_plural;
          } else {
            return "Mapped " + descriptor.model.title_plural;
          }
        },
        widget_info : descriptor.widget_info,
        widget_icon: descriptor.model.table_singular,
        object_category: descriptor.model.category || descriptor.object_category
      }
    }

  , make_list_view_descriptor_from_model_descriptor: function(descriptor) {
      return {
        content_controller: GGRC.Controllers.ListView,
        content_controller_options: descriptor,
        widget_id: descriptor.model.table_singular,
        widget_name: descriptor.widget_name || function() {
          var $objectArea = $(".object-area");
          if ( $objectArea.hasClass("dashboard-area") ) {
            return descriptor.model.title_plural;
          } else if (/people/.test(window.location)) {
            return "My " + descriptor.model.title_plural;
          } else {
            return "Mapped " + descriptor.model.title_plural;
          }
        },
        widget_info : descriptor.widget_info,
        widget_icon: descriptor.model.table_singular,
        object_category: descriptor.model.category || descriptor.object_category
      }
    }
});


CMS.Controllers.Dashboard("CMS.Controllers.PageObject", {

}, {
    init: function() {
      this.options.model = this.options.instance.constructor;
      this._super();
    }

  , init_page_title: function() {
      // Reset title when page object is modified
      var that = this
        , that_super = this._super
        ;
      this.options.instance.bind("change", function() {
        that_super.apply(that);
      });
      this._super();
    }

  , init_widget_descriptors: function() {
      var that = this;

      this.options.widget_descriptors = this.options.widget_descriptors || {};
    }

  , init_menu_tree: function() {
      var that = this
        , widget_descriptors = this.options.widget_descriptors
        , model_name = this.options.instance.constructor.shortName
        , categories_index = {}
        , menu_tree = { categories : [] }
        ;

      can.each(GGRC.RELATIONSHIP_TYPES[model_name], function(value, key) {
        var related_model = CMS.Models[key]
          , descriptor = widget_descriptors[related_model.table_singular]
          , category = null
          ;

        if (descriptor) {
          category = descriptor.object_category;

          if (!categories_index[category]) {
            categories_index[category] = {
                title : can.map(category.split(" "), can.capitalize).join(" ")
              , objects : []
              };
            menu_tree.categories.push(categories_index[category]);
          }
          categories_index[category].objects.push(descriptor);
        }
      });

      this.options.menu_tree = menu_tree;
    }
});


can.Control("CMS.Controllers.InnerNav", {
  defaults: {
      internav_view : "/static/mustache/dashboard/internav_list.mustache"
    , widget_list : null
    , spinners : {}
    , contexts : null
  }
}, {
    init: function(options) {
      var that = this
        ;

      if (!this.options.widget_list)
        this.options.widget_list = new can.Observe.List([]);

      this.sortable();

      can.route.ready();

      if (!(this.options.contexts instanceof can.Observe))
        this.options.contexts = new can.Observe(this.options.contexts);

      // FIXME: Initialize from `*_widget` hash when hash has no `#!`
      can.route(":path", {});

      can.route.bind("change", function(ev, attr, how, newVal, oldVal) {
        if (attr === "path") {
          if (newVal) {
            that.display_path(newVal);
          }
          else {
            that.display_path('info_widget');
          }
        }
      });

      can.view(this.options.internav_view, this.options, function(frag) {
        function fn() {
          that.element.append(frag);
          if (window.location.hash.substr(0,2) === "#!") {
            can.route.attr('path', window.location.hash.substr(2));
          }
          else {
            can.route.attr('path', window.location.hash.substr(1));
          }
          delete that.delayed_display;
        }
        that.delayed_display = {
          fn : fn
          , timeout : setTimeout(fn, 50)
        };
      });

      this.on();
    }

  , display_path: function(path) {
      var step = path.split("/")[0]
        , rest = path.substr(step.length + 1)
        ;

      // Find and make active the widget specified by `step`
      widget = this.find_widget_by_target("#" + step);
      if (widget) {
        this.set_active_widget(widget);
        return this.display_widget_path(rest);
      }
      else {
        return new $.Deferred().resolve();
      }
    }

  , display_widget_path: function(path) {
      var active_widget_selector = this.options.contexts.active_widget.selector
        , $active_widget = $(active_widget_selector)
        , widget_controller = $active_widget.control()
        ;
      if (widget_controller && widget_controller.display_path) {
        return widget_controller.display_path(path);
      }
      else {
        return new $.Deferred().resolve();
      }
    }

  , sortable: function() {
      return this.element.sortable({
          placeholder: 'drop-placeholder'
        , items : "li"
        , disabled: true
      })
    }

  , " sortupdate": "apply_widget_list_sort"

  , apply_widget_list_sort: function() {
      var widget_ids
        ;

      widget_ids = this.element.find("li > a").map(function() {
        return $(this).attr("href");
      }).toArray();

      this.element.trigger("inner_nav_sort_updated", [widget_ids]);
    }

  , set_active_widget : function(widget) {
    if (typeof widget === 'string') {
      this.options.contexts.attr("active_widget", this.widget_by_selector(widget));
    }
    else {
      this.options.contexts.attr("active_widget", widget);
    }
    this.show_active_widget();
  }

  , show_active_widget : function(selector) {
    var that = this
      , widget = $(selector || this.options.contexts.attr('active_widget').selector);
    if (widget.length) {
      this.options.dashboard_controller.show_widget_area();
      widget.siblings(':visible').hide().end().show();
      $('[href=' + (selector || that.options.contexts.attr('active_widget').selector) + ']')
        .closest('li').addClass('active')
        .siblings().removeClass('active');
    }
  }

  , find_widget_by_target: function(target) {
      var i
        , widget
        ;
      for (i=0; i<this.options.widget_list.length; i++) {
        widget = this.options.widget_list[i];
        if (widget.selector === target)
          return widget;
      }
    }

  , widget_by_selector : function(selector) {
    return $.map(this.options.widget_list, function(widget) {
      return widget.selector === selector ? widget : undefined;
    })[0] || undefined;
  }

  , "{document.body} loaded" : function(body, ev) {
    this.element.sortable("enable");
  }

  , update_widget_list : function(widget_elements) {
      var starttime = Date.now()
        , widget_list = this.options.widget_list.slice(0)
        , that = this
        ;

      can.each(widget_elements, function(widget_element, index) {
        widget_list.splice(
          can.inArray(
            that.update_widget(widget_element, index)
            , widget_list)
          , 1);
      });

      can.each(widget_list, function(widget) {
        that.options.widget_list.splice(can.inArray(widget, that.options.widget_list), 1);
      });
    }

  , update_widget : function(widget_element, index) {
      var $widget = $(widget_element)
        , widget = this.widget_by_selector("#" + $widget.attr("id"))
        , $header = $widget.find(".header h2")
        , icon = $header.find("i").attr("class")
        , menuItem = $header.text().trim()
        , match = menuItem ? menuItem.match(/\s*(\S.*?)\s*(?:\((?:(\d+)|\.*)(\/\d+)?\))?$/) : {}
        , title = match[1]
        , count = match[2] || undefined
        , existing_index
        ;

      index = (index == null) ? this.options.widget_list.length : index;

      if(this.delayed_display) {
        clearTimeout(this.delayed_display.timeout);
        this.delayed_display.timeout = setTimeout(this.delayed_display.fn, 50);
      }


      // If the metadata is unrendered, find it via options
      if (!title) {
        var widget_options = $widget.control("dashboard_widgets").options
          , widget_name = widget_options.widget_name;
        icon = icon || widget_options.widget_icon;
        // Strips html
        title = $('<div>').html(typeof widget_name === 'function' ? widget_name() : (''+widget_name)).text();
      }
      title = title.replace(/^(Mapped|Linked|My)\s+/,'');

      // Only create the observable once, this gets updated elsewhere
      if (!widget) {
        widget = new can.Observe({
            selector: "#" + $widget.attr("id")
          , count: count
          , has_count: count != null
        });
      }
      existing_index = this.options.widget_list.indexOf(widget);

      widget.attr({
        internav_icon: icon
      , internav_display: title
      , spinner : this.options.spinners["#" + $widget.attr("id")]
      });

      if(existing_index !== index) {
        if(existing_index > -1) {
          this.options.widget_list.splice(existing_index, 1);
        }
        if(index >= this.options.widget_list.length) {
          this.options.widget_list.push(widget);
        } else {
          this.options.widget_list.splice(index, 0, widget);
        }
      }
      return widget;
  }

  , update_widget_count : function($el, count) {
      var widget_id = $el.closest('.widget').attr('id')
        , widget = this.widget_by_selector("#" + widget_id)
        ;

      if (widget) {
        widget.attr({
            count: count
          , has_count: true
        });
      }
    }
});

})(this.can, this.can.$);
