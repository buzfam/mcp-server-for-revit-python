# -*- coding: utf-8 -*-
"""Issue sheets as PDF with Revit's native exporter (export_sheets_pdf)"""

import json
import re
from datetime import datetime
from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response

MAX_SHEETS = 200
MAX_EXPORT_FILES = 60
DRY_RUN_TIMEOUT = 120.0
UNKNOWN_COUNT_TIMEOUT = 1800.0
DEFAULT_PATTERN = "{project_number}_{sheet_number}_{revision}_{date}"
TOKENS = ("project_number", "project_name", "sheet_number", "sheet_name", "revision",
          "revision_date", "date")
COLORS = ("color", "grayscale", "black_line")
RASTER_QUALITIES = ("low", "medium", "high", "presentation")
INVALID_NAME_CHARS = '<>:"/\\|?*'


def _pattern_error(pattern):
    if not isinstance(pattern, str) or not pattern.strip():
        return "name_pattern must be a non-empty text"
    tokens = re.findall(r"\{([^{}]*)\}", pattern)
    unknown = [token for token in tokens if token not in TOKENS]
    if unknown:
        return "unknown token(s) {} in name_pattern; use {}".format(
            ", ".join("{" + t + "}" for t in unknown), ", ".join("{" + t + "}" for t in TOKENS))
    if not tokens:
        return "name_pattern needs at least one {token}"
    literal = re.sub(r"\{[^{}]*\}", "", pattern)
    if "{" in literal or "}" in literal:
        return "name_pattern has an unbalanced brace"
    if any(char in INVALID_NAME_CHARS or ord(char) < 32 for char in literal):
        return "name_pattern contains a character not allowed in file names"
    return None


def _folder_error(folder):
    if not isinstance(folder, str) or not folder.strip():
        return "folder must be a non-empty text"
    if folder.startswith(("\\\\", "//")):
        return "folder must be on a local drive (network/UNC paths are not allowed)"
    if not re.match(r"^[A-Za-z]:[\\/]", folder):
        return "folder must be an absolute path starting with a drive, like D:\\Projetos\\Emissao"
    segments = [s for s in re.split(r"[\\/]", folder[3:]) if s]
    if not segments:
        return "folder must not be the root of a drive"
    if any(segment in (".", "..") for segment in segments):
        return "folder must not contain '.' or '..'"
    return None


def _issued_at_error(value):
    for layout in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            datetime.strptime(value.strip(), layout)
            return None
        except (ValueError, AttributeError):
            pass
    return "issued_at must be the ISO date-time returned by the dry run, like 2026-10-21T09:05:12"


def export_timeout(dry_run, sheet_count):
    """Seconds to wait for Revit: about 20 s per sheet, at least 600; 1800
    when the number of sheets is not known (sheet set or all sheets)."""
    if dry_run:
        return DRY_RUN_TIMEOUT
    if sheet_count is None:
        return UNKNOWN_COUNT_TIMEOUT
    return max(600.0, 20.0 * sheet_count)


def register_pdf_export_tools(mcp, revit_get, revit_post):
    """Register the PDF issue tool with the MCP server."""

    @mcp.tool()
    async def export_sheets_pdf(
        sheets: Optional[List[str]] = None,
        sheet_set: Optional[str] = None,
        folder: Optional[str] = None,
        issued_at: Optional[str] = None,
        name_pattern: str = DEFAULT_PATTERN,
        combine: bool = False,
        paper_format: str = "default",
        zoom_percent: Optional[int] = None,
        color: str = "color",
        raster_quality: str = "high",
        hide_crop_boundaries: bool = True,
        hide_scope_boxes: bool = True,
        hide_reference_planes: bool = True,
        hide_unreferenced_view_tags: bool = True,
        overwrite: bool = False,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Issue Revit sheets as PDF files with Revit's native PDF exporter
        (Revit 2022+), named by the office rule, plus an emissao.json manifest
        (project, date, user, Revit version and, per sheet, number, name,
        current revision, revision date, file and size) for the issue history.

        Workflow: call with dry_run=true (the default) first. It returns the
        folder, issued_at and the planned file names, marks files that
        already exist and lists skipped sheets (placeholder, not printable),
        writing nothing. Show that plan to the user, then call again with
        dry_run=false passing the SAME folder and issued_at returned by the
        dry run, so {date} and the default folder do not change. The real run
        asks for approval in Revit (the dialog shows the folder, the files and
        any file it will replace). The model is not changed.

        A real run exports at most 60 separate PDFs per call (combine=true is
        one file for up to 200 sheets); split bigger issues by sheets. If the
        call times out, Revit may still be exporting: check the folder and
        emissao.json (or run the dry run again and look at "exists") before
        retrying.

        Replacing (overwrite=true): existing PDFs are first renamed to
        <name>.pdf.bak-<stamp>; the backup is deleted only when the new PDF was
        written, and restored when that sheet failed. A file open in a PDF
        viewer stops the call with nothing changed. The previous emissao.json
        is kept as a .bak, and each run is appended to emissao_historico.jsonl.

        Sheets: give sheet numbers, OR the name of a saved sheet set
        (Imprimir > Configurar conjunto de vistas/folhas), OR nothing for every
        non-placeholder sheet (at most 200 per call). Unknown sheet numbers
        stop the call. check_sheets is a good step before issuing.

        Folder: default is Documents\\RevitMCP\\Emissoes\\<project>\\<YYYY-MM-DD_HHMMSS>.
        A given folder must be an absolute path on a local fixed or removable
        drive (no network or mapped drives, no "..", no "~" short names, no
        junctions, no system folders); it is created when missing.

        File names: name_pattern tokens {project_number} {project_name}
        {sheet_number} {sheet_name} {revision} {revision_date} {date} (today,
        YYYY-MM-DD). Characters Windows forbids become "-", accents are kept,
        an empty token is dropped with the separator before it, and repeated
        names get "_2", "_3". Revision is the sheet's current revision. With
        combine=true one PDF holds all sheets in the given order (or by sheet
        number); sheet tokens are empty and {revision} is filled only when all
        sheets share it.

        Args:
            sheets: Sheet numbers to export, e.g. ["A-101", "A-102"] (1-200).
            sheet_set: Name of a saved Revit sheet set (instead of sheets).
            folder: Output folder (absolute local path). Default: per-project folder in Documents.
                For the real run, pass the folder returned by the dry run.
            issued_at: Issue date-time returned by the dry run (ISO); keeps {date} and the default folder identical.
            name_pattern: File name pattern (without .pdf).
            combine: One PDF with all sheets instead of one PDF per sheet.
            paper_format: "default" (each sheet's own size) or a Revit ExportPaperFormat name like "ISO_A1".
            zoom_percent: Print zoom 10-500; omit for fit to page.
            color: "color", "grayscale" or "black_line".
            raster_quality: "low", "medium", "high" or "presentation".
            hide_crop_boundaries: Hide crop regions (default true).
            hide_scope_boxes: Hide scope boxes (default true).
            hide_reference_planes: Hide reference planes (default true).
            hide_unreferenced_view_tags: Hide unreferenced view tags (default true).
            overwrite: Replace files that already exist in the folder (default false: the real run stops).
            dry_run: Plan only (default true). Use false to write the PDFs.
        """
        if sheets is not None and sheet_set is not None:
            return "Error: give sheets or sheet_set, not both"
        if sheets is not None:
            if not sheets:
                return "Error: sheets must contain at least one sheet number"
            if len(sheets) > MAX_SHEETS:
                return "Error: at most {} sheets per call, got {}".format(MAX_SHEETS, len(sheets))
            if any(not isinstance(s, str) or not s.strip() for s in sheets):
                return "Error: each sheet must be a non-empty sheet number"
        if sheet_set is not None and not sheet_set.strip():
            return "Error: sheet_set must be a non-empty name"
        if folder is not None:
            error = _folder_error(folder)
            if error:
                return "Error: " + error
        if issued_at is not None:
            error = _issued_at_error(issued_at)
            if error:
                return "Error: " + error
        if not dry_run and not combine and sheets is not None and len(sheets) > MAX_EXPORT_FILES:
            return "Error: a real run exports at most {} separate PDFs per call, got {} sheets; " \
                "split them or use combine=true".format(MAX_EXPORT_FILES, len(sheets))
        error = _pattern_error(name_pattern)
        if error:
            return "Error: " + error
        if color not in COLORS:
            return "Error: color must be one of {}".format(", ".join(COLORS))
        if raster_quality not in RASTER_QUALITIES:
            return "Error: raster_quality must be one of {}".format(", ".join(RASTER_QUALITIES))
        if zoom_percent is not None and not 10 <= zoom_percent <= 500:
            return "Error: zoom_percent must be between 10 and 500"
        if not isinstance(paper_format, str) or not re.match(r"^[A-Za-z0-9_]+$", paper_format):
            return "Error: paper_format must be 'default' or an ExportPaperFormat name like ISO_A1"

        data = {"name_pattern": name_pattern, "combine": combine, "paper_format": paper_format,
                "color": color, "raster_quality": raster_quality,
                "hide_crop_boundaries": hide_crop_boundaries,
                "hide_scope_boxes": hide_scope_boxes,
                "hide_reference_planes": hide_reference_planes,
                "hide_unreferenced_view_tags": hide_unreferenced_view_tags,
                "overwrite": overwrite, "dry_run": dry_run}
        if sheets is not None:
            data["sheets"] = sheets
        if sheet_set is not None:
            data["sheet_set"] = sheet_set
        if folder is not None:
            data["folder"] = folder
        if zoom_percent is not None:
            data["zoom_percent"] = zoom_percent
        if issued_at is not None:
            data["issued_at"] = issued_at.strip()

        if ctx:
            await ctx.info("{} PDF issue...".format("Planning" if dry_run else "Exporting"))
        response = await revit_post("/export_sheets_pdf/", data, ctx, timeout=export_timeout(dry_run, len(sheets) if sheets is not None else None))
        # A partial export still wrote files: keep the whole report visible.
        if isinstance(response, dict) and response.get("status") == "error" and \
                response.get("files"):
            return json.dumps(response, indent=2, ensure_ascii=False)
        return format_response(response)
