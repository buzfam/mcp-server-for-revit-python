# 010 — export_schedule e export_rooms

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (as duas)
- **Rotas:** `POST /export_schedule/` e `POST /export_rooms/` em `revit_mcp/exports.py` (módulo novo)
- **Wrapper:** `tools/export_tools.py` (módulo novo)

## Problema
O usuário precisa levar quantitativos e o quadro de áreas para planilhas,
memoriais e orçamentos. Hoje isso é "Exportar tabela" à mão, um arquivo por
vez. Com estas tools o LLM lê a tabela ou os ambientes direto do modelo, em
JSON (para responder perguntas) ou CSV (para colar numa planilha).

`list_schedules` **não** foi criada: `list_revit_views` já devolve os nomes das
tabelas no grupo `"schedules"`.

## Parâmetros da tool

### export_schedule
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `schedule_name` | str | — | Nome da tabela como no Navegador de Projeto |
| `schedule_id` | int | — | Alternativa a `schedule_name` (um dos dois é obrigatório) |
| `limit` | int | 200 | Máximo de linhas (1–1000) |
| `format` | str | `"json"` | `"json"` ou `"csv"` |

### export_rooms
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `level` | str | todos | Só ambientes desse nível |
| `include_unplaced` | bool | True | Incluir não posicionados e não fechados |
| `include_details` | bool | False | Incluir departamento e comentários |
| `limit` | int | 200 | Máximo de ambientes (1–1000) |
| `format` | str | `"json"` | `"json"` ou `"csv"` |

## Requisição para a rota
```json
{"schedule_name": "Tabela de portas", "limit": 200, "format": "csv"}
```
```json
{"level": "Térreo", "include_unplaced": true, "include_details": false, "limit": 200, "format": "json"}
```

## Resposta
`export_schedule` (JSON):
```json
{
  "status": "success",
  "schedule": {"id": 4567, "name": "Tabela de portas"},
  "format": "json",
  "body_rows": 43,
  "returned": 40,
  "truncated": false,
  "columns": ["Marca", "Largura", "Altura"],
  "rows": [["P1", "80", "210"], ["P2", "90", "210"]]
}
```
Com `format="csv"`, `columns`/`rows` dão lugar a `"csv": "Marca,Largura,Altura\nP1,80,210\n..."`.
Tabela inexistente → 404 com `available` (até 50 nomes) e `available_total`.

`export_rooms` (JSON):
```json
{
  "status": "success",
  "format": "json",
  "total": 12,
  "returned": 12,
  "truncated": false,
  "counts": {"placed": 11, "unplaced": 1, "not_enclosed": 0},
  "total_area_m2": 245.37,
  "rooms": [
    {"element_id": 1001, "number": "101", "name": "Sala", "level": "Térreo",
     "area_m2": 18.5, "perimeter_m": 17.4, "volume_m3": null, "status": "placed"}
  ]
}
```
Com `format="csv"`, `rooms` dá lugar a `csv` (colunas `element_id, number,
name, level, area_m2, perimeter_m, volume_m3, status` + `department, comments`
com `include_details`). Filtro de nível sem resultado → `levels_with_rooms`.

## Regras e casos de borda
- Sem documento aberto → 503. `limit` fora de 1–1000 ou `format` inválido → 400.
- Só leitura: `authorize_request(action=None)`, sem transação.
- Tabela: lê a seção `Body` com `GetCellText`. A linha que repete os
  cabeçalhos dos campos visíveis vira `columns`; se não houver, usa os
  cabeçalhos dos campos (quando o número bate) ou "Column N". Linhas em
  branco (separadores de agrupamento) são descartadas. Os valores são os
  textos formatados, nas unidades do projeto.
- Campos ocultos (`ScheduleField.IsHidden`): se a seção `Body` tiver uma coluna
  por campo (ocultos incluídos), as colunas dos campos ocultos são descartadas
  pelo índice antes de comparar cabeçalhos; se tiver só as visíveis, lê todas.
- Tabelas de revisão de folha e templates são ignorados.
- Ambientes: nome por `ROOM_NAME` e número por `ROOM_NUMBER`
  (`room.Name` dá `AttributeError` no IronPython). Área em m²
  (× 0,09290304), perímetro em m (× 0,3048), volume em m³ só quando o cálculo
  de volume está ligado (senão `null`).
- `status`: `"unplaced"` (sem locação), `"not_enclosed"` (posicionado, área 0;
  inclui redundantes), `"placed"`.
- `counts` e `total_area_m2` (só posicionados) somam todos os ambientes do
  filtro, não só os devolvidos. Ordem: nível, depois número.
- CSV montado com junções de `unicode` (sem `json.dumps` nem módulo `csv`, que
  falham com acentos no IronPython 2); campos com vírgula, aspas ou quebra de
  linha vão entre aspas.

## Revit API
- `DB.ViewSchedule`, `GetTableData().GetSectionData(SectionType.Body)`,
  `TableSectionData.NumberOfRows/NumberOfColumns`, `ViewSchedule.GetCellText`,
  `ScheduleDefinition.GetField(i).ColumnHeading/IsHidden`.
- `BuiltInCategory.OST_Rooms`, `Room.Area/Perimeter/Volume/Location/LevelId`,
  `BuiltInParameter.ROOM_NAME/ROOM_NUMBER/ROOM_DEPARTMENT/ALL_MODEL_INSTANCE_COMMENTS`.

## Testes
- Unitário (wrapper): `tests/unit/test_annotation_export_tools.py`.
- Unitário (rota, API falsa): `tests/unit/test_annotation_export_route_logic.py`
  — CSV com acentos/vírgulas/aspas, opções, leitura de tabela (linha de
  cabeçalho, linhas em branco, limite), conversão de unidades, filtro e
  contagens de ambientes.

## Validação manual no Revit
- [ ] `export_schedule` numa tabela com agrupamento e cabeçalhos em português.
- [ ] CSV com acentos colado no Excel/Sheets.
- [ ] Tabela inexistente devolve `available`.
- [ ] `export_rooms` com ambientes não posicionados e não fechados.
- [ ] Filtro por nível com acento ("Térreo").
- [ ] Áreas conferem com o quadro de áreas do Revit.
- [ ] Tabela com campo oculto no meio: colunas e valores sem o campo oculto, alinhados.
- [ ] Tabela grande (milhares de linhas) responde dentro do timeout de 120 s.
