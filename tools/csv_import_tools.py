# -*- coding: utf-8 -*-
"""Spreadsheet (CSV) to Revit parameters, on top of bulk_set_parameters.

Runs only on the CPython side: parses the CSV, maps each row to an element
and calls the existing /bulk_set_parameters/ route (and /export_rooms/ to
match rooms by number). There is no route of its own.

file_path only reads .csv files inside the import folder
(REVIT_MCP_IMPORT_DIR, default ~/Documents/RevitMCP/Importar) and is turned
off when the server is reachable from the network (set_remote_mode).
"""

import csv
import io
import json
import os
import re
import sys
from typing import Dict, List, Optional

from mcp.server.fastmcp import Context
from .remote_access import LOOPBACK_HOSTS
from .utils import format_response

MAX_CHANGES = 500  # same cap as bulk_set_parameters
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_SKIPPED_LISTED = 50
MAX_CONFLICTS_LISTED = 20
SNIFF_SAMPLE = 64 * 1024
IMPORT_DIR_ENV = "REVIT_MCP_IMPORT_DIR"
DEFAULT_IMPORT_DIR = os.path.join("~", "Documents", "RevitMCP", "Importar")
ROOM_LOOKUP_LIMIT = 1000  # export_rooms maximum
DELIMITERS = (";", "\t", ",")  # tie order: Brazilian Excel uses ";"
DELIMITER_NAMES = {";": ";", ",": ",", "\t": "\t", "tab": "\t", "\\t": "\t"}
MATCH_BY = ("element_id", "room_number")
DEFAULT_KEY_COLUMNS = {
    "element_id": ["element_id", "element id", "elementid", "id", "id do elemento"],
    "room_number": ["number", "room number", "número", "numero", "nº", "n°", "no"],
}

_QUOTED = re.compile(r'"(?:[^"]|"")*"')
_remote_mode = False


def set_remote_mode(enabled):
    """Called by main.py when the server listens on a network address:
    file_path is then refused (a remote client must not read local files)."""
    global _remote_mode
    _remote_mode = bool(enabled)


def argv_is_remote(argv):
    """True when the command line binds a non-loopback --host (fallback when
    main.py did not call set_remote_mode)."""
    for position, argument in enumerate(argv):
        if argument == "--host" and position + 1 < len(argv):
            host = argv[position + 1]
        elif argument.startswith("--host="):
            host = argument.split("=", 1)[1]
        else:
            continue
        if host not in LOOPBACK_HOSTS:
            return True
    return False


def file_import_allowed():
    return not (_remote_mode or argv_is_remote(sys.argv[1:]))


def import_dir():
    """Absolute, symlink-resolved import folder."""
    folder = os.environ.get(IMPORT_DIR_ENV) or DEFAULT_IMPORT_DIR
    return os.path.realpath(os.path.expanduser(folder))


def _header_key(text):
    """Comparison form of a header: trimmed, lower case, no spaces/_/-."""
    return re.sub(r"[\s_\-]+", "", (text or "").strip().lower())


def decode_csv_bytes(raw):
    """Text of a CSV file. UTF-16 (BOM), UTF-8 (with or without BOM), then
    Windows-1252 (Excel "CSV (separado por vírgulas)" on Windows in Brazil).
    Returns (text, encoding)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def resolve_import_path(file_path, folder=None):
    """Real path of file_path inside the import folder. Relative paths are
    taken from that folder. Returns (path, error)."""
    folder = folder or import_dir()
    candidate = os.path.expanduser(file_path.strip())
    if not os.path.isabs(candidate):
        candidate = os.path.join(folder, candidate)
    path = os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        return None, "file_path must be inside the import folder {} (set {} to change it)".format(
            folder, IMPORT_DIR_ENV)
    if os.path.splitext(path)[1].lower() != ".csv":
        return None, "file_path must point to a .csv file (in Excel: Save As > CSV)"
    return path, None


def read_csv_file(file_path, folder=None):
    """Read a .csv file from the import folder. Returns (text, encoding, error)."""
    path, error = resolve_import_path(file_path, folder)
    if error:
        return None, None, error
    if not os.path.isfile(path):
        return None, None, "file not found in the import folder: {}".format(
            os.path.relpath(path, folder or import_dir()))
    size = os.path.getsize(path)
    if size > MAX_CSV_BYTES:
        return None, None, "file is too large ({} bytes, at most {})".format(size, MAX_CSV_BYTES)
    with open(path, "rb") as handle:
        raw = handle.read()
    text, encoding = decode_csv_bytes(raw)
    return text, encoding, None


def _header_line(text):
    """First non-blank line once quoted parts (which may hold newlines) are removed."""
    for line in _QUOTED.sub("", text).splitlines():
        if line.strip():
            return line
    return ""


def detect_delimiter(text):
    """';', tab or ','. csv.Sniffer on a sample first; its answer is kept only
    when that character really separates the header. Otherwise count them in
    the header (outside quotes); ties go to ';' then tab; default ';'."""
    sample = text[:SNIFF_SAMPLE]
    line = _header_line(sample)
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        sniffed = None
    if sniffed in DELIMITERS and line.count(sniffed):
        return sniffed
    best, best_count = ";", 0
    for candidate in DELIMITERS:
        count = line.count(candidate)
        if count > best_count:
            best, best_count = candidate, count
    return best


def parse_table(text, delimiter=None):
    """Parse CSV text. The header is the first non-empty row.

    Row numbers are the spreadsheet row numbers (1-based, one per CSV record,
    blank lines included), so they match what the user sees in Excel.
    Returns (delimiter, header_row, headers, rows) where rows is a list of
    (row_number, cells); headers are trimmed. header_row is None when the
    text has no non-empty row.
    """
    text = text.lstrip("﻿")
    delimiter = delimiter or detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header_row, headers, rows = None, [], []
    for row_number, cells in enumerate(reader, start=1):
        if header_row is None:
            if any(cell.strip() for cell in cells):
                header_row = row_number
                headers = [cell.strip().lstrip("﻿").strip() for cell in cells]
            continue
        rows.append((row_number, cells))
    return delimiter, header_row, headers, rows


def parse_element_id(text):
    """Integer element id from a cell ('123456', ' 123456 ', '123456.0'), else None."""
    value = (text or "").strip().replace(" ", "").replace(" ", "")
    if value.endswith(".0"):
        value = value[:-2]
    if not value.isdigit():
        return None
    number = int(value)
    return number if number > 0 else None


def find_column(headers, name, aliases=()):
    """Index of the header matching name (trimmed, case/space/_ insensitive),
    then of the first alias. Returns (index, error)."""
    for candidate in [name] + list(aliases):
        wanted = _header_key(candidate)
        found = [i for i, header in enumerate(headers) if _header_key(header) == wanted]
        if len(found) > 1:
            return None, "column '{}' appears {} times in the header".format(
                headers[found[0]], len(found))
        if found:
            return found[0], None
    return None, "column '{}' not found; header has: {}".format(
        name, ", ".join(h for h in headers if h) or "(nothing)")


def map_columns(headers, key_index, columns=None, ignore_columns=None):
    """Which column goes to which parameter.

    columns: {header: parameter}. Default: every other non-empty header is a
    parameter with the same name. Returns (mapping, ignored, error), mapping a
    list of (index, header, parameter) and ignored a list of header texts.
    """
    ignore = set(_header_key(name) for name in (ignore_columns or []))
    mapping, ignored = [], []
    if columns:
        for header, parameter in columns.items():
            if not isinstance(parameter, str) or not parameter.strip():
                return None, None, "columns['{}'] must be a parameter name".format(header)
            index, error = find_column(headers, header)
            if error:
                return None, None, error
            if index == key_index:
                return None, None, "column '{}' is the key column".format(header)
            mapping.append((index, headers[index], parameter.strip()))
        used = set(index for index, _, _ in mapping)
        ignored = [h for i, h in enumerate(headers) if i != key_index and i not in used and h]
    else:
        seen = {}
        for index, header in enumerate(headers):
            if index == key_index or not header:
                continue
            if _header_key(header) in ignore:
                ignored.append(header)
                continue
            key = _header_key(header)
            if key in seen:
                return None, None, (
                    "column '{}' appears more than once in the header; rename it or "
                    "use columns/ignore_columns".format(header))
            seen[key] = index
            mapping.append((index, header, header))
    if not mapping:
        return None, None, "no parameter columns: the CSV needs a key column plus at least one more"
    targets = {}
    for index, header, parameter in mapping:
        if parameter.lower() in targets:
            return None, None, "columns '{}' and '{}' both go to parameter '{}'".format(
                targets[parameter.lower()], header, parameter)
        targets[parameter.lower()] = header
    return mapping, ignored, None


def room_key(text):
    """Room number comparison form, like exports._key: lower case, inner
    whitespace collapsed."""
    return " ".join((text or "").lower().split())


def room_number_index(rooms):
    """{room_key(number): [room, ...]} from export_rooms records."""
    index = {}
    for room in rooms or []:
        number = room_key(room.get("number"))
        if number and room.get("element_id") is not None:
            index.setdefault(number, []).append(room)
    return index


def room_number_resolver(rooms):
    """resolve(text) -> (element_id, reason) for match_by='room_number'.
    When a number is repeated, a single placed room wins over unplaced or
    not enclosed ones."""
    index = room_number_index(rooms)

    def resolve(text):
        matches = index.get(room_key(text), [])
        if len(matches) > 1:
            placed = [room for room in matches if room.get("status") == "placed"]
            if len(placed) == 1:
                matches = placed
        if not matches:
            return None, "no room with this number"
        if len(matches) > 1:
            levels = ", ".join(sorted(set(str(room.get("level")) for room in matches)))
            return None, "room number is used by {} rooms (levels: {}); pass level".format(
                len(matches), levels)
        return matches[0]["element_id"], None
    return resolve


def element_id_resolver(text):
    if parse_element_id(text) is None:
        return None, "key is not an element id (whole number)"
    return parse_element_id(text), None


def build_changes(rows, key_index, mapping, resolve, set_empty=False):
    """Turn rows into bulk_set_parameters changes.

    Several rows for the same element are merged: a repeated value is
    collapsed (noted), different values for the same parameter are a
    conflict. Skip reasons never quote cell text.

    Returns a dict: changes, change_rows ((row, header) per change), skipped
    [{row, reason}], empty_rows, collapsed [{row, column, same_as_row}],
    conflicts [{rows, column, element_id}].
    """
    changes, change_rows, skipped, collapsed, conflicts = [], [], [], [], []
    empty_rows = 0
    written = {}  # (element_id, parameter lower) -> (value, row)
    for row_number, cells in rows:
        if not any(cell.strip() for cell in cells):
            empty_rows += 1
            continue
        key = cells[key_index] if key_index < len(cells) else ""
        if not key.strip():
            skipped.append({"row": row_number, "reason": "key cell is empty"})
            continue
        element_id, reason = resolve(key)
        if element_id is None:
            skipped.append({"row": row_number, "reason": reason})
            continue
        row_changes = []
        for index, header, parameter in mapping:
            value = (cells[index] if index < len(cells) else "").strip()
            if value or set_empty:
                row_changes.append((header, parameter, value))
        if not row_changes:
            skipped.append({"row": row_number, "reason": "all parameter cells are empty"})
            continue
        for header, parameter, value in row_changes:
            target = (element_id, parameter.lower())
            if target in written:
                previous, previous_row = written[target]
                if previous == value:
                    collapsed.append({"row": row_number, "column": header,
                                      "same_as_row": previous_row})
                else:
                    conflicts.append({"rows": [previous_row, row_number], "column": header,
                                      "element_id": element_id})
                continue
            written[target] = (value, row_number)
            changes.append({"element_id": element_id, "parameter": parameter, "value": value})
            change_rows.append((row_number, header))
    return {"changes": changes, "change_rows": change_rows, "skipped": skipped,
            "empty_rows": empty_rows, "collapsed": collapsed, "conflicts": conflicts}


def annotate_rows(response, change_rows):
    """Add the spreadsheet row and column to each indexed result/error."""
    for name in ("results", "errors", "warnings", "collapsed"):
        items = response.get(name)
        if not isinstance(items, list):
            continue
        for item in items:
            index = item.get("index") if isinstance(item, dict) else None
            if isinstance(index, int) and 0 <= index < len(change_rows):
                item["row"], item["column"] = change_rows[index]
            same_as = item.get("same_as") if isinstance(item, dict) else None
            if isinstance(same_as, int) and 0 <= same_as < len(change_rows):
                item["same_as_row"] = change_rows[same_as][0]
    return response


def _with_report(response, report, change_rows):
    if isinstance(response, dict):
        response = annotate_rows(dict(response), change_rows)
        response["import_report"] = report
        return format_response(response)
    return "{}\n\nimport_report: {}".format(
        format_response(response), json.dumps(report, ensure_ascii=False, indent=2))


def _report_text(report):
    return json.dumps(report, ensure_ascii=False, indent=2)


def register_csv_import_tools(mcp, revit_get, revit_post):
    """Register the CSV import tool with the MCP server."""

    @mcp.tool()
    async def import_parameters_from_csv(
        csv_text: Optional[str] = None,
        file_path: Optional[str] = None,
        match_by: str = "element_id",
        key_column: Optional[str] = None,
        columns: Optional[Dict[str, str]] = None,
        ignore_columns: Optional[List[str]] = None,
        delimiter: Optional[str] = None,
        normalize_numbers: bool = True,
        set_empty: bool = False,
        use_type: bool = False,
        level: Optional[str] = None,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Write parameter values from a spreadsheet (CSV text pasted by the user,
        or a .csv file saved from Excel) into Revit elements, all or nothing.

        One row per element, one column per parameter. The first non-empty row
        is the header. This tool builds the changes and runs
        bulk_set_parameters: with dry_run=true (the default) nothing changes
        and you get before/after values to show the user; then call again
        with dry_run=false, which asks for approval in Revit once and is a
        single undo step. At most 500 changes (rows x columns) per call:
        split bigger spreadsheets.

        Matching rows to elements:
            - match_by="element_id" (default): a column with Revit element ids
              (header "element_id", "id" or key_column), e.g. the CSV from
              export_rooms.
            - match_by="room_number": rooms by their number (header "number",
              "Número" or key_column). A placed room wins over unplaced ones
              with the same number; pass level when numbers repeat per level.
        Several rows for the same element are merged; the same parameter with
        different values in two rows is an error.

        Columns: by default every other column is a parameter with the same
        name as its header (localized name like "Comentários", or a builtin
        name like "ALL_MODEL_INSTANCE_COMMENTS"). Use columns to rename
        ({"Acabamento piso": "Acabamento do piso"}) or to pick only some
        columns, and ignore_columns to drop some. Empty cells are skipped
        unless set_empty=true (clears text parameters; ignored with a warning
        for numeric ones).

        Values go as text, in the project's units ("110 cm", "2,40").
        normalize_numbers (default true) lets Revit rewrite numbers of numeric
        parameters to the project's decimal symbol, so "2,40" and "2.40" both
        work; text parameters are never changed.

        file_path is a .csv inside the import folder of the computer running
        this MCP server (REVIT_MCP_IMPORT_DIR, default
        Documents/RevitMCP/Importar); a plain file name is looked up there.
        It is not available when the server is used over the network: paste
        csv_text instead.

        Response: the bulk_set_parameters response (each result/error also has
        the spreadsheet "row" and "column") plus import_report: delimiter,
        encoding, header_row, mapped_columns, ignored_columns, rows_read,
        rows_used, skipped rows with reason, collapsed duplicates, changes.

        Args:
            csv_text: The CSV/table text (header + rows). Use this or file_path.
            file_path: .csv file name or path inside the import folder.
            match_by: "element_id" (default) or "room_number".
            key_column: Header of the key column (default: see above).
            columns: Optional {column header: parameter name}; only these columns are used.
            ignore_columns: Column headers to skip (when columns is not given).
            delimiter: ";", "," or "tab". Default: detected.
            normalize_numbers: Adapt "2,40"/"2.40" to the project's decimal symbol (default true).
            set_empty: Also send empty cells as "" (default false: skip them).
            use_type: Apply to each element's type instead of the instance.
            level: With match_by="room_number", only rooms on this level.
            dry_run: Validate and preview only (default true). Use false to apply.
        """
        if bool(csv_text) == bool(file_path):
            return "Error: pass csv_text or file_path (exactly one)"
        if match_by not in MATCH_BY:
            return "Error: match_by must be 'element_id' or 'room_number'"
        if level and match_by != "room_number":
            return "Error: level is only used with match_by='room_number'"
        if delimiter is not None:
            delimiter = DELIMITER_NAMES.get(delimiter, DELIMITER_NAMES.get(delimiter.lower()))
            if delimiter is None:
                return "Error: delimiter must be ';', ',' or 'tab'"
        if columns is not None and (not isinstance(columns, dict) or not columns):
            return "Error: columns must be a non-empty object {column header: parameter}"

        if file_path:
            if not file_import_allowed():
                return ("Error: file_path is disabled while the server is reachable from the "
                        "network; paste the spreadsheet as csv_text")
            text, encoding, error = read_csv_file(file_path)
            if error:
                return "Error: " + error
            source = os.path.basename(file_path.strip())
        else:
            if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
                return "Error: csv_text is too large (at most {} bytes)".format(MAX_CSV_BYTES)
            text, encoding, source = csv_text, None, "csv_text"

        try:
            delimiter, header_row, headers, rows = parse_table(text, delimiter)
        except csv.Error as e:
            return "Error: could not read the CSV: {}".format(e)
        if header_row is None:
            return "Error: the CSV is empty"

        if key_column:
            key_index, error = find_column(headers, key_column)
        else:
            defaults = DEFAULT_KEY_COLUMNS[match_by]
            key_index, error = find_column(headers, defaults[0], defaults[1:])
        if error:
            return "Error: key " + error
        mapping, ignored, error = map_columns(headers, key_index, columns, ignore_columns)
        if error:
            return "Error: " + error

        rooms_response = None
        if match_by == "room_number":
            if ctx:
                await ctx.info("Reading rooms to match room numbers...")
            lookup = {"format": "json", "limit": ROOM_LOOKUP_LIMIT, "include_unplaced": True,
                      "include_details": False}
            if level:
                lookup["level"] = level
            rooms_response = await revit_post("/export_rooms/", lookup, ctx, timeout=60.0)
            if not isinstance(rooms_response, dict) or rooms_response.get("status") != "success":
                return format_response(rooms_response)
            resolve = room_number_resolver(rooms_response.get("rooms"))
        else:
            resolve = element_id_resolver

        built = build_changes(rows, key_index, mapping, resolve, set_empty)
        changes, skipped = built["changes"], built["skipped"]
        data_rows = len(rows) - built["empty_rows"]
        report = {
            "source": source,
            "encoding": encoding,
            "delimiter": "tab" if delimiter == "\t" else delimiter,
            "header_row": header_row,
            "key_column": headers[key_index],
            "match_by": match_by,
            "mapped_columns": [{"column": header, "parameter": parameter}
                               for _, header, parameter in mapping],
            "ignored_columns": ignored,
            "rows_read": data_rows,
            "rows_used": data_rows - len(skipped),
            "rows_skipped": len(skipped),
            "skipped": skipped[:MAX_SKIPPED_LISTED],
            "changes": len(changes),
        }
        if built["collapsed"]:
            report["collapsed"] = built["collapsed"][:MAX_SKIPPED_LISTED]
        if rooms_response is not None and rooms_response.get("truncated"):
            report["warning"] = "only the first {} rooms were searched; pass level".format(
                ROOM_LOOKUP_LIMIT)

        if built["conflicts"]:
            report["conflicts"] = built["conflicts"][:MAX_CONFLICTS_LISTED]
            return ("Error: {} cells give the same element different values (see conflicts: "
                    "rows and column). Fix the spreadsheet; nothing was sent to Revit."
                    "\n\nimport_report: {}").format(len(built["conflicts"]), _report_text(report))
        if not changes:
            return "Error: no changes to apply\n\nimport_report: {}".format(_report_text(report))
        if len(changes) > MAX_CHANGES:
            return ("Error: the spreadsheet gives {} changes, at most {} per call. Split the "
                    "rows (about {} rows per call) or pick fewer columns with columns."
                    "\n\nimport_report: {}").format(
                len(changes), MAX_CHANGES, max(1, MAX_CHANGES // len(mapping)),
                _report_text(report))

        if ctx:
            await ctx.info("{} {} parameter changes from {} rows...".format(
                "Validating" if dry_run else "Applying", len(changes), report["rows_used"]))
        data = {"changes": changes, "dry_run": dry_run, "use_type": use_type}
        if normalize_numbers:
            data["normalize_numbers"] = True
        response = await revit_post("/bulk_set_parameters/", data, ctx, timeout=120.0)
        return _with_report(response, report, built["change_rows"])
