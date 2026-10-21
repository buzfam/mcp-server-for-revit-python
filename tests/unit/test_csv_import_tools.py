# -*- coding: utf-8 -*-
"""import_parameters_from_csv (019): CSV parsing helpers and the wrapper."""
import json
import os

import pytest

import tools.csv_import_tools as module
from tools.csv_import_tools import (
    MAX_CHANGES,
    annotate_rows,
    argv_is_remote,
    build_changes,
    decode_csv_bytes,
    detect_delimiter,
    element_id_resolver,
    find_column,
    map_columns,
    parse_element_id,
    parse_table,
    read_csv_file,
    register_csv_import_tools,
    resolve_import_path,
    room_number_resolver,
)


@pytest.fixture
def import_folder(tmp_path, monkeypatch):
    folder = tmp_path / "Importar"
    folder.mkdir()
    monkeypatch.setenv("REVIT_MCP_IMPORT_DIR", str(folder))
    monkeypatch.setattr(module, "_remote_mode", False)
    monkeypatch.setattr(module.sys, "argv", ["main.py"])
    return folder


class TestDecodeAndRead:
    def test_utf8_with_bom(self):
        text, encoding = decode_csv_bytes("﻿element_id;Comentários\n".encode("utf-8"))
        assert encoding == "utf-8" and text == "element_id;Comentários\n"

    def test_cp1252_fallback(self):
        text, encoding = decode_csv_bytes("id;Acabamento\n1;Cerâmica\n".encode("cp1252"))
        assert encoding == "cp1252" and "Cerâmica" in text

    def test_utf16_with_bom(self):
        text, encoding = decode_csv_bytes("id\tMarca\n1\tP01\n".encode("utf-16"))
        assert encoding == "utf-16" and text.startswith("id\tMarca")

    def test_reads_csv_file_cp1252(self, import_folder):
        (import_folder / "Planilha.CSV").write_bytes("id;Acabamento\n1;Porcelanato ção\n".encode("cp1252"))
        text, encoding, error = read_csv_file("Planilha.CSV")
        assert error is None and encoding == "cp1252" and "ção" in text

    def test_absolute_path_inside_folder(self, import_folder):
        (import_folder / "sub").mkdir()
        (import_folder / "sub" / "a.csv").write_text("id;x\n", encoding="utf-8")
        assert read_csv_file(str(import_folder / "sub" / "a.csv"))[2] is None

    def test_rejects_other_extensions(self, import_folder):
        (import_folder / "planilha.xlsx").write_bytes(b"x")
        assert ".csv" in read_csv_file("planilha.xlsx")[2]

    def test_missing_file(self, import_folder):
        error = read_csv_file("nada.csv")[2]
        assert "not found" in error and str(import_folder) not in error

    @pytest.mark.parametrize("path", ["../fora.csv", "/etc/passwd.csv", "~/fora.csv"])
    def test_rejects_paths_outside_the_folder(self, import_folder, path):
        assert "inside the import folder" in read_csv_file(path)[2]

    def test_rejects_symlink_escaping_the_folder(self, import_folder, tmp_path):
        outside = tmp_path / "segredo.csv"
        outside.write_text("id;x\n1;y\n", encoding="utf-8")
        os.symlink(outside, import_folder / "link.csv")
        assert "inside the import folder" in read_csv_file("link.csv")[2]

    def test_sibling_folder_with_same_prefix_is_outside(self, import_folder, tmp_path):
        sibling = tmp_path / "Importar2"
        sibling.mkdir()
        (sibling / "a.csv").write_text("id\n", encoding="utf-8")
        assert resolve_import_path(str(sibling / "a.csv"))[1]

    def test_default_folder(self, monkeypatch):
        monkeypatch.delenv("REVIT_MCP_IMPORT_DIR", raising=False)
        assert module.import_dir().endswith(os.path.join("Documents", "RevitMCP", "Importar"))

    def test_too_large(self, import_folder, monkeypatch):
        monkeypatch.setattr(module, "MAX_CSV_BYTES", 10)
        (import_folder / "big.csv").write_bytes(b"x" * 11)
        assert "too large" in read_csv_file("big.csv")[2]

    @pytest.mark.parametrize("argv, remote", [
        ([], False), (["--http"], False), (["--http", "--host", "127.0.0.1"], False),
        (["--http", "--host", "0.0.0.0"], True), (["--host=192.168.0.10", "--http"], True),
        (["--host"], False),
    ])
    def test_argv_is_remote(self, argv, remote):
        assert argv_is_remote(argv) is remote


class TestDelimiter:
    @pytest.mark.parametrize("text, expected", [
        ("element_id;Marca;Comentários\n1;a,b;c\n", ";"),
        ("element_id,Mark,Comments\n1,a,b\n", ","),
        ("element_id\tMark\n1\tP01\n", "\t"),
        ('\n\n"a;b",c,d\n', ","),        # ; inside quotes does not count
        ("single\n1\n", ";"),
        ("id;Altura;Obs\n1;2,40;a\n2;3,10;b\n", ";"),  # decimal commas in a ; file
        ('"element\nid";"Marca, nova"\n1;"a,b"\n', ";"),  # quoted newline in the header
        ("element_id;Mark,x\n1;a,b\n2;c\n", ";"),  # ',' not consistent in the rows
        ("id;Mark\n" + "1;P\n" * 3, ";"),
    ])
    def test_detects(self, text, expected):
        assert detect_delimiter(text) == expected


class TestParseTable:
    def test_bom_header_trimming_and_row_numbers(self):
        text = "﻿  element_id ; Comentários \n101;ok\n\n102;x\n"
        delimiter, header_row, headers, rows = parse_table(text)
        assert delimiter == ";" and header_row == 1
        assert headers == ["element_id", "Comentários"]
        assert [number for number, _ in rows] == [2, 3, 4]
        assert rows[2][1] == ["102", "x"]

    def test_leading_blank_lines_move_the_header(self):
        _, header_row, headers, rows = parse_table("\n;\nid;Mark\n1;P01\n")
        assert header_row == 3 and headers == ["id", "Mark"] and rows == [(4, ["1", "P01"])]

    def test_quoted_fields_with_delimiter_and_newlines(self):
        text = 'id;Comentários;Marca\r\n1;"linha 1\r\nlinha 2; com ;";P01\r\n2;"aspas ""x""";P02\r\n'
        _, _, headers, rows = parse_table(text)
        assert rows[0] == (2, ["1", "linha 1\r\nlinha 2; com ;", "P01"])
        # a quoted newline is still one spreadsheet row
        assert rows[1] == (3, ["2", 'aspas "x"', "P02"])

    def test_explicit_delimiter(self):
        _, _, headers, _ = parse_table("a;b,c\n", ",")
        assert headers == ["a;b", "c"]

    def test_empty_text(self):
        assert parse_table("\n \n")[1] is None


class TestHelpers:
    @pytest.mark.parametrize("value, expected", [
        ("123456", 123456), (" 123456 ", 123456), ("123456.0", 123456),
        ("1 234", 1234), ("abc", None), ("-5", None), ("0", None), ("", None),
        ("12.5", None),
    ])
    def test_parse_element_id(self, value, expected):
        assert parse_element_id(value) == expected

    def test_find_column_is_case_and_space_insensitive(self):
        assert find_column(["Element ID", "Mark"], "element_id") == (0, None)
        assert find_column(["ID", "Mark"], "element_id", ["id"]) == (0, None)
        index, error = find_column(["a", "b"], "element_id")
        assert index is None and "not found" in error and "a, b" in error

    def test_find_column_rejects_duplicates(self):
        assert "2 times" in find_column(["id", "ID", "x"], "id")[1]

    def test_map_columns_default_uses_every_other_header(self):
        mapping, ignored, error = map_columns(["id", "Marca", "", "Comentários"], 0)
        assert error is None and ignored == []
        assert mapping == [(1, "Marca", "Marca"), (3, "Comentários", "Comentários")]

    def test_map_columns_ignore(self):
        mapping, ignored, _ = map_columns(["element_id", "number", "area_m2", "Nome"], 0,
                                          ignore_columns=["Area_M2", "number"])
        assert [m[2] for m in mapping] == ["Nome"] and ignored == ["number", "area_m2"]

    def test_map_columns_explicit(self):
        mapping, ignored, error = map_columns(
            ["element_id", "name", "level"], 0, {"Name": "ROOM_NAME"})
        assert error is None and mapping == [(1, "name", "ROOM_NAME")] and ignored == ["level"]

    @pytest.mark.parametrize("headers, columns, message", [
        (["id", "Marca", "marca"], None, "more than once"),
        (["id"], None, "no parameter columns"),
        (["id", "a"], {"b": "Mark"}, "not found"),
        (["id", "a"], {"id": "Mark"}, "key column"),
        (["id", "a"], {"a": " "}, "parameter name"),
        (["id", "a", "b"], {"a": "Mark", "b": "mark"}, "both go to"),
    ])
    def test_map_columns_errors(self, headers, columns, message):
        assert message in map_columns(headers, 0, columns)[2]

    def test_room_number_resolver(self):
        resolve = room_number_resolver([
            {"element_id": 1, "number": "101", "level": "Térreo", "status": "placed"},
            {"element_id": 2, "number": "102", "level": "Térreo", "status": "placed"},
            {"element_id": 3, "number": "102", "level": "1º Pav", "status": "placed"},
            {"element_id": 4, "number": None, "level": "Térreo", "status": "placed"},
            {"element_id": 5, "number": "B  01", "level": "Térreo", "status": "placed"},
            {"element_id": 6, "number": "B 01", "level": "1º Pav", "status": "unplaced"},
            {"element_id": 7, "number": "C1", "level": "Térreo", "status": "unplaced"},
            {"element_id": 8, "number": "c1", "level": "1º Pav", "status": "not_enclosed"},
        ])
        assert resolve(" 101 ") == (1, None)
        assert resolve("b 01") == (5, None)  # whitespace collapsed; placed room wins
        assert resolve("999") == (None, "no room with this number")
        assert "2 rooms" in resolve("102")[1] and "pass level" in resolve("102")[1]
        assert "2 rooms" in resolve("C1")[1]  # none placed: still ambiguous

    def test_skip_reasons_never_quote_cells(self):
        assert element_id_resolver("segredo") == (
            None, "key is not an element id (whole number)")
        assert "segredo" not in room_number_resolver([])("segredo")[1]

    def test_annotate_rows(self):
        response = {"results": [{"index": 1}, {"index": 9}, "x"], "errors": [{"index": 0}],
                    "warnings": [{"index": 1}], "collapsed": [{"index": 1, "same_as": 0}]}
        annotate_rows(response, [(2, "Marca"), (3, "Marca")])
        assert response["results"][0] == {"index": 1, "row": 3, "column": "Marca"}
        assert "row" not in response["results"][1]
        assert response["errors"][0]["row"] == 2
        assert response["warnings"][0]["row"] == 3
        assert response["collapsed"][0] == {"index": 1, "same_as": 0, "row": 3,
                                            "column": "Marca", "same_as_row": 2}


class TestBuildChanges:
    MAPPING = [(1, "Marca", "Mark"), (2, "Comentários", "Comments")]

    def build(self, rows, **kwargs):
        return build_changes(rows, 0, self.MAPPING, element_id_resolver, **kwargs)

    def test_rows_to_changes(self):
        built = self.build([
            (2, ["10", "P01", " ok "]),
            (3, ["11", "P02"]),              # short row: missing cell is empty
            (4, ["", "", ""]),               # empty row
            (5, ["", "P03", ""]),            # no key
            (6, ["abc", "P04", ""]),         # bad id
            (7, ["10", "P01", ""]),          # same element, same value -> collapsed
            (8, ["12", "", ""]),             # nothing to write
            (9, ["11", "", "novo"]),         # same element, other parameter -> merged
        ])
        assert built["changes"] == [
            {"element_id": 10, "parameter": "Mark", "value": "P01"},
            {"element_id": 10, "parameter": "Comments", "value": "ok"},
            {"element_id": 11, "parameter": "Mark", "value": "P02"},
            {"element_id": 11, "parameter": "Comments", "value": "novo"},
        ]
        assert built["change_rows"] == [(2, "Marca"), (2, "Comentários"), (3, "Marca"),
                                        (9, "Comentários")]
        assert built["empty_rows"] == 1
        assert [s["row"] for s in built["skipped"]] == [5, 6, 8]
        assert "abc" not in built["skipped"][1]["reason"]
        assert built["collapsed"] == [{"row": 7, "column": "Marca", "same_as_row": 2}]
        assert built["conflicts"] == []

    def test_conflicting_duplicates(self):
        built = self.build([(2, ["10", "P01", ""]), (3, ["10", "P02", ""])])
        assert built["conflicts"] == [{"rows": [2, 3], "column": "Marca", "element_id": 10}]
        assert len(built["changes"]) == 1

    def test_set_empty(self):
        built = self.build([(2, ["10", "2,40", ""])], set_empty=True)
        assert [c["value"] for c in built["changes"]] == ["2,40", ""]

    def test_values_are_not_altered(self):
        built = self.build([(2, ["10", "2,40", ""])])
        assert built["changes"][0]["value"] == "2,40"


class TestImportParametersFromCsv:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {
            "status": "dry_run", "message": "Dry run: 2 changes are valid.", "applied": 0,
            "results": [{"index": 0, "element_id": 10, "before": "", "after": "P01"},
                        {"index": 1, "element_id": 11, "before": "", "after": "P02"}]}
        register_csv_import_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["import_parameters_from_csv"]
        self.mock_post = mock_revit_post

    async def test_dry_run_default_payload_and_report(self):
        result = await self.tool(csv_text="element_id;Marca\n10;P01\n\n11;P02\nx;P03\n", ctx=None)
        self.mock_post.assert_called_once_with(
            "/bulk_set_parameters/",
            {"changes": [{"element_id": 10, "parameter": "Marca", "value": "P01"},
                         {"element_id": 11, "parameter": "Marca", "value": "P02"}],
             "dry_run": True, "use_type": False, "normalize_numbers": True},
            None, timeout=120.0)
        assert "ERROR" not in result
        report = json.loads(result[result.index("{"):])["import_report"]
        assert report["rows_read"] == 3 and report["rows_used"] == 2
        assert report["skipped"] == [{"row": 5, "reason": "key is not an element id (whole number)"}]
        assert report["delimiter"] == ";" and report["header_row"] == 1
        assert report["mapped_columns"] == [{"column": "Marca", "parameter": "Marca"}]
        assert '"row": 4' in result  # result index 1 -> spreadsheet row 4

    async def test_real_run_on_types_with_columns(self):
        self.mock_post.return_value = {"status": "success", "applied": 1, "results": []}
        await self.tool(csv_text="id,Tipo,Obs\n10,A,\n", columns={"Tipo": "Type Mark"},
                        use_type=True, dry_run=False, normalize_numbers=False, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload == {"changes": [{"element_id": 10, "parameter": "Type Mark",
                                        "value": "A"}], "dry_run": False, "use_type": True}

    async def test_reads_file(self, import_folder):
        (import_folder / "portas.csv").write_bytes("element_id;Comentários\n10;Revisão\n".encode("cp1252"))
        result = await self.tool(file_path="portas.csv", ctx=None)
        change = self.mock_post.call_args.args[1]["changes"][0]
        assert change == {"element_id": 10, "parameter": "Comentários", "value": "Revisão"}
        assert '"encoding": "cp1252"' in result and '"source": "portas.csv"' in result

    async def test_room_number_match(self):
        self.mock_post.side_effect = [
            {"status": "success", "truncated": False, "rooms": [
                {"element_id": 501, "number": "101", "level": "Térreo", "status": "placed"},
                {"element_id": 502, "number": "102", "level": "Térreo", "status": "placed"}]},
            {"status": "dry_run", "message": "ok", "applied": 0, "results": []},
        ]
        await self.tool(csv_text="Número;Acabamento de piso\n101;Porcelanato\n",
                        match_by="room_number", level="Térreo", ctx=None)
        lookup = self.mock_post.call_args_list[0]
        assert lookup.args[0] == "/export_rooms/"
        assert lookup.args[1] == {"format": "json", "limit": 1000, "include_unplaced": True,
                                  "include_details": False, "level": "Térreo"}
        assert self.mock_post.call_args.args[1]["changes"] == [
            {"element_id": 501, "parameter": "Acabamento de piso", "value": "Porcelanato"}]

    async def test_room_lookup_error_is_returned(self):
        self.mock_post.return_value = "Error: 503 - No active Revit document"
        result = await self.tool(csv_text="number;Nome\n101;Sala\n", match_by="room_number",
                                 ctx=None)
        assert "503" in result and self.mock_post.call_count == 1

    async def test_route_error_string_keeps_report(self):
        self.mock_post.return_value = "Error: 400 - invalid"
        result = await self.tool(csv_text="id;Mark\n10;P01\n", ctx=None)
        assert result.startswith("Error: 400") and "import_report" in result

    async def test_validation_errors_get_row_numbers(self):
        self.mock_post.return_value = {
            "status": "error", "error": "1 of 1 changes are invalid; nothing was applied",
            "applied": 0, "errors": [{"index": 0, "element_id": 10, "error": "not found"}]}
        result = await self.tool(csv_text="id;Mark\n\n10;P01\n", ctx=None)
        assert "'row': 3" in result and "import_report" in result

    async def test_conflicting_rows_are_an_error(self):
        result = await self.tool(csv_text="id;Mark\n10;P01\n10;P02\n", ctx=None)
        assert result.startswith("Error: 1 cells") and '"rows": [\n' in result
        assert "P02" not in result
        self.mock_post.assert_not_called()

    async def test_collapsed_rows_are_reported(self):
        result = await self.tool(csv_text="id;Mark\n10;P01\n10;P01\n", ctx=None)
        assert len(self.mock_post.call_args.args[1]["changes"]) == 1
        assert '"same_as_row": 2' in result

    async def test_file_path_disabled_in_remote_mode(self, import_folder, monkeypatch):
        (import_folder / "a.csv").write_text("id;Mark\n10;P01\n", encoding="utf-8")
        monkeypatch.setattr(module, "_remote_mode", True)
        result = await self.tool(file_path="a.csv", ctx=None)
        assert "disabled" in result
        monkeypatch.setattr(module, "_remote_mode", False)
        monkeypatch.setattr(module.sys, "argv", ["main.py", "--http", "--host", "0.0.0.0"])
        assert "disabled" in await self.tool(file_path="a.csv", ctx=None)
        self.mock_post.assert_not_called()

    async def test_set_remote_mode(self, monkeypatch):
        monkeypatch.setattr(module, "_remote_mode", False)
        module.set_remote_mode(True)
        assert module._remote_mode is True and not module.file_import_allowed()

    async def test_file_outside_folder_is_refused(self, import_folder, tmp_path):
        outside = tmp_path / "x.csv"
        outside.write_text("id;Mark\n10;P01\n", encoding="utf-8")
        result = await self.tool(file_path=str(outside), ctx=None)
        assert "inside the import folder" in result and "P01" not in result
        self.mock_post.assert_not_called()

    async def test_too_many_changes(self):
        rows = "\n".join("{};a;b".format(i) for i in range(1, 252))
        result = await self.tool(csv_text="id;A;B\n" + rows, ctx=None)
        assert result.startswith("Error:") and "at most {}".format(MAX_CHANGES) in result
        assert "250 rows" in result
        self.mock_post.assert_not_called()

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "exactly one"),
        ({"csv_text": "id;a\n1;b", "file_path": "x.csv"}, "exactly one"),
        ({"csv_text": "id;a\n1;b", "match_by": "mark"}, "match_by"),
        ({"csv_text": "id;a\n1;b", "level": "Térreo"}, "level"),
        ({"csv_text": "id;a\n1;b", "delimiter": "|"}, "delimiter"),
        ({"csv_text": "id;a\n1;b", "columns": {}}, "columns"),
        ({"csv_text": "\n\n"}, "empty"),
        ({"csv_text": "Marca;Obs\nP01;x"}, "key column 'element_id' not found"),
        ({"csv_text": "id;a\n1;b", "key_column": "Marca"}, "not found"),
        ({"csv_text": "id;a\n;\n"}, "no changes"),
        ({"csv_text": "id;Mark\nsegredo;P\n"}, "no changes"),
    ])
    async def test_rejects_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_tab_delimiter_name(self):
        await self.tool(csv_text="id\tMark;x\n10\tP;1\n", delimiter="tab", ctx=None)
        assert self.mock_post.call_args.args[1]["changes"][0]["parameter"] == "Mark;x"


def test_main_turns_on_remote_mode_before_serving_on_the_network():
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    remote_branch = next(n for n in ast.walk(main_fn) if isinstance(n, ast.If)
                         and ast.unparse(n.test) == "args.remote")
    calls = [ast.unparse(c.func) for c in ast.walk(remote_branch) if isinstance(c, ast.Call)]
    assert "set_remote_mode" in calls
