# -*- coding: UTF-8 -*-
"""
Issue sheets as PDF with Revit's native PDF exporter (Revit 2022+,
Document.Export(folder, IList<ElementId>, PDFExportOptions)); no virtual
printer. File names follow the office rule (project, sheet, revision, date)
and an emissao.json manifest is written next to the PDFs. The model is not
changed: no transaction. dry_run (default) only plans names and folder.
"""

from pyrevit import routes, DB
import datetime
import io
import json
import logging
import os
import re
from .security import authorize_request
from .utils import normalize_string, element_id_value
from .sheets import name_key, element_name

logger = logging.getLogger(__name__)

MAX_SHEETS = 200
# A real run of separate PDFs is cut to 60 files per call (about 20 s per
# sheet stays inside the wrapper timeout); the dry run can plan 200.
MAX_EXPORT_FILES = 60
MAX_NAME_LENGTH = 120
MAX_PATH_LENGTH = 259
MANIFEST_NAME = u"emissao.json"
HISTORY_NAME = u"emissao_historico.jsonl"
LOCAL_DRIVE_TYPES = ("Fixed", "Removable")
DEFAULT_PATTERN = u"{project_number}_{sheet_number}_{revision}_{date}"
DEFAULT_SUBFOLDER = (u"RevitMCP", u"Emissoes")
TOKENS = ("project_number", "project_name", "sheet_number", "sheet_name", "revision",
          "revision_date", "date")
# Tokens that have no single value in a combined PDF.
SHEET_TOKENS = ("sheet_number", "sheet_name", "revision_date")
COLORS = {"color": "Color", "grayscale": "GrayScale", "black_line": "BlackLine"}
RASTER_QUALITIES = {"low": "Low", "medium": "Medium", "high": "High",
                    "presentation": "Presentation"}
MIN_ZOOM = 10
MAX_ZOOM = 500
HIDE_OPTIONS = (("hide_crop_boundaries", "HideCropBoundaries"),
                ("hide_scope_boxes", "HideScopeBoxes"),
                ("hide_reference_planes", "HideReferencePlane"),
                ("hide_unreferenced_view_tags", "HideUnreferencedViewTags"))
INVALID_NAME_CHARS = u'<>:"/\\|?*'
SEPARATORS = u"_-. "
RESERVED_NAMES = set([u"con", u"prn", u"aux", u"nul"] +
                     [u"com{}".format(n) for n in range(1, 10)] +
                     [u"lpt{}".format(n) for n in range(1, 10)])
# Folders a PDF issue must never be written to (the route adds the
# environment's own SystemRoot/ProgramFiles/ProgramData values).
SYSTEM_FOLDERS = (u"C:\\Windows", u"C:\\Program Files", u"C:\\Program Files (x86)",
                  u"C:\\ProgramData", u"C:\\$Recycle.Bin", u"C:\\System Volume Information",
                  u"C:\\Recovery", u"C:\\Boot")
_TOKEN_RE = re.compile(u"\\{([^{}]*)\\}")
_EMPTY = u"\x00"


# ---- pure helpers (no Revit API; checked by scripts/ironpython_check.py) ----

def _unicode(value):
    if value is None:
        return u""
    return normalize_string(value)


def sanitize_name_part(value):
    """Text safe inside a Windows file name: invalid and control characters
    become "-", runs of spaces collapse, accents are kept."""
    text = _unicode(value)
    out = []
    for char in text:
        if char in INVALID_NAME_CHARS or ord(char) < 32:
            out.append(u"-")
        else:
            out.append(char)
    return u" ".join(u"".join(out).split())


def pattern_error(pattern):
    """Validate a file name pattern. Returns an error message or None."""
    if not isinstance(pattern, basestring) or not pattern.strip():
        return "name_pattern must be a non-empty text"
    tokens = _TOKEN_RE.findall(pattern)
    unknown = [token for token in tokens if token not in TOKENS]
    if unknown:
        return u"Unknown token(s) {} in name_pattern; use {}".format(
            u", ".join(u"{" + t + u"}" for t in unknown),
            u", ".join(u"{" + t + u"}" for t in TOKENS))
    if not tokens:
        return "name_pattern needs at least one {token}"
    literal = _TOKEN_RE.sub(u"", pattern)
    if u"{" in literal or u"}" in literal:
        return "name_pattern has an unbalanced brace"
    for char in literal:
        if char in INVALID_NAME_CHARS or ord(char) < 32:
            return u"name_pattern contains a character not allowed in file names: {!r}".format(char)
    return None


def build_file_name(pattern, values, fallback=u"Emissao"):
    """File name (without .pdf) from a pattern like "{project_number}_{sheet_number}".

    Token values are sanitized; an empty token is dropped together with the
    separators ("_", "-", ".", space) before it, so "P01_{revision}_2026"
    with no revision gives "P01_2026". Reserved Windows names get a "_".
    """
    def replace(match):
        value = sanitize_name_part(values.get(match.group(1)))
        return value if value else _EMPTY

    out = u""
    for char in _TOKEN_RE.sub(replace, pattern):
        if char == _EMPTY:
            out = out.rstrip(SEPARATORS)
        else:
            out += char
    text = out
    text = u" ".join(text.split())
    text = text.strip(SEPARATORS)
    if len(text) > MAX_NAME_LENGTH:
        text = text[:MAX_NAME_LENGTH].rstrip(SEPARATORS)
    if text.lower().endswith(u".pdf"):
        text = text[:-4].rstrip(SEPARATORS)
    if not text:
        text = fallback
    if text.split(u".")[0].lower() in RESERVED_NAMES:
        text = text + u"_"
    return text


def dedupe_names(names):
    """Make names unique ignoring case (Windows): repeats get "_2", "_3"...
    Returns (unique_names, renamed_indexes)."""
    seen = set()
    result = []
    renamed = []
    for index, name in enumerate(names):
        candidate = name
        counter = 2
        while candidate.lower() in seen:
            candidate = u"{}_{}".format(name, counter)
            counter += 1
        if candidate != name:
            renamed.append(index)
        seen.add(candidate.lower())
        result.append(candidate)
    return result, renamed


def _normalized_folder(path):
    return path.replace(u"/", u"\\").rstrip(u"\\")


def _is_under(path, parent):
    path = _normalized_folder(path).lower()
    parent = _normalized_folder(parent).lower()
    return path == parent or path.startswith(parent + u"\\")


def validate_folder(path, blocked=SYSTEM_FOLDERS):
    """Check an output folder given by the user. Must be an absolute path on a
    local drive ("D:\\Projetos\\Emissao"): no UNC, no relative path, no "..",
    not a drive root, not a system folder. Returns (folder, error)."""
    if not isinstance(path, basestring) or not path.strip():
        return None, "folder must be a non-empty text"
    text = _unicode(path)
    if text.startswith(u"\\\\") or text.startswith(u"//"):
        return None, "folder must be on a local drive (network/UNC paths are not allowed)"
    text = text.replace(u"/", u"\\")
    if not re.match(u"^[A-Za-z]:\\\\", text):
        return None, "folder must be an absolute path starting with a drive, like D:\\Projetos\\Emissao"
    segments = [segment for segment in text[3:].split(u"\\") if segment != u""]
    if not segments:
        return None, "folder must not be the root of a drive"
    for segment in segments:
        if segment in (u".", u".."):
            return None, "folder must not contain '.' or '..'"
        if any(char in INVALID_NAME_CHARS or ord(char) < 32 for char in segment):
            return None, u"folder segment '{}' has a character not allowed by Windows".format(segment)
        if segment != segment.rstrip(u". "):
            return None, u"folder segment '{}' ends with a dot or a space".format(segment)
        if u"~" in segment:
            return None, u"folder segment '{}' has '~' (short 8.3 names are not allowed)".format(segment)
        if segment.split(u".")[0].strip().lower() in RESERVED_NAMES:
            return None, u"folder segment '{}' is a reserved Windows device name".format(segment)
    folder = text[0].upper() + u":\\" + u"\\".join(segments)
    for parent in blocked:
        if parent and _is_under(folder, parent):
            return None, u"folder is inside a system folder ({})".format(parent)
    return folder, None


def folder_ancestors(folder):
    """"D:\\a\\b" -> ["D:\\", "D:\\a", "D:\\a\\b"]."""
    folder = _normalized_folder(folder)
    root = folder[:2] + u"\\"
    result = [root]
    current = folder[:2]
    for segment in [s for s in folder[3:].split(u"\\") if s]:
        current = current + u"\\" + segment
        result.append(current)
    return result


def disk_folder_error(folder, drive_type, attributes):
    """Checks that need the disk. drive_type(root) gives a DriveType name
    ("Fixed", "Network"...); attributes(path) gives the FileAttributes text of
    an existing path or None. Rejects mapped/SUBST/network drives and
    junctions or symbolic links on the way. Returns an error or None."""
    kind = drive_type(folder[:3])
    if kind not in LOCAL_DRIVE_TYPES:
        return u"folder must be on a local fixed or removable drive (drive {} is {})".format(
            folder[:2], kind)
    for path in folder_ancestors(folder)[1:]:
        flags = attributes(path)
        if flags is None:
            break
        if u"ReparsePoint" in u"{}".format(flags):
            return u"{} is a junction or symbolic link; use the real folder".format(path)
    return None


def default_folder(documents, project, now):
    """<Documents>\\RevitMCP\\Emissoes\\<project>\\<YYYY-MM-DD_HHMMSS>."""
    project = sanitize_name_part(project).strip(SEPARATORS) or u"Projeto"
    parts = [_normalized_folder(_unicode(documents))] + list(DEFAULT_SUBFOLDER) + \
        [project[:60].rstrip(SEPARATORS), now.strftime("%Y-%m-%d_%H%M%S")]
    return u"\\".join(parts)


def parse_issued_at(text):
    """ISO date-time from a dry run ("2026-10-21T09:05:12"). Returns (datetime, error)."""
    if not isinstance(text, basestring):
        return None, "issued_at must be an ISO date-time like 2026-10-21T09:05:12"
    for layout in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(text.strip(), layout), None
        except ValueError:
            pass
    return None, "issued_at must be an ISO date-time like 2026-10-21T09:05:12"


def backup_name(name, now):
    """Name an existing file is moved to before it is replaced."""
    return u"{}.bak-{}".format(name, now.strftime("%Y%m%d_%H%M%S"))


def paper_format_name(requested, names):
    """ExportPaperFormat name matching requested (any case), else None."""
    wanted = _unicode(requested).lower()
    for name in names:
        if _unicode(name).lower() == wanted:
            return _unicode(name)
    return None


def approval_action(count, folder, replaced):
    """Text of the approval dialog: what is written and where."""
    text = u"Export {} PDF(s) to {}".format(count, folder)
    if replaced:
        text += u" and REPLACE {} existing file(s)".format(replaced)
    return text


def common_revision(revisions):
    """The revision shared by all sheets of a combined PDF, else u""."""
    values = set(_unicode(r) for r in revisions)
    return values.pop() if len(values) == 1 else u""


def _json_string(text):
    out = [u'"']
    for char in text:
        code = ord(char)
        if char == u'"':
            out.append(u'\\"')
        elif char == u"\\":
            out.append(u"\\\\")
        elif char == u"\n":
            out.append(u"\\n")
        elif char == u"\r":
            out.append(u"\\r")
        elif char == u"\t":
            out.append(u"\\t")
        elif code < 32 or code > 126:
            if code > 0xFFFF:  # CPython wide char; IronPython already gives surrogates
                code -= 0x10000
                out.append(u"\\u{:04x}\\u{:04x}".format(0xD800 + (code >> 10),
                                                       0xDC00 + (code & 0x3FF)))
            else:
                out.append(u"\\u{:04x}".format(code))
        else:
            out.append(char)
    out.append(u'"')
    return u"".join(out)


def json_text(value, indent=0, compact=False):
    """ASCII-only JSON (non-ASCII as \\uXXXX). json.dumps fails on accented
    text in IronPython 2. OrderedDict keeps its key order; plain dicts are
    sorted. compact=True gives one line (for the .jsonl history)."""
    if compact:
        pad = end = u""
        open_obj, open_list, comma, close = u"{", u"[", u", ", u""
    else:
        pad = u"  " * (indent + 1)
        end = u"  " * indent
        open_obj, open_list, comma, close = u"{\n", u"[\n", u",\n", u"\n"
    if value is None:
        return u"null"
    if value is True:
        return u"true"
    if value is False:
        return u"false"
    if isinstance(value, basestring):
        return _json_string(_unicode(value) if not isinstance(value, unicode) else value)
    if isinstance(value, (int, long)):
        return u"{}".format(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return u"null"
        return u"{!r}".format(value)
    if isinstance(value, dict):
        items = list(value.items())
        if type(value) is dict:
            items.sort(key=lambda item: u"{}".format(item[0]))
        if not items:
            return u"{}"
        return open_obj + comma.join(
            pad + _json_string(u"{}".format(key)) + u": " + json_text(item, indent + 1, compact)
            for key, item in items) + close + end + u"}"
    if isinstance(value, (list, tuple)):
        if not value:
            return u"[]"
        return open_list + comma.join(pad + json_text(item, indent + 1, compact)
                                      for item in value) + close + end + u"]"
    return _json_string(_unicode(value))


def write_text_file(path, text, append=False):
    """Write (or append) unicode text (ASCII from json_text) to path."""
    with io.open(path, "a" if append else "w", encoding="utf-8", newline=u"\n") as stream:
        stream.write(text)


def _replace_file(source, target):
    """Move source onto target (os.rename cannot overwrite on Windows)."""
    if os.path.exists(target):
        os.remove(target)
    os.rename(source, target)


def free_backup_name(folder, name, now):
    """backup_name(), with "-2", "-3"... when that backup already exists."""
    candidate = backup_name(name, now)
    counter = 2
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = u"{}-{}".format(backup_name(name, now), counter)
        counter += 1
    return candidate


def backup_existing(folder, names, now):
    """Move each existing target aside as <name>.bak-<stamp> before the export.
    All or nothing: if one move fails (file open in a PDF viewer), the moves
    already done are undone. Returns (moved [(name, backup)], error)."""
    moved = []
    for name in names:
        backup = free_backup_name(folder, name, now)
        try:
            os.rename(os.path.join(folder, name), os.path.join(folder, backup))
        except Exception as e:
            restore_backups(folder, moved)
            return [], u"file in use or locked: {} ({})".format(name, e)
        moved.append((name, backup))
    return moved, None


def restore_backups(folder, moved, keep_names=()):
    """Put the backups back, except for names in keep_names (exported fine),
    whose backup is deleted. Returns the names that could not be restored."""
    problems = []
    keep = set(keep_names)
    for name, backup in moved:
        original = os.path.join(folder, name)
        backup_path = os.path.join(folder, backup)
        try:
            if name in keep:
                os.remove(backup_path)
            else:
                _replace_file(backup_path, original)
        except Exception:
            problems.append(name)
    return problems


def validate_options(data):
    """Shape check of the request options. Returns an error message or None."""
    sheets = data.get("sheets")
    sheet_set = data.get("sheet_set")
    if sheets is not None and sheet_set is not None:
        return "Give sheets or sheet_set, not both"
    if sheets is not None:
        if not isinstance(sheets, list) or not sheets:
            return "sheets must be a non-empty list of sheet numbers"
        if len(sheets) > MAX_SHEETS:
            return "At most {} sheets per call, got {}".format(MAX_SHEETS, len(sheets))
        for item in sheets:
            if not isinstance(item, basestring) or not item.strip():
                return "Each sheet must be a non-empty sheet number"
    if sheet_set is not None and (not isinstance(sheet_set, basestring) or not sheet_set.strip()):
        return "sheet_set must be a non-empty name"
    error = pattern_error(data.get("name_pattern", DEFAULT_PATTERN))
    if error:
        return error
    if data.get("color", "color") not in COLORS:
        return "color must be one of {}".format(", ".join(sorted(COLORS)))
    if data.get("raster_quality", "high") not in RASTER_QUALITIES:
        return "raster_quality must be one of {}".format(", ".join(sorted(RASTER_QUALITIES)))
    zoom = data.get("zoom_percent")
    if zoom is not None and (not isinstance(zoom, (int, long)) or isinstance(zoom, bool)
                             or zoom < MIN_ZOOM or zoom > MAX_ZOOM):
        return "zoom_percent must be an integer between {} and {}".format(MIN_ZOOM, MAX_ZOOM)
    paper = data.get("paper_format", "default")
    if not isinstance(paper, basestring) or not re.match(u"^[A-Za-z0-9_]+$", paper):
        return "paper_format must be 'default' or an ExportPaperFormat name like ISO_A1"
    if data.get("issued_at") is not None and parse_issued_at(data["issued_at"])[1]:
        return parse_issued_at(data["issued_at"])[1]
    for key in ["combine", "dry_run", "overwrite"] + [name for name, _ in HIDE_OPTIONS]:
        if key in data and not isinstance(data[key], bool):
            return "{} must be true or false".format(key)
    return None


def plan_files(entries, pattern, project, today, combine):
    """File names for the sheets to export.

    entries: list of dicts with sheet_number, sheet_name, revision, revision_date.
    Returns a list of (file_name_without_extension, [entry indexes]) and the
    indexes renamed to avoid duplicate names.
    """
    base = {"project_number": project.get("number"), "project_name": project.get("name"),
            "date": today}
    if combine:
        values = dict(base)
        values["revision"] = common_revision(e.get("revision") for e in entries)
        for token in SHEET_TOKENS:
            values[token] = u""
        return [(build_file_name(pattern, values), list(range(len(entries))))], []
    names = []
    for entry in entries:
        values = dict(base)
        for token in ("sheet_number", "sheet_name", "revision", "revision_date"):
            values[token] = entry.get(token)
        names.append(build_file_name(pattern, values))
    names, renamed = dedupe_names(names)
    return [(name, [index]) for index, name in enumerate(names)], renamed


def manifest_record(project, user, revit_version, issued_at, folder, combine, files, skipped,
                    errors):
    """Ordered content of emissao.json (pairs keep the key order in json_text)."""
    from collections import OrderedDict
    record = OrderedDict()
    record["project_number"] = project.get("number")
    record["project_name"] = project.get("name")
    record["document"] = project.get("title")
    record["issued_at"] = issued_at
    record["user"] = user
    record["revit_version"] = revit_version
    record["folder"] = folder
    record["combined"] = combine
    record["files"] = files
    record["skipped"] = skipped
    record["errors"] = errors
    return record


# ---- Revit helpers ----

def _parse_body(request):
    """The JSON body as a dict, or None when it is not a JSON object."""
    if not request or not request.data:
        return {}
    data = request.data
    if isinstance(data, basestring):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _parameter_text(element, builtin_name):
    builtin = getattr(DB.BuiltInParameter, builtin_name, None)
    if builtin is None:
        return u""
    try:
        parameter = element.get_Parameter(builtin)
    except Exception:
        return u""
    if parameter is None:
        return u""
    value = parameter.AsString()
    if value is None:
        value = parameter.AsValueString()
    return normalize_string(value) if value else u""


def _project_info(doc):
    info = doc.ProjectInformation
    number = name = u""
    if info is not None:
        try:
            number = normalize_string(info.Number) if info.Number else u""
        except Exception:
            pass
        try:
            name = normalize_string(info.Name) if info.Name else u""
        except Exception:
            pass
    title = normalize_string(doc.Title) if getattr(doc, "Title", None) else u""
    return {"number": number, "name": name, "title": title}


def _sheet_entry(sheet):
    return {"id": element_id_value(sheet.Id),
            "sheet_number": normalize_string(sheet.SheetNumber),
            "sheet_name": element_name(sheet) or u"",
            "revision": _parameter_text(sheet, "SHEET_CURRENT_REVISION"),
            "revision_date": _parameter_text(sheet, "SHEET_CURRENT_REVISION_DATE"),
            "revision_description": _parameter_text(sheet, "SHEET_CURRENT_REVISION_DESCRIPTION")}


def _skip_reason(sheet):
    if getattr(sheet, "IsPlaceholder", False):
        return "placeholder"
    try:
        if not sheet.CanBePrinted:
            return "not_printable"
    except Exception:
        pass
    return None


def _number_key(entry):
    """Natural order: A-2 before A-10."""
    parts = re.split(u"(\\d+)", name_key(entry["sheet_number"]))
    return [int(part) if part.isdigit() else part for part in parts]


def _select_sheets(doc, data):
    """Sheets to export. Returns (sheets, skipped, error_response)."""
    all_sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet))
    skipped = []
    if data.get("sheet_set") is not None:
        wanted = name_key(data["sheet_set"])
        sets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheetSet))
        found = None
        for sheet_set in sets:
            if name_key(element_name(sheet_set)) == wanted:
                found = sheet_set
                break
        if found is None:
            names = sorted(element_name(s) or u"" for s in sets)
            return None, None, routes.make_response(
                data={"error": u"Sheet set '{}' not found".format(normalize_string(data["sheet_set"])),
                      "available_sheet_sets": names[:50]}, status=404)
        candidates = []
        for view in found.Views:
            if isinstance(view, DB.ViewSheet):
                candidates.append(view)
            else:
                skipped.append({"view": element_name(view), "reason": "not_a_sheet"})
    elif data.get("sheets") is not None:
        by_number = {}
        for sheet in all_sheets:
            by_number[name_key(sheet.SheetNumber)] = sheet
        candidates = []
        not_found = []
        seen = set()
        for number in data["sheets"]:
            key = name_key(number)
            if key in seen:
                continue
            seen.add(key)
            if key in by_number:
                candidates.append(by_number[key])
            else:
                not_found.append(normalize_string(number))
        if not_found:
            return None, None, routes.make_response(
                data={"error": "{} sheet number(s) not found; nothing was exported".format(
                    len(not_found)), "not_found": not_found}, status=404)
    else:
        candidates = all_sheets

    sheets = []
    for sheet in candidates:
        reason = _skip_reason(sheet)
        if reason:
            skipped.append({"sheet_number": normalize_string(sheet.SheetNumber),
                            "sheet_name": element_name(sheet), "reason": reason})
        else:
            sheets.append(sheet)
    if data.get("sheets") is None:
        sheets.sort(key=lambda s: _number_key({"sheet_number": s.SheetNumber}))
    if len(sheets) > MAX_SHEETS:
        return None, None, routes.make_response(
            data={"error": "{} sheets selected; at most {} per call. Pass sheets or "
                           "sheet_set to split the issue.".format(len(sheets), MAX_SHEETS)},
            status=400)
    return sheets, skipped, None


def _environment(name):
    try:
        from System import Environment
        return Environment.GetEnvironmentVariable(name)
    except Exception:
        return os.environ.get(name)


def _documents_folder():
    try:
        from System import Environment
        path = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments)
        if path:
            return normalize_string(path)
    except Exception:
        pass
    return os.path.join(_environment("USERPROFILE") or u"C:\\Users\\Public", u"Documents")


def _blocked_folders():
    blocked = list(SYSTEM_FOLDERS)
    for name in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                 "ProgramData"):
        value = _environment(name)
        if value:
            blocked.append(normalize_string(value))
    return blocked


def _user_name():
    try:
        from System import Environment
        return normalize_string(Environment.UserName)
    except Exception:
        return normalize_string(os.environ.get("USERNAME") or os.environ.get("USER") or u"")


def _revit_version(doc):
    try:
        app = doc.Application
        return u"{} ({})".format(normalize_string(app.VersionName), normalize_string(app.VersionBuild))
    except Exception:
        return None




def _dotnet_folder_error(folder):
    """Drive type and junction checks through .NET; skipped where System is
    not available (unit tests on CPython)."""
    try:
        from System.IO import DriveInfo, File, Directory
    except Exception:
        return None

    def drive_type(root):
        try:
            return u"{}".format(DriveInfo(root).DriveType)
        except Exception:
            return u"Unknown"

    def attributes(path):
        if not Directory.Exists(path) and not File.Exists(path):
            return None
        return u"{}".format(File.GetAttributes(path))

    return disk_folder_error(folder, drive_type, attributes)


def _enum_names(enum_type):
    try:
        from System import Enum
        return [u"{}".format(name) for name in Enum.GetNames(enum_type)]
    except Exception:
        return [name for name in dir(enum_type) if not name.startswith("_")]


# Options the export cannot do without: failing to set one stops the call.
CRITICAL_OPTIONS = ("FileName", "Combine", "PaperFormat", "ZoomType", "ZoomPercentage",
                    "ColorDepth")


def _set_option(options, name, value, problems):
    try:
        setattr(options, name, value)
    except Exception as e:
        problems.append((name, u"Could not set PDFExportOptions.{}: {}".format(name, e)))


def build_pdf_options(data, paper_name, file_name):
    """PDFExportOptions for one file. Returns (options, errors, warnings):
    errors for the critical properties, warnings for the others."""
    problems = []
    options = DB.PDFExportOptions()
    _set_option(options, "FileName", file_name, problems)
    _set_option(options, "Combine", True, problems)
    _set_option(options, "PaperFormat", getattr(DB.ExportPaperFormat, paper_name, None)
                if paper_name else None, problems)
    zoom = data.get("zoom_percent")
    if zoom is None:
        _set_option(options, "ZoomType", getattr(DB.ZoomType, "FitToPage", None), problems)
    else:
        _set_option(options, "ZoomType", getattr(DB.ZoomType, "Zoom", None), problems)
        _set_option(options, "ZoomPercentage", int(zoom), problems)
    _set_option(options, "ColorDepth",
                getattr(DB.ColorDepthType, COLORS[data.get("color", "color")], None), problems)
    _set_option(options, "RasterQuality",
                getattr(DB.RasterQualityType,
                        RASTER_QUALITIES[data.get("raster_quality", "high")], None), problems)
    for key, prop in HIDE_OPTIONS:
        _set_option(options, prop, data.get(key, True) is not False, problems)
    _set_option(options, "StopOnError", False, problems)
    try:
        _set_option(options, "PaperPlacement", DB.PaperPlacementType.Center, problems)
    except Exception as e:
        problems.append(("PaperPlacement", u"PaperPlacementType.Center: {}".format(e)))
    errors = [text for name, text in problems if name in CRITICAL_OPTIONS]
    warnings = [text for name, text in problems if name not in CRITICAL_OPTIONS]
    return options, errors, warnings


def _id_list(element_ids):
    from System.Collections.Generic import List
    ids = List[DB.ElementId]()
    for element_id in element_ids:
        ids.Add(element_id)
    return ids


def _file_size(path):
    try:
        return os.path.getsize(path) if os.path.isfile(path) else None
    except Exception:
        return None


def export_sheets(doc, sheets, entries, planned, folder, data, paper_name, warnings):
    """Run the export (one Export call per file so FileName is exact).
    Returns (files, errors)."""
    files = []
    errors = []
    for file_name, indexes in planned:
        numbers = [entries[i]["sheet_number"] for i in indexes]
        options, option_errors, option_warnings = build_pdf_options(data, paper_name, file_name)
        warnings.extend(option_warnings)
        problem = u"; ".join(option_errors) or None
        path = os.path.join(folder, file_name + u".pdf")
        if problem is None:
            try:
                if not doc.Export(folder, _id_list([sheets[i].Id for i in indexes]), options):
                    problem = "Revit did not export the file"
            except Exception as e:
                problem = normalize_string(u"{}".format(e))
        size = _file_size(path)
        if size is None and problem is None:
            problem = "Revit reported success but the file was not found"
        if problem is not None:
            errors.append({"file": file_name + u".pdf", "sheets": numbers, "error": problem})
            continue
        for index in indexes:
            entry = entries[index]
            files.append({"sheet_number": entry["sheet_number"], "sheet_name": entry["sheet_name"],
                          "revision": entry["revision"] or None,
                          "revision_date": entry["revision_date"] or None,
                          "revision_description": entry["revision_description"] or None,
                          "file": file_name + u".pdf", "size_bytes": size})
    return files, errors


def write_manifest(folder, record, now):
    """emissao.json is written to a .tmp file and then moved in place; an
    older emissao.json is kept as emissao.json.bak-<stamp>. One line per run
    is appended to emissao_historico.jsonl. Returns (manifest_path, warnings)."""
    warnings = []
    manifest = os.path.join(folder, MANIFEST_NAME)
    temporary = manifest + u".tmp"
    write_text_file(temporary, json_text(record) + u"\n")
    if os.path.exists(manifest):
        os.rename(manifest, os.path.join(folder, free_backup_name(folder, MANIFEST_NAME, now)))
    os.rename(temporary, manifest)
    try:
        write_text_file(os.path.join(folder, HISTORY_NAME),
                        json_text(record, compact=True) + u"\n", append=True)
    except Exception as e:
        warnings.append(u"Could not append to {}: {}".format(HISTORY_NAME, e))
    return manifest, warnings


def _bad(message, status=400, **extra):
    data = {"error": message}
    data.update(extra)
    return routes.make_response(data=data, status=status)


def register_pdf_export_routes(api):
    """Register PDF export routes with the API"""

    @api.route("/export_sheets_pdf/", methods=["POST"])
    def export_sheets_pdf(doc, request):
        """
        Export sheets to PDF with the native exporter (Revit 2022+).

        Expected JSON payload (all optional):
        {
            "sheets": ["A-101", "A-102"],          // or "sheet_set": "Emissao executivo"
            "folder": "D:\\Projetos\\Casa X\\Emissao",
            "issued_at": "2026-10-21T09:05:12",     // from the dry run: same {date}/folder
            "name_pattern": "{project_number}_{sheet_number}_{revision}_{date}",
            "combine": false,
            "paper_format": "default",              // sheet size, or ExportPaperFormat name
            "zoom_percent": null,                   // null = fit to page
            "color": "color", "raster_quality": "high",
            "hide_crop_boundaries": true, "hide_scope_boxes": true,
            "hide_reference_planes": true, "hide_unreferenced_view_tags": true,
            "overwrite": false,
            "dry_run": true
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return _bad("No active Revit document", 503)
            data = _parse_body(request)
            if data is None:
                return _bad("The request body must be a JSON object")
            error = validate_options(data)
            if error:
                return _bad(error)
            if not hasattr(DB, "PDFExportOptions"):
                return _bad("Native PDF export needs Revit 2022 or newer", 501)
            paper = data.get("paper_format", "default")
            paper_name = paper_format_name(paper, _enum_names(DB.ExportPaperFormat))
            if paper_name is None:
                return _bad(u"Unknown paper_format '{}'; use 'default' (sheet size) or an "
                            u"ExportPaperFormat name like ISO_A1".format(paper))
            dry_run = data.get("dry_run", True) is not False
            combine = data.get("combine", False) is True
            overwrite = data.get("overwrite", False) is True
            pattern = data.get("name_pattern", DEFAULT_PATTERN)

            if data.get("issued_at") is not None:
                now = parse_issued_at(data["issued_at"])[0]
            else:
                now = datetime.datetime.now().replace(microsecond=0)
            issued_at = now.isoformat()
            project = _project_info(doc)
            if data.get("folder") is not None:
                folder, error = validate_folder(data["folder"], _blocked_folders())
            else:
                folder, error = validate_folder(
                    default_folder(_documents_folder(),
                                   project["number"] or project["name"] or project["title"], now),
                    _blocked_folders())
            if error is None:
                error = _dotnet_folder_error(folder)
            if error:
                return _bad(error)
            if os.path.exists(folder) and not os.path.isdir(folder):
                return _bad(u"{} exists and is not a folder".format(folder))

            sheets, skipped, denial = _select_sheets(doc, data)
            if denial is not None:
                return denial
            if not sheets:
                return _bad("No printable sheet to export", skipped=skipped)
            entries = [_sheet_entry(sheet) for sheet in sheets]
            planned, renamed = plan_files(entries, pattern, project,
                                          now.strftime("%Y-%m-%d"), combine)

            # The options must be accepted before anything is written.
            _, option_errors, option_warnings = build_pdf_options(
                data, paper_name, planned[0][0])
            if option_errors:
                return _bad("This Revit version rejected required PDF options; nothing was "
                            "exported", 501, details=option_errors)

            targets = [name + u".pdf" for name, _ in planned]
            too_long = [t for t in targets if len(os.path.join(folder, t)) > MAX_PATH_LENGTH]
            if too_long:
                return _bad("{} path(s) longer than {} characters; use a shorter folder or "
                            "name_pattern".format(len(too_long), MAX_PATH_LENGTH),
                            files=too_long[:20])
            directories = [t for t in targets if os.path.isdir(os.path.join(folder, t))]
            if directories:
                return _bad("{} planned file name(s) are folders; nothing was exported".format(
                    len(directories)), 409, conflicts=directories)
            existing = [t for t in targets if os.path.isfile(os.path.join(folder, t))]

            plan = []
            for name, indexes in planned:
                revision = common_revision(entries[i]["revision"] for i in indexes)
                plan.append({"file": name + u".pdf",
                             "sheets": [entries[i]["sheet_number"] for i in indexes],
                             "revision": revision or None,
                             "exists": name + u".pdf" in existing})
            result = {"folder": folder, "folder_exists": os.path.isdir(folder),
                      "issued_at": issued_at, "combine": combine,
                      "manifest": os.path.join(folder, MANIFEST_NAME), "files": plan,
                      "skipped": skipped}
            if renamed:
                result["renamed_to_avoid_duplicates"] = [plan[i]["file"] for i in renamed]
            if option_warnings:
                result["warnings"] = sorted(set(option_warnings))
            too_many = not combine and len(planned) > MAX_EXPORT_FILES

            if dry_run:
                notes = []
                if existing and not overwrite:
                    notes.append("{} file(s) already exist; the real run needs "
                                 "overwrite=true".format(len(existing)))
                if too_many:
                    notes.append("A real run exports at most {} separate PDFs per call; "
                                 "split the sheets".format(MAX_EXPORT_FILES))
                if notes:
                    result["warning"] = u" ".join(notes)
                result["status"] = "dry_run"
                result["message"] = (
                    "Dry run: {} PDF file(s) for {} sheet(s) planned. Nothing was written; call "
                    "again with dry_run=false and the same folder and issued_at.").format(
                        len(planned), len(sheets))
                return routes.make_response(data=result)

            if too_many:
                return _bad("{} separate PDFs planned; a real run exports at most {} per call. "
                            "Split the sheets or use combine=true.".format(
                                len(planned), MAX_EXPORT_FILES))
            if existing and not overwrite:
                result.update({"status": "error", "existing": existing,
                               "error": "{} file(s) already exist in the folder; nothing was "
                                        "exported. Use overwrite=true or another folder.".format(
                                            len(existing))})
                return routes.make_response(data=result, status=409)

            # The review dialog shows the resolved target, not only the request.
            if isinstance(request.data, dict):
                request.data["_resolved_folder"] = folder
                request.data["_files"] = targets
                if existing:
                    request.data["_will_replace"] = [os.path.join(folder, t) for t in existing]
            denial = authorize_request(
                request, action=approval_action(len(targets), folder, len(existing)))
            if denial is not None:
                return denial

            if not os.path.isdir(folder):
                os.makedirs(folder)
            # A file that appeared while the dialog was open was not approved for replacing.
            appeared = [t for t in targets if t not in existing
                        and os.path.exists(os.path.join(folder, t))]
            if appeared:
                return _bad("{} file(s) appeared in the folder while the approval dialog was "
                            "open; nothing was exported".format(len(appeared)), 409,
                            existing=appeared)
            existing = [t for t in existing if os.path.isfile(os.path.join(folder, t))]
            # Backups are stamped with the real clock: issued_at may repeat.
            stamp = datetime.datetime.now()
            moved, error = backup_existing(folder, existing, stamp)
            if error:
                return _bad(error, 423)

            warnings = list(option_warnings)
            files, errors = export_sheets(doc, sheets, entries, planned, folder, data,
                                          paper_name, warnings)
            exported = set(f["file"] for f in files)
            not_restored = restore_backups(folder, moved, keep_names=exported)
            if not_restored:
                warnings.append(u"Could not restore the previous version of: {}".format(
                    u", ".join(not_restored)))

            manifest = None
            if files:
                record = manifest_record(project, _user_name(), _revit_version(doc), issued_at,
                                         folder, combine, files, skipped, errors)
                manifest, manifest_warnings = write_manifest(folder, record, stamp)
                warnings.extend(manifest_warnings)

            result = {"status": "success" if not errors else "error", "folder": folder,
                      "issued_at": issued_at, "combine": combine, "manifest": manifest,
                      "history": os.path.join(folder, HISTORY_NAME) if files else None,
                      "files": files, "exported": len(exported), "replaced": sorted(
                          name for name, _ in moved if name in exported),
                      "skipped": skipped, "errors": errors}
            if warnings:
                result["warnings"] = sorted(set(warnings))
            if errors:
                result["error"] = ("{} of {} PDF file(s) failed; the others were written and "
                                   "replaced files that failed were restored").format(
                                       len(errors), len(planned))
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in export_sheets_pdf route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
