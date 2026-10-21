# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/pdf_export.py (017) against a fake Revit API:
file names by the office rule, folder validation, the ASCII-safe manifest
writer, sheet selection (numbers, sheet set, all), dry run and the real export
with the manifest. The module is IronPython code; py2 builtins are injected
for CPython."""
import builtins
import datetime
import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.sheets", "revit_mcp.pdf_export")


class Eid(int):
    @property
    def Value(self):
        return int(self)


class Param:
    def __init__(self, value):
        self.value = value

    def AsString(self):
        return self.value

    def AsValueString(self):
        return self.value


class Element:
    def __init__(self, doc, name):
        self.doc, self._name = doc, name
        self.Id = doc.add(self)


class ViewSheet(Element):
    def __init__(self, doc, number, name, revision=None, revision_date=None,
                 placeholder=False, printable=True):
        super().__init__(doc, name)
        self.SheetNumber, self.IsPlaceholder, self.CanBePrinted = number, placeholder, printable
        self.params = {"SHEET_CURRENT_REVISION": Param(revision),
                       "SHEET_CURRENT_REVISION_DATE": Param(revision_date)}

    def get_Parameter(self, builtin):
        return self.params.get(builtin)


class View(Element):
    pass


class ViewSheetSet(Element):
    def __init__(self, doc, name, views):
        super().__init__(doc, name)
        self.Views = views


class Collector:
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        return [e for e in self.doc.elements if type(e) is cls]


class PDFExportOptions:
    reject = set()  # property names this "Revit version" refuses

    def __setattr__(self, name, value):
        if name in PDFExportOptions.reject:
            raise AttributeError(name)
        object.__setattr__(self, name, value)


class Doc:
    def __init__(self, folder_fails=()):
        self.elements = []
        self.exports = []
        self.fails = set(folder_fails)
        self.Title = u"Casa Árvore"
        self.ProjectInformation = SimpleNamespace(Number=u"P-07", Name=u"Casa Árvore")
        self.Application = SimpleNamespace(VersionName=u"Autodesk Revit 2024",
                                           VersionBuild=u"24.2.0")

    def add(self, element):
        self.elements.append(element)
        return Eid(len(self.elements))

    def Export(self, folder, ids, options):
        self.exports.append((folder, list(ids), options))
        if options.FileName in self.fails:
            return False
        with open(os.path.join(folder, options.FileName + ".pdf"), "wb") as stream:
            stream.write(b"%PDF-1.7" + b"x" * len(ids))
        return True


def _name(element):
    return element._name


@pytest.fixture
def pdf(monkeypatch):
    DB = SimpleNamespace(
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=_name)),
        ViewSheet=ViewSheet, ViewSheetSet=ViewSheetSet, FilteredElementCollector=Collector,
        BuiltInParameter=SimpleNamespace(
            SHEET_CURRENT_REVISION="SHEET_CURRENT_REVISION",
            SHEET_CURRENT_REVISION_DATE="SHEET_CURRENT_REVISION_DATE",
            SHEET_CURRENT_REVISION_DESCRIPTION="SHEET_CURRENT_REVISION_DESCRIPTION"),
        PDFExportOptions=PDFExportOptions,
        ColorDepthType=SimpleNamespace(Color="Color", GrayScale="GrayScale", BlackLine="BlackLine"),
        RasterQualityType=SimpleNamespace(Low="Low", Medium="Medium", High="High",
                                          Presentation="Presentation"),
        ExportPaperFormat=SimpleNamespace(Default="Default", ISO_A1="ISO_A1"),
        ZoomType=SimpleNamespace(FitToPage="FitToPage", Zoom="Zoom"),
        ElementId=Eid)
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(
        make_response=lambda data, status=200: SimpleNamespace(data=data, status=status)),
        forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name, value in (("long", int), ("basestring", str), ("unicode", str)):
        monkeypatch.setattr(builtins, name, value, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    PDFExportOptions.reject = set()
    yield importlib.import_module("revit_mcp.pdf_export")
    for name in MODULES:
        sys.modules.pop(name, None)


# ---- pure helpers ----

def test_file_name_from_pattern(pdf):
    values = {"project_number": u"P-07", "sheet_number": u"A-101", "revision": u"R02",
              "date": u"2026-10-21", "sheet_name": u"Planta: Térreo / Layout?"}
    assert pdf.build_file_name(pdf.DEFAULT_PATTERN, values) == u"P-07_A-101_R02_2026-10-21"
    assert pdf.build_file_name(u"{sheet_number} - {sheet_name}", values) == \
        u"A-101 - Planta- Térreo - Layout"  # trailing separators trimmed
    # Empty tokens are dropped with the separator before them (or after, at the start).
    no_revision = dict(values, revision=None)
    assert pdf.build_file_name(pdf.DEFAULT_PATTERN, no_revision) == u"P-07_A-101_2026-10-21"
    assert pdf.build_file_name(pdf.DEFAULT_PATTERN, dict(values, project_number=u"")) == \
        u"A-101_R02_2026-10-21"
    assert pdf.build_file_name(u"{revision}", {}) == u"Emissao"
    assert pdf.build_file_name(u"{sheet_number}", {"sheet_number": u"CON"}) == u"CON_"
    assert pdf.build_file_name(u"{sheet_number}.pdf", {"sheet_number": u"A1"}) == u"A1"
    assert len(pdf.build_file_name(u"{sheet_name}", {"sheet_name": u"x" * 300})) == 120


def test_pattern_validation(pdf):
    assert pdf.pattern_error(pdf.DEFAULT_PATTERN) is None
    assert u"{sheet}" in pdf.pattern_error(u"{sheet}_{date}")
    assert "at least one" in pdf.pattern_error(u"fixo")
    assert "not allowed" in pdf.pattern_error(u"{sheet_number}/{date}")
    assert "unbalanced" in pdf.pattern_error(u"{date}}")


def test_dedupe_names_ignores_case(pdf):
    assert pdf.dedupe_names([u"A", u"a", u"B", u"A"]) == ([u"A", u"a_2", u"B", u"A_3"], [1, 3])


@pytest.mark.parametrize("path, expected", [
    (u"D:\\Projetos\\Casa Árvore\\Emissão\\", u"D:\\Projetos\\Casa Árvore\\Emissão"),
    (u"d:/Projetos/Emissao", u"D:\\Projetos\\Emissao"),
    (u"C:\\Users\\carol\\Documents\\Emissao", u"C:\\Users\\carol\\Documents\\Emissao"),
])
def test_valid_folders(pdf, path, expected):
    assert pdf.validate_folder(path) == (expected, None)


@pytest.mark.parametrize("path, message", [
    (u"\\\\servidor\\share", "UNC"), (u"//servidor/share", "UNC"),
    (u"Emissao", "absolute"), (u"\\Emissao", "absolute"), (u"D:Emissao", "absolute"),
    (u"D:\\", "root"), (u"D:\\a\\..\\b", "'..'"), (u"D:\\a\\.\\b", "'..'"),
    (u"D:\\a?b", "not allowed"), (u"D:\\pasta \\b", "dot or a space"),
    (u"c:\\windows\\Temp", "system folder"), (u"C:\\Program Files (x86)\\x", "system folder"),
    (u"C:\\ProgramData", "system folder"), (u"", "non-empty"), (None, "non-empty"),
    (u"D:\\PROJET~1\\x", "8.3"), (u"D:\\Projetos\\con.txt", "reserved"),
    (u"D:\\Projetos\\LPT1", "reserved"),
])
def test_rejected_folders(pdf, path, message):
    folder, error = pdf.validate_folder(path)
    assert folder is None and message in error, error


def test_default_folder(pdf):
    now = datetime.datetime(2026, 10, 21, 9, 5)
    assert pdf.default_folder(u"C:\\Users\\carol\\Documents", u"P-07: Casa", now) == \
        u"C:\\Users\\carol\\Documents\\RevitMCP\\Emissoes\\P-07- Casa\\2026-10-21_090500"
    assert pdf.default_folder(u"C:\\Docs\\", u"", now).endswith(u"\\Projeto\\2026-10-21_090500")


def test_json_text_is_ascii_and_valid(pdf):
    from collections import OrderedDict
    value = OrderedDict([("z", u"Térreo \"A\"\\ção\n\t😀"), ("a", [1, 2.5, True, None, {}]),
                         ("b", {"y": u"é", "x": []}), ("n", -3)])
    text = pdf.json_text(value)
    text.encode("ascii")  # nothing outside ASCII
    assert json.loads(text) == {"z": u"Térreo \"A\"\\ção\n\t😀", "a": [1, 2.5, True, None, {}],
                                "b": {"y": u"é", "x": []}, "n": -3}
    assert text.index('"z"') < text.index('"a"')  # OrderedDict keeps its order
    assert "\\u00e9" in text and "\\ud83d\\ude00" in text
    assert pdf.json_text(float("nan")) == u"null"


def test_write_text_file(pdf, tmp_path):
    path = str(tmp_path / u"emissão.json")
    pdf.write_text_file(path, pdf.json_text({"nome": u"Árvore"}) + u"\n")
    assert json.loads(open(path, encoding="ascii").read()) == {"nome": u"Árvore"}


def test_plan_files_single_and_combined(pdf):
    entries = [{"sheet_number": u"A-101", "sheet_name": u"Planta", "revision": u"R01"},
               {"sheet_number": u"A-101", "sheet_name": u"Cópia", "revision": u"R01"},
               {"sheet_number": u"A-102", "sheet_name": u"Cortes", "revision": u""}]
    project = {"number": u"P-07", "name": u"Casa"}
    planned, renamed = pdf.plan_files(entries, pdf.DEFAULT_PATTERN, project, u"2026-10-21", False)
    assert [name for name, _ in planned] == [u"P-07_A-101_R01_2026-10-21",
                                             u"P-07_A-101_R01_2026-10-21_2",
                                             u"P-07_A-102_2026-10-21"]
    assert renamed == [1]
    planned, _ = pdf.plan_files(entries, pdf.DEFAULT_PATTERN, project, u"2026-10-21", True)
    assert planned == [(u"P-07_2026-10-21", [0, 1, 2])]  # revisions differ: dropped
    planned, _ = pdf.plan_files(entries[:2], pdf.DEFAULT_PATTERN, project, u"2026-10-21", True)
    assert planned[0][0] == u"P-07_R01_2026-10-21"


def test_validate_options(pdf):
    assert pdf.validate_options({}) is None
    assert "not both" in pdf.validate_options({"sheets": ["A"], "sheet_set": "x"})
    assert "non-empty" in pdf.validate_options({"sheets": []})
    assert "at most" in pdf.validate_options({"sheets": ["A"] * 201}).lower()
    assert "color" in pdf.validate_options({"color": "cor"})
    assert "zoom" in pdf.validate_options({"zoom_percent": True})
    assert "combine" in pdf.validate_options({"combine": "sim"})


# ---- route ----

@pytest.fixture
def doc():
    doc = Doc()
    doc.a102 = ViewSheet(doc, u"A-102", u"Cortes", revision=u"R01", revision_date=u"21/10/26")
    doc.a101 = ViewSheet(doc, u"A-101", u"Planta Térreo", revision=u"R02",
                         revision_date=u"20/10/26")
    doc.a10 = ViewSheet(doc, u"A-10", u"Situação")
    ViewSheet(doc, u"A-900", u"Reservada", placeholder=True)
    ViewSheet(doc, u"A-901", u"Não imprime", printable=False)
    doc.plan = View(doc, u"Térreo")
    ViewSheetSet(doc, u"Emissão executivo", [doc.a101, doc.plan])
    return doc


@pytest.fixture
def route(pdf, monkeypatch, tmp_path):
    handlers = {}

    class Api:
        def route(self, path, methods):
            def decorator(function):
                handlers[path] = function
                return function
            return decorator

    approvals = []
    previews = []

    def authorize(request, action=None):
        approvals.append(action)
        if action:
            previews.append(dict(request.data))
        return None

    out = tmp_path / "out"
    real_validate = pdf.validate_folder

    def validate(path, blocked=pdf.SYSTEM_FOLDERS):
        # POSIX temp folders stand in for D:\ in these tests.
        if isinstance(path, str) and path.startswith(str(tmp_path)):
            return path, None
        return real_validate(path, blocked)

    monkeypatch.setattr(pdf, "authorize_request", authorize)
    monkeypatch.setattr(pdf, "validate_folder", validate)
    monkeypatch.setattr(pdf, "_id_list", list)
    monkeypatch.setattr(pdf, "default_folder", lambda documents, project, now: str(out))
    monkeypatch.setattr(pdf, "_user_name", lambda: u"carol")
    pdf.register_pdf_export_routes(Api())

    def call(doc, body=None, **data):
        return handlers["/export_sheets_pdf/"](
            doc, SimpleNamespace(data=data if body is None else body))
    call.approvals, call.previews, call.out = approvals, previews, out
    return call


def test_dry_run_plans_without_writing(route, doc):
    response = route(doc)
    data = response.data
    assert response.status == 200 and data["status"] == "dry_run"
    today = datetime.date.today().isoformat()
    assert [f["file"] for f in data["files"]] == [
        u"P-07_A-10_{}.pdf".format(today), u"P-07_A-101_R02_{}.pdf".format(today),
        u"P-07_A-102_R01_{}.pdf".format(today)]  # natural order, no revision dropped
    assert [f["sheets"] for f in data["files"]] == [[u"A-10"], [u"A-101"], [u"A-102"]]
    assert {s["reason"] for s in data["skipped"]} == {"placeholder", "not_printable"}
    assert data["folder"] == str(route.out) and not route.out.exists()
    assert data["issued_at"].startswith(today)
    assert doc.exports == [] and route.approvals == [None]


def test_issued_at_from_the_dry_run_fixes_the_date(route, doc):
    data = route(doc, sheets=[u"A-101"], issued_at=u"2026-01-02T03:04:05").data
    assert data["issued_at"] == u"2026-01-02T03:04:05"
    assert data["files"][0]["file"] == u"P-07_A-101_R02_2026-01-02.pdf"
    assert route(doc, issued_at=u"ontem").status == 400


def test_real_export_writes_pdfs_manifest_and_history(route, doc):
    response = route(doc, sheets=[u"a-101", u"A-102"], name_pattern=u"{sheet_number} {sheet_name}",
                     dry_run=False)
    data = response.data
    assert data["status"] == "success", data
    assert route.approvals == [None, u"Export 2 PDF(s) to {}".format(route.out)]
    preview = route.previews[0]
    assert preview["_resolved_folder"] == str(route.out)
    assert preview["_files"] == [u"A-101 Planta Térreo.pdf", u"A-102 Cortes.pdf"]
    assert "_will_replace" not in preview
    assert [f["file"] for f in data["files"]] == [u"A-101 Planta Térreo.pdf", u"A-102 Cortes.pdf"]
    assert data["files"][0]["revision"] == u"R02" and data["files"][0]["size_bytes"] == 9
    options = doc.exports[0][2]
    assert options.Combine is True and options.ColorDepth == "Color"
    assert options.RasterQuality == "High" and options.PaperFormat == "Default"
    assert options.ZoomType == "FitToPage" and options.HideScopeBoxes is True
    assert options.StopOnError is False
    assert any("PaperPlacement" in w for w in data["warnings"])  # not in the fake API
    manifest = json.loads(open(data["manifest"], encoding="ascii").read())
    assert manifest["project_number"] == u"P-07" and manifest["user"] == u"carol"
    assert manifest["revit_version"] == u"Autodesk Revit 2024 (24.2.0)"
    assert manifest["issued_at"] == data["issued_at"]
    assert [f["sheet_name"] for f in manifest["files"]] == [u"Planta Térreo", u"Cortes"]
    assert not os.path.exists(data["manifest"] + ".tmp")
    # A second run keeps the previous manifest and adds a history line.
    second = route(doc, sheets=[u"A-10"], name_pattern=u"{sheet_number}", dry_run=False,
                   issued_at=u"2026-01-02T03:04:05").data
    names = sorted(os.listdir(route.out))
    assert len([n for n in names if n.startswith(u"emissao.json.bak-")]) == 1
    lines = open(second["history"], encoding="ascii").read().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["files"][0]["file"] == u"A-10.pdf"


def test_combined_export_is_one_call(route, doc):
    data = route(doc, combine=True, zoom_percent=100, color="grayscale", dry_run=False).data
    assert len(doc.exports) == 1 and len(doc.exports[0][1]) == 3
    assert data["exported"] == 1 and len(data["files"]) == 3
    assert doc.exports[0][2].ZoomType == "Zoom" and doc.exports[0][2].ZoomPercentage == 100
    assert doc.exports[0][2].ColorDepth == "GrayScale"


def test_sheet_set_skips_views_that_are_not_sheets(route, doc):
    data = route(doc, sheet_set=u"emissão EXECUTIVO").data
    assert [f["sheets"] for f in data["files"]] == [[u"A-101"]]
    assert data["skipped"] == [{"view": u"Térreo", "reason": "not_a_sheet"}]
    missing = route(doc, sheet_set=u"Outro")
    assert missing.status == 404 and missing.data["available_sheet_sets"] == [u"Emissão executivo"]


def test_unknown_sheet_stops_the_call(route, doc):
    response = route(doc, sheets=[u"A-101", u"Z-1"], dry_run=False)
    assert response.status == 404 and response.data["not_found"] == [u"Z-1"]
    assert doc.exports == []


def test_existing_files_need_overwrite(route, doc):
    route.out.mkdir()
    (route.out / u"A-101.pdf").write_bytes(b"old")
    dry = route(doc, sheets=[u"A-101"], name_pattern=u"{sheet_number}").data
    assert dry["files"][0]["exists"] is True and "overwrite" in dry["warning"]
    blocked = route(doc, sheets=[u"A-101"], name_pattern=u"{sheet_number}", dry_run=False)
    assert blocked.status == 409 and doc.exports == []
    done = route(doc, sheets=[u"A-101"], name_pattern=u"{sheet_number}", dry_run=False,
                 overwrite=True, issued_at=u"2026-01-02T03:04:05").data
    assert done["status"] == "success" and done["files"][0]["size_bytes"] == 9
    assert done["replaced"] == [u"A-101.pdf"]
    assert route.approvals[-1] == u"Export 1 PDF(s) to {} and REPLACE 1 existing file(s)".format(
        route.out)
    assert route.previews[-1]["_will_replace"] == [os.path.join(str(route.out), u"A-101.pdf")]
    assert not [n for n in os.listdir(route.out) if ".pdf.bak-" in n]  # backup removed


def test_failed_replacement_restores_the_previous_pdf(route, doc):
    route.out.mkdir()
    (route.out / u"A-101.pdf").write_bytes(b"old101")
    (route.out / u"A-102.pdf").write_bytes(b"old102")
    doc.fails.add(u"A-102")
    data = route(doc, sheets=[u"A-101", u"A-102"], name_pattern=u"{sheet_number}",
                 dry_run=False, overwrite=True).data
    assert data["status"] == "error" and data["errors"][0]["file"] == u"A-102.pdf"
    assert (route.out / u"A-102.pdf").read_bytes() == b"old102"   # restored
    assert (route.out / u"A-101.pdf").read_bytes().startswith(b"%PDF")
    assert not [n for n in os.listdir(route.out) if ".pdf.bak-" in n]
    assert json.loads(open(data["manifest"]).read())["errors"][0]["sheets"] == [u"A-102"]


def test_locked_file_stops_with_nothing_changed(route, doc, pdf, monkeypatch):
    route.out.mkdir()
    (route.out / u"A-101.pdf").write_bytes(b"old101")
    (route.out / u"A-102.pdf").write_bytes(b"old102")
    real_rename = os.rename

    def rename(source, target):
        if source.endswith(u"A-102.pdf"):
            raise OSError("in use")
        real_rename(source, target)
    monkeypatch.setattr(pdf.os, "rename", rename)
    response = route(doc, sheets=[u"A-101", u"A-102"], name_pattern=u"{sheet_number}",
                     dry_run=False, overwrite=True)
    assert response.status == 423 and "A-102.pdf" in response.data["error"]
    assert sorted(os.listdir(route.out)) == [u"A-101.pdf", u"A-102.pdf"]
    assert (route.out / u"A-101.pdf").read_bytes() == b"old101" and doc.exports == []


def test_file_appearing_during_the_dialog_is_not_replaced(route, doc, pdf, monkeypatch):
    def authorize(request, action=None):
        if action:
            route.out.mkdir()
            (route.out / u"A-101.pdf").write_bytes(b"new")
        return None
    monkeypatch.setattr(pdf, "authorize_request", authorize)
    pdf.register_pdf_export_routes(SimpleNamespace(route=lambda path, methods: (
        lambda function: setattr(route, "handler", function) or function)))
    response = route.handler(doc, SimpleNamespace(data={"sheets": [u"A-101"], "dry_run": False,
                                                       "overwrite": True,
                                                       "name_pattern": u"{sheet_number}"}))
    assert response.status == 409 and doc.exports == []
    assert (route.out / u"A-101.pdf").read_bytes() == b"new"


def test_planned_name_that_is_a_folder_is_a_conflict(route, doc):
    (route.out / u"A-101.pdf").mkdir(parents=True)
    response = route(doc, sheets=[u"A-101"], name_pattern=u"{sheet_number}")
    assert response.status == 409 and response.data["conflicts"] == [u"A-101.pdf"]


def test_real_run_of_separate_pdfs_is_capped(route, doc, pdf, monkeypatch):
    monkeypatch.setattr(pdf, "MAX_EXPORT_FILES", 2)
    assert "split" in route(doc).data["warning"]          # dry run still plans
    assert route(doc, dry_run=False).status == 400
    assert route(doc, dry_run=False, combine=True).data["status"] == "success"


def test_required_option_rejected_stops_before_writing(route, doc):
    PDFExportOptions.reject = {"PaperFormat"}
    response = route(doc, dry_run=False)
    assert response.status == 501 and "PaperFormat" in response.data["details"][0]
    assert doc.exports == [] and route.approvals == [None]
    PDFExportOptions.reject = {"HideScopeBoxes"}
    data = route(doc, dry_run=False).data
    assert data["status"] == "success" and any("HideScopeBoxes" in w for w in data["warnings"])


def test_bad_input_is_rejected(route, doc):
    assert route(doc, folder=u"\\\\nas\\emissao").status == 400
    assert route(doc, paper_format=u"ISO_A9").status == 400
    assert route(doc, paper_format=u"iso_a1").data["status"] == "dry_run"
    assert route(doc, body="[1, 2]").status == 400
    assert route(doc, body=["x"]).status == 400


def test_no_document(route):
    assert route(None).status == 503


# ---- disk checks (the .NET part is injected) ----

def test_disk_folder_error(pdf):
    def kinds(kind):
        return lambda root: kind
    no_links = lambda path: None if path.count("\\") > 2 else "Directory"
    assert pdf.disk_folder_error(u"D:\\a\\b\\c", kinds("Fixed"), no_links) is None
    assert pdf.disk_folder_error(u"E:\\a", kinds("Removable"), no_links) is None
    assert "Network" in pdf.disk_folder_error(u"Z:\\a", kinds("Network"), no_links)
    assert "NoRootDirectory" in pdf.disk_folder_error(u"S:\\a", kinds("NoRootDirectory"), no_links)
    junction = lambda path: "Directory, ReparsePoint" if path == u"D:\\a" else "Directory"
    assert u"D:\\a is a junction" in pdf.disk_folder_error(u"D:\\a\\b", kinds("Fixed"), junction)
    assert pdf.folder_ancestors(u"D:\\a\\b") == [u"D:\\", u"D:\\a", u"D:\\a\\b"]


def test_small_helpers(pdf):
    now = datetime.datetime(2026, 1, 2, 3, 4, 5)
    assert pdf.backup_name(u"A.pdf", now) == u"A.pdf.bak-20260102_030405"
    assert pdf.parse_issued_at(u"2026-01-02T03:04:05") == (now, None)
    assert pdf.parse_issued_at(u"02/01/2026")[1]
    assert pdf.paper_format_name(u"iso_a1", [u"Default", u"ISO_A1"]) == u"ISO_A1"
    assert pdf.paper_format_name(u"A9", [u"Default"]) is None
    assert pdf.approval_action(3, u"D:\\x", 0) == u"Export 3 PDF(s) to D:\\x"
    assert pdf.approval_action(3, u"D:\\x", 2).endswith(u"and REPLACE 2 existing file(s)")
    line = pdf.json_text({"a": [1, {"b": u"é"}]}, compact=True)
    assert "\n" not in line and json.loads(line) == {"a": [1, {"b": u"é"}]}


def test_backup_and_restore(pdf, tmp_path):
    now = datetime.datetime(2026, 1, 2, 3, 4, 5)
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / "b.pdf").write_bytes(b"b")
    moved, error = pdf.backup_existing(str(tmp_path), [u"a.pdf", u"b.pdf"], now)
    assert error is None and sorted(os.listdir(tmp_path)) == [
        u"a.pdf.bak-20260102_030405", u"b.pdf.bak-20260102_030405"]
    (tmp_path / "a.pdf").write_bytes(b"new")
    (tmp_path / "b.pdf").write_bytes(b"partial")
    assert pdf.restore_backups(str(tmp_path), moved, keep_names=[u"a.pdf"]) == []
    assert sorted(os.listdir(tmp_path)) == [u"a.pdf", u"b.pdf"]
    assert (tmp_path / "a.pdf").read_bytes() == b"new" and (tmp_path / "b.pdf").read_bytes() == b"b"
    (tmp_path / u"a.pdf.bak-20260102_030405").write_bytes(b"x")
    assert pdf.free_backup_name(str(tmp_path), u"a.pdf", now) == u"a.pdf.bak-20260102_030405-2"


def test_same_issued_at_twice_keeps_both_manifests(route, doc):
    for _ in range(2):
        data = route(doc, sheets=[u"A-10"], name_pattern=u"{sheet_number}", dry_run=False,
                     overwrite=True, issued_at=u"2026-01-02T03:04:05").data
        assert data["status"] == "success", data
    names = os.listdir(route.out)
    assert len([n for n in names if n.startswith(u"emissao.json.bak-")]) == 1
    assert len(open(data["history"]).read().splitlines()) == 2


# ---- entry point shared with the ribbon button (Conferir > Emitir PDF) ----

def test_issue_pdfs_runs_without_a_request(route, pdf, doc):
    calls = []

    def approve(action, preview):
        calls.append((action, preview))
        return None

    plan = pdf.issue_pdfs(doc, {"sheets": [u"A-101"], "name_pattern": u"{sheet_number}"}, approve)
    assert plan.data["status"] == "dry_run" and calls == [] and doc.exports == []
    done = pdf.issue_pdfs(doc, {"sheets": [u"A-101"], "name_pattern": u"{sheet_number}",
                                "dry_run": False, "folder": plan.data["folder"],
                                "issued_at": plan.data["issued_at"]}, approve)
    assert done.data["status"] == "success" and done.data["exported"] == 1
    assert calls[0][0] == u"Export 1 PDF(s) to {}".format(route.out)
    assert calls[0][1]["_files"] == [u"A-101.pdf"] and "_will_replace" not in calls[0][1]


def test_issue_pdfs_stops_when_approve_refuses(route, pdf, doc):
    route.out.mkdir()
    (route.out / u"A-101.pdf").write_bytes(b"old")
    stop = SimpleNamespace(data={"error": "recusado"}, status=403)
    seen = []

    def approve(action, preview):
        seen.append(preview["_will_replace"])
        return stop

    response = pdf.issue_pdfs(doc, {"sheets": [u"A-101"], "name_pattern": u"{sheet_number}",
                                    "dry_run": False, "overwrite": True}, approve)
    assert response is stop and doc.exports == []
    assert seen == [[os.path.join(str(route.out), u"A-101.pdf")]]
    assert (route.out / u"A-101.pdf").read_bytes() == b"old"
    assert route.approvals == []  # the MCP approval dialog is not involved
