(function() {
  // A tile carrying data-doctype is hidden unless the current user can read
  // that doctype, so nobody is offered a link that 403s (Warehouse and Farm
  // need stock / upande_core roles an irrigation operator may lack). frappe.boot
  // already lists readable doctypes for this session, so this costs nothing.
  var canRead = (window.frappe && frappe.boot && frappe.boot.user && frappe.boot.user.can_read) || [];
  root_element.querySelectorAll('.irn-tile[data-doctype]').forEach(function(tile) {
    var doctype = tile.getAttribute('data-doctype');
    if (doctype && canRead.indexOf(doctype) < 0) {
      tile.classList.add('irn-hide');
    }
  });

  // Don't leave a heading above a group whose tiles are all hidden.
  root_element.querySelectorAll('.irn-grid').forEach(function(grid) {
    if (grid.querySelectorAll('.irn-tile:not(.irn-hide)').length === 0) {
      grid.classList.add('irn-hide');
      var title = grid.previousElementSibling;
      if (title && title.classList.contains('irn-title')) {
        title.classList.add('irn-hide');
      }
    }
  });
})();
