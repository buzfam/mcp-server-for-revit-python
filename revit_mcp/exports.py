# -*- coding: UTF-8 -*-
"""
Data export routes
Reads schedules and rooms as rows (JSON) or CSV text, without changing the model
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import normalize_string, element_id_value, element_id_from_int

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
FORMATS = ("json", "csv")
SQFT_TO_M2 = 0.09290304
FT_TO_M = 0.3048
CUFT_TO_M3 = 0.028316846592
ROOM_COLUMNS = ["element_id", "number", "name", "level", "area_m2", "perimeter_m",
                "volume_m3", "status"]
ROOM_DETAIL_COLUMNS = ["department", "comments"]


def text(value):
    """Unicode text of a cell or parameter value; None and empty become u""."""
    if value is None:
        return u""
    return normalize_string(value)


def csv_field(value):
    """One CSV field: quoted when it holds a comma, quote or line break."""
    if value is None:
        field = u""
    elif value is True or value is False:
        field = u"true" if value else u"false"
    elif isinstance(value, basestring):
        field = normalize_string(value) if not isinstance(value, unicode) else value
    else:
        field = u"{}".format(value)
    if any(char in field for char in (u",", u'"', u"\n", u"\r")):
        field = u'"' + field.replace(u'"', u'""') + u'"'
    return field


def csv_text(columns, rows):
    """CSV text (header + rows) built with unicode joins: json.dumps and the
    csv module fail on accented text in IronPython 2."""
    lines = [u",".join(csv_field(column) for column in columns)]
    for row in rows:
        lines.append(u",".join(csv_field(value) for value in row))
    return u"\n".join(lines) + u"\n"


def _key(value):
    return u" ".join(text(value).lower().split())


def header_row_index(first_rows, headings):
    """Index of the body row that repeats the column headings, else None."""
    wanted = [_key(heading) for heading in headings]
    if not any(wanted):
        return None
    for index, row in enumerate(first_rows):
        if [_key(cell) for cell in row] == wanted:
            return index
    return None


def visible_columns(hidden_flags, column_count):
    """Indexes of the body columns to read.

    hidden_flags: ScheduleField.IsHidden of every field, in field order. When
    the body has one column per field (hidden ones included), the hidden
    columns are dropped by index; otherwise every column is visible."""
    if column_count == len(hidden_flags) and any(hidden_flags):
        return [index for index, hidden in enumerate(hidden_flags) if not hidden]
    return list(range(column_count))


def room_status(has_location, area):
    """"unplaced" (no location), "not_enclosed" (placed, zero area) or "placed"."""
    if not has_location:
        return "unplaced"
    if not area or area <= 0:
        return "not_enclosed"
    return "placed"


def rounded(value, factor, digits=2):
    """Convert from Revit internal units and round; None stays None."""
    if value is None:
        return None
    return round(value * factor, digits)


def parse_options(data):
    """Common limit/format validation. Returns (limit, format, error)."""
    try:
        limit = int(data.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = 0
    if limit < 1 or limit > MAX_LIMIT:
        return None, None, "limit must be between 1 and {}".format(MAX_LIMIT)
    output = data.get("format", "json")
    output = output.lower() if isinstance(output, basestring) else output
    if output not in FORMATS:
        return None, None, "format must be 'json' or 'csv'"
    return limit, output, None


def table_payload(columns, rows, output):
    if output == "csv":
        return {"csv": csv_text(columns, rows)}
    return {"columns": columns, "rows": rows}


def _parse_body(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _safe_name(element):
    if element is None:
        return None
    try:
        return normalize_string(DB.Element.Name.GetValue(element))
    except Exception:
        try:
            return normalize_string(element.Name)
        except Exception:
            return None


def _parameter_text(element, builtin):
    parameter = element.get_Parameter(builtin)
    if parameter is None:
        return None
    value = parameter.AsString()
    if value is None:
        value = parameter.AsValueString()
    return normalize_string(value) if value else None


# ---- schedules ----

def _schedules(doc):
    result = []
    for schedule in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        try:
            if schedule.IsTemplate or getattr(schedule, "IsTitleblockRevisionSchedule", False):
                continue
        except Exception:
            continue
        result.append(schedule)
    return result


def _find_schedule(doc, schedule_id, schedule_name):
    """Returns (schedule, error, status)."""
    if schedule_id is not None:
        schedule = doc.GetElement(element_id_from_int(int(schedule_id)))
        if schedule is None or not isinstance(schedule, DB.ViewSchedule):
            return None, "Schedule {} not found".format(schedule_id), 404
        return schedule, None, None
    if not isinstance(schedule_name, basestring) or not schedule_name.strip():
        return None, "schedule_name or schedule_id is required", 400
    wanted = _key(schedule_name)
    for schedule in _schedules(doc):
        if _key(_safe_name(schedule)) == wanted:
            return schedule, None, None
    return None, u"Schedule '{}' not found".format(normalize_string(schedule_name)), 404


def _schedule_fields(schedule):
    """[(heading, is_hidden)] of every field, in field (column) order."""
    fields = []
    try:
        definition = schedule.Definition
        for index in range(definition.GetFieldCount()):
            field = definition.GetField(index)
            fields.append((text(field.ColumnHeading), bool(field.IsHidden)))
    except Exception as e:
        logger.warning("Could not read schedule fields: %s", e)
    return fields


def read_schedule(schedule, limit):
    """Body of a schedule as (columns, rows, body_rows, truncated).

    The body section usually starts with the heading row(s); the row that
    repeats the visible field headings becomes the columns. Blank spacer rows
    (grouping) are dropped.
    """
    section = schedule.GetTableData().GetSectionData(DB.SectionType.Body)
    row_count = section.NumberOfRows
    fields = _schedule_fields(schedule)
    indexes = visible_columns([hidden for _, hidden in fields], section.NumberOfColumns)
    headings = [heading for heading, hidden in fields if not hidden]

    def cells(row):
        return [text(schedule.GetCellText(DB.SectionType.Body, row, column))
                for column in indexes]

    first_rows = [cells(row) for row in range(min(row_count, 5))]
    header_index = header_row_index(first_rows, headings)
    if header_index is not None:
        columns = first_rows[header_index]
        start = header_index + 1
    elif len(headings) == len(indexes):
        columns = headings
        start = 0
    else:
        columns = [u"Column {}".format(index + 1) for index in range(len(indexes))]
        start = 0

    rows = []
    truncated = False
    for row in range(start, row_count):
        values = first_rows[row] if row < len(first_rows) else cells(row)
        if not any(values):
            continue
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(values)
    return columns, rows, row_count - start, truncated


# ---- rooms ----

def room_record(doc, room, level_cache, include_details):
    """One room as a dict, in meters / m² / m³."""
    has_location = room.Location is not None
    area = room.Area
    level = None
    level_id = room.LevelId
    if level_id is not None and level_id != DB.ElementId.InvalidElementId:
        key = element_id_value(level_id)
        if key not in level_cache:
            level_cache[key] = _safe_name(doc.GetElement(level_id))
        level = level_cache[key]
    volume = None
    try:
        volume = room.Volume if room.Volume and room.Volume > 0 else None
    except Exception:
        pass
    record = {
        "element_id": element_id_value(room.Id),
        # room.Name raises AttributeError in IronPython: read the parameters.
        "number": _parameter_text(room, DB.BuiltInParameter.ROOM_NUMBER),
        "name": _parameter_text(room, DB.BuiltInParameter.ROOM_NAME),
        "level": level,
        "area_m2": rounded(area, SQFT_TO_M2),
        "perimeter_m": rounded(room.Perimeter, FT_TO_M),
        "volume_m3": rounded(volume, CUFT_TO_M3),
        "status": room_status(has_location, area),
    }
    if include_details:
        record["department"] = _parameter_text(room, DB.BuiltInParameter.ROOM_DEPARTMENT)
        record["comments"] = _parameter_text(
            room, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return record


def summarize_rooms(records, level_filter, include_unplaced, limit):
    """Filter, sort (level, number) and cut the room records.
    Returns (returned, total, counts, total_area_m2, truncated)."""
    wanted = _key(level_filter) if level_filter else None
    matching = []
    for record in records:
        if wanted and _key(record.get("level")) != wanted:
            continue
        if not include_unplaced and record["status"] != "placed":
            continue
        matching.append(record)
    matching.sort(key=lambda r: (_key(r.get("level")), _key(r.get("number"))))
    counts = {"placed": 0, "unplaced": 0, "not_enclosed": 0}
    total_area = 0.0
    for record in matching:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        if record["status"] == "placed" and record["area_m2"]:
            total_area += record["area_m2"]
    return (matching[:limit], len(matching), counts, round(total_area, 2),
            len(matching) > limit)


def register_export_routes(api):
    """Register data export routes with the API"""

    @api.route("/export_schedule/", methods=["POST"])
    def export_schedule(doc, request):
        """
        Read the rows of a schedule.

        Expected JSON payload:
        {
            "schedule_name": "Tabela de portas",   // or "schedule_id": 123
            "limit": 200,
            "format": "json"                       // or "csv"
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            limit, output, error = parse_options(data)
            if error:
                return routes.make_response(data={"error": error}, status=400)

            schedule, error, status = _find_schedule(
                doc, data.get("schedule_id"), data.get("schedule_name"))
            if error:
                response = {"error": error}
                if status == 404:
                    names = sorted(_safe_name(s) for s in _schedules(doc))
                    response["available"] = names[:50]
                    response["available_total"] = len(names)
                return routes.make_response(data=response, status=status)

            columns, rows, body_rows, truncated = read_schedule(schedule, limit)
            result = {
                "status": "success",
                "schedule": {"id": element_id_value(schedule.Id), "name": _safe_name(schedule)},
                "format": output,
                "body_rows": body_rows,
                "returned": len(rows),
                "truncated": truncated,
            }
            result.update(table_payload(columns, rows, output))
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in export_schedule route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)

    @api.route("/export_rooms/", methods=["POST"])
    def export_rooms(doc, request):
        """
        List rooms with number, name, level, area, perimeter and volume.

        Expected JSON payload (all optional):
        {
            "level": "Térreo",
            "include_unplaced": true,
            "include_details": false,
            "limit": 200,
            "format": "json"
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            limit, output, error = parse_options(data)
            if error:
                return routes.make_response(data={"error": error}, status=400)
            include_unplaced = data.get("include_unplaced", True) is not False
            include_details = bool(data.get("include_details", False))
            level_filter = data.get("level")

            level_cache = {}
            records = []
            collector = DB.FilteredElementCollector(doc).OfCategory(
                DB.BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
            for room in collector:
                try:
                    records.append(room_record(doc, room, level_cache, include_details))
                except Exception as e:
                    logger.warning("Could not read room: %s", e)

            returned, total, counts, total_area, truncated = summarize_rooms(
                records, level_filter, include_unplaced, limit)
            columns = ROOM_COLUMNS + (ROOM_DETAIL_COLUMNS if include_details else [])
            result = {
                "status": "success",
                "format": output,
                "total": total,
                "returned": len(returned),
                "truncated": truncated,
                "counts": counts,
                "total_area_m2": total_area,
            }
            if level_filter and total == 0:
                levels = sorted(set(r["level"] for r in records if r.get("level")))
                result["levels_with_rooms"] = levels[:50]
            if output == "csv":
                result["csv"] = csv_text(
                    columns, [[record.get(column) for column in columns] for record in returned])
            else:
                result["rooms"] = returned
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in export_rooms route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
