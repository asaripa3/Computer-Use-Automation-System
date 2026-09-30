// Raw capture, run inside one frame. Gathers facts and decides nothing.
//
// Every naming and role judgement is made in Python (surface/naming.py)
// where it can be unit-tested against plain dictionaries without a browser.
// This script's only job is to report what the DOM says, including the several
// competing things that might serve as a name, and let the caller choose.
//
// Element handles are parked on window.__px so the driver can act on a node by
// index without writing attributes into the application's markup.

(function (maxNodes) {
  const SELECTOR = [
    "input", "select", "textarea", "button",
    "a[href]", "[onclick]", "[role]",
    "th", "td",
    "h1", "h2", "h3", "label", "legend",
  ].join(",");

  window.__px = [];

  const textOf = (el) => {
    if (!el) return "";
    return (el.textContent || "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  };

  const ownTextOf = (el) => {
    let out = "";
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) out += child.nodeValue;
    }
    return out.replace(/ /g, " ").replace(/\s+/g, " ").trim();
  };

  const isVisible = (el, rect) => {
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (parseFloat(style.opacity || "1") === 0) return false;
    if (el.type === "hidden") return false;
    return true;
  };

  // A structural path within the frame: tag plus index among same-tag siblings.
  // The fallback of last resort for identifying a node, and the closest thing
  // the web has to a UIA runtime id.
  const pathOf = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.documentElement) {
      const tag = node.tagName.toLowerCase();
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`${tag}[${index}]`);
      node = node.parentElement;
    }
    return parts.slice(-12);
  };

  // Index every table once so cells can report their coordinates and the
  // header they sit under.
  const tables = Array.from(document.querySelectorAll("table"));
  const tableInfo = tables.map((table) => {
    const rows = Array.from(table.rows);
    let headers = [];
    // A header row is one made of <th>. Failing that, a first row whose cells
    // are all non-numeric is treated as headers -- legacy grids frequently
    // style a plain <tr> as the header instead of using <th>.
    const headerRow = rows.find((r) => r.querySelector("th"));
    if (headerRow) {
      headers = Array.from(headerRow.cells).map(textOf);
    } else if (rows.length > 1) {
      const first = Array.from(rows[0].cells).map(textOf);
      const numeric = first.filter((t) => /^[\d.,$%()-]+$/.test(t)).length;
      if (first.length && numeric === 0) headers = first;
    }
    // Distinguish a real data grid from a table used purely for layout.
    // Legacy pages nest layout tables many deep; reading one as a grid
    // produces rows whose "cells" are the entire rest of the page.
    const cols = rows.length ? Math.max(...rows.map((r) => r.cells.length)) : 0;
    const nested = !!table.querySelector("table");
    const hasTh = !!headerRow;
    // Without <th>, only a table wide and tall enough to have repeating
    // structure is treated as data. That threshold is what keeps two-column
    // label/value panels -- which are layout, however tidy -- out of the grids.
    const inferable = !nested && rows.length >= 3 && cols >= 3 && headers.length > 0
      && headers.every((h) => h && !/^[\d.,$%()-]+$/.test(h));
    const isDataTable = !nested && cols >= 2 && rows.length >= 2 && (hasTh || inferable);

    return {
      table,
      headerRowIndex: headerRow ? rows.indexOf(headerRow) : (headers.length ? 0 : -1),
      headers,
      isDataTable,
    };
  });

  const tableCoordsFor = (el) => {
    const cell = el.closest("td, th");
    if (!cell) return null;
    const row = cell.parentElement;
    if (!row || !row.cells) return null;
    const table = cell.closest("table");
    const index = tables.indexOf(table);
    if (index < 0) return null;

    const info = tableInfo[index];
    const rowIndex = Array.from(table.rows).indexOf(row);
    const colIndex = Array.from(row.cells).indexOf(cell);

    // The neighbouring cell to the left, and the cell directly above. On a
    // table-layout form these are where a field's label actually lives.
    const prevCell = colIndex > 0 ? row.cells[colIndex - 1] : null;
    const rowsArr = Array.from(table.rows);
    const aboveRow = rowIndex > 0 ? rowsArr[rowIndex - 1] : null;
    const aboveCell = aboveRow && aboveRow.cells.length > colIndex ? aboveRow.cells[colIndex] : null;

    return {
      tableIndex: index,
      row: rowIndex,
      column: colIndex,
      columnHeader: info.headers[colIndex] || null,
      rowHeader: row.cells.length ? textOf(row.cells[0]) : null,
      isDataTable: info.isDataTable,
      isHeaderRow: info.headerRowIndex === rowIndex,
      isTh: cell.tagName === "TH",
      cellText: textOf(cell),
      prevCellText: prevCell ? textOf(prevCell) : null,
      aboveCellText: aboveCell ? textOf(aboveCell) : null,
      isOwnCell: cell === el,
    };
  };

  const labelTextFor = (el) => {
    if (el.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (explicit) return textOf(explicit);
    }
    const wrapping = el.closest("label");
    if (wrapping) return textOf(wrapping);
    return null;
  };

  const labelledByTextFor = (el) => {
    const ref = el.getAttribute("aria-labelledby");
    if (!ref) return null;
    const parts = ref.split(/\s+/)
      .map((id) => document.getElementById(id))
      .filter(Boolean)
      .map(textOf);
    return parts.length ? parts.join(" ") : null;
  };

  const records = [];
  let truncated = false;

  for (const el of document.querySelectorAll(SELECTOR)) {
    if (records.length >= maxNodes) { truncated = true; break; }

    const rect = el.getBoundingClientRect();
    const visible = isVisible(el, rect);
    const tag = el.tagName.toLowerCase();

    // Skip structural cells that carry neither text nor a control -- empty
    // spacer cells are the bulk of a table-layout page and carry no meaning.
    if ((tag === "td" || tag === "th")) {
      const hasControl = el.querySelector("input, select, textarea, button, a[href]");
      // A cell containing another table is page chrome, not content: its text
      // is everything nested beneath it.
      if (hasControl || el.querySelector("table") || !textOf(el)) continue;
    }
    if (!visible) continue;

    const coords = tableCoordsFor(el);

    records.push({
      index: records.length,
      tag: tag,
      type: (el.getAttribute("type") || "").toLowerCase(),
      roleAttr: el.getAttribute("role") || "",
      fieldName: el.getAttribute("name") || "",
      elementId: el.id || "",
      href: el.getAttribute("href") || "",
      formName: (el.form && el.form.getAttribute("name")) || "",
      hasClickHandler: el.hasAttribute("onclick"),
      disabled: !!el.disabled,
      checked: el.type === "checkbox" || el.type === "radio" ? !!el.checked : null,
      value: ("value" in el) ? String(el.value ?? "") : null,
      placeholder: el.getAttribute("placeholder") || "",
      title: el.getAttribute("title") || "",
      alt: el.getAttribute("alt") || "",
      ariaLabel: el.getAttribute("aria-label") || "",
      ariaLabelledByText: labelledByTextFor(el),
      labelText: labelTextFor(el),
      ownText: ownTextOf(el),
      text: textOf(el).slice(0, 240),
      options: tag === "select"
        ? Array.from(el.options).map((o) => o.textContent.trim()).slice(0, 40)
        : [],
      selectedOption: tag === "select" && el.selectedIndex >= 0
        ? el.options[el.selectedIndex].textContent.trim()
        : null,
      bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      visible: visible,
      path: pathOf(el),
      table: coords && coords.isOwnCell ? coords : null,
      // A control inside a table cell keeps its neighbours for labelling, but
      // is not itself a cell.
      cellContext: coords && !coords.isOwnCell ? coords : null,
    });

    window.__px.push(el);
  }

  return {
    url: location.href,
    title: document.title,
    records: records,
    truncated: truncated,
  };
})
