# 007 — create_sheets e place_views_on_sheets

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogos: `"Create sheets"` e `"Place views on sheets"`); `dry_run` é leitura
- **Rotas:** `POST /create_sheets/` e `POST /place_views_on_sheets/` em `revit_mcp/sheets.py` (módulo novo)
- **Wrapper:** `tools/sheet_tools.py` (módulo novo)
- **Depende de:** `list_revit_views` (o LLM descobre os nomes das vistas antes de posicionar)

## Problema
Montar o conjunto de folhas de um projeto é repetitivo: criar dezenas de
folhas com número e nome, escolher o carimbo e arrastar cada vista para a sua
folha. Com estas tools o usuário cola a lista de folhas (ou descreve a regra) e
o LLM cria tudo e posiciona as vistas de uma vez, com **um diálogo de
aprovação por lote** e um único Ctrl+Z.

## Parâmetros da tool

### create_sheets
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sheets` | list[{number:str, name:str}] | — | Folhas a criar (1–200) |
| `title_block` | str \| null | null | Carimbo como `"Família : Tipo"`, ou só o nome do tipo ou da família (sem diferenciar maiúsculas). Padrão: o primeiro carimbo carregado em ordem alfabética; sem carimbo carregado, a folha é criada sem carimbo |
| `dry_run` | bool | True | Só valida e mostra o resultado, sem alterar |

### place_views_on_sheets
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `placements` | list[{sheet_number:str, view?:str, view_id?:int, x?:float, y?:float}] | — | Posicionamentos (1–200). `view` (nome) **ou** `view_id`; `x`/`y` em metros (os dois ou nenhum) |
| `dry_run` | bool | True | Só valida e mostra as posições, sem alterar |

`x`/`y` é o centro da vista na folha, em metros a partir da origem da folha.
Sem `x`/`y`: centro do carimbo da folha (caixa do carimbo); sem carimbo, centro
do contorno da folha (`ViewSheet.Outline`).

## Requisição para a rota
```json
{"dry_run": true, "title_block": "Carimbo A1 : Padrão",
 "sheets": [{"number": "A101", "name": "Planta Térreo"},
            {"number": "A102", "name": "Planta Cobertura"}]}
```
```json
{"dry_run": false,
 "placements": [{"sheet_number": "A101", "view": "Térreo"},
                {"sheet_number": "A102", "view_id": 123456, "x": 0.42, "y": 0.30},
                {"sheet_number": "A102", "view": "Tabela de Portas"}]}
```

## Resposta
```json
{"status": "success", "applied": 2,
 "title_block": {"family": "Carimbo A1", "type": "Padrão", "id": 4567},
 "results": [{"index": 0, "number": "A101", "name": "Planta Térreo", "sheet_id": 98765}]}
```
```json
{"status": "success", "applied": 1,
 "results": [{"index": 0, "sheet_number": "A101", "view": "Térreo", "view_id": 2222,
              "kind": "viewport", "position_m": {"x": 0.4267, "y": 0.3048},
              "viewport_id": 99999}]}
```
`dry_run` responde `{"status": "dry_run", "message": ..., "results": [...]}`
sem `sheet_id`/`viewport_id` (os elementos foram desfeitos).

Com erro por item (nada é aplicado, HTTP 200):
```json
{"status": "error", "applied": 0,
 "error": "2 of 3 sheets are invalid; nothing was applied",
 "errors": [{"index": 1, "number": "a101", "error": "Sheet number repeated in this batch"},
            {"index": 2, "number": "A100", "error": "A sheet with this number already exists"}]}
```
Carimbo não encontrado ou ambíguo → `{"status": "error", "error": ..., "available"|"matches": [...]}`
(até 20 nomes `"Família : Tipo"`).

## Decisões de implementação (2026-10-21)
- Mesmo padrão da 003: a rota autentica sem diálogo (`action=None`), valida, e
  só com `dry_run=false` chama `authorize_request(..., action="Create sheets")`
  ou `"Place views on sheets"`.
- **Tudo ou nada**: primeiro valida todos os itens (sem transação); com
  qualquer erro, devolve `errors`. Depois uma única `DB.Transaction` por
  requisição (`"MCP: create sheets"`, `"MCP: place views on sheets"`), desfeita
  no `dry_run` ou se o Revit recusar qualquer item. Commit não confirmado → erro.
- Números de folha são comparados **sem diferenciar maiúsculas** e sem espaços
  extras, contra as folhas existentes (inclusive placeholders) e dentro do lote.
- O nome da folha é gravado pelo `BuiltInParameter.SHEET_NAME`; nomes de
  elementos são lidos com `DB.Element.Name.GetValue` (o `Element.Name` falha em
  alguns tipos no IronPython). Carimbos são buscados por
  `BuiltInCategory.OST_TitleBlocks`, nunca pelo nome da categoria (o Revit está em português).
- Vistas por nome: ignora templates, folhas e navegadores; nome repetido (ex.:
  planta de piso e de forro "Cobertura") → erro pedindo `view_id`.
- Vista de modelo só pode estar em uma folha: `Viewport.CanAddViewToSheet`
  antes (validação) e de novo dentro da transação; a mesma vista duas vezes no
  lote → erro. Tabelas (`ViewSchedule`) e legendas podem repetir; tabelas
  usam `ScheduleSheetInstance.Create`. Tabelas de revisão do carimbo e de
  notas-chave internas → erro.
- Ponto de inserção: a `Viewport` é posicionada pelo **centro**; a
  `ScheduleSheetInstance`, pelo **canto superior esquerdo**. Sem `x`/`y`, vistas
  vão para o centro do carimbo e tabelas para o canto superior esquerdo do
  carimbo, 2 cm para dentro (`Min.X + margem`, `Max.Y - margem`). Com `x`/`y`, o
  ponto é o centro (vista) ou o canto superior esquerdo (tabela). Cada resultado
  traz `"anchor": "centre"` ou `"top_left"`.
- Transação dentro de um `TransactionGroup` com `IFailuresPreprocessor`
  (`revit_mcp/transactions.py`): avisos do Revit voltam em `warnings` (até 50)
  em vez de abrir diálogos que travariam a requisição; erros desfazem tudo. O
  `dry_run` faz `Commit` da transação (o Revit processa as falhas) e depois
  desfaz o grupo; a execução real assimila o grupo (um só Ctrl+Z).
- Entrada em metros, convertida para pés (`/ 0.3048`); a resposta devolve
  `position_m` em metros.
- Erros de formato (lista vazia, > 200, item sem campos) → HTTP 400.
- Lógica testada com um Revit simulado em `tests/unit/test_sheet_route_logic.py`.

## Regras e casos de borda
- Sem documento aberto → 503.
- Número de folha com caractere proibido pelo Revit → exceção no item → nada aplicado.
- Vista vazia (sem elementos) não entra em folha: `CanAddViewToSheet` devolve falso.
- Workshared: folha emprestada por outro usuário falha no commit → erro.

## Revit API
- `ViewSheet.Create(doc, titleBlockTypeId)`, `ViewSheet.SheetNumber`, `BuiltInParameter.SHEET_NAME`
- `FilteredElementCollector.OfCategory(BuiltInCategory.OST_TitleBlocks)`, `FamilySymbol.FamilyName`
- `Viewport.CanAddViewToSheet`, `Viewport.Create`, `ScheduleSheetInstance.Create`
- `Element.get_BoundingBox(sheet)` do carimbo, `ViewSheet.Outline`
- `DB.Transaction`, `DB.TransactionGroup` (`Assimilate`/`RollBack`), `Commit`/`RollBack`, `TransactionStatus`
- `IFailuresPreprocessor`, `FailuresAccessor.DeleteWarning`, `FailureHandlingOptions`

## Testes
- Unitário (wrapper, `tests/unit/test_sheet_tools.py`): endpoint, `dry_run=True`
  por padrão, sem chaves `None`, limite de 200 e validação de itens.
- Unitário (rota, `tests/unit/test_sheet_route_logic.py`): duplicados, carimbo
  padrão/ambíguo/inexistente, rollback do `dry_run`, centro do carimbo,
  tabela vs. viewport, vista em duas folhas, ordem da autorização.
- Integração (Windows + Revit): pendente.

## Validação manual no Revit
- [ ] `create_sheets` com `dry_run` não marca o documento como modificado
- [ ] 20 folhas criadas com um diálogo só; Ctrl+Z desfaz todas
- [ ] Número já existente → nada criado, erro com índice
- [ ] Carimbo padrão e carimbo por `"Família : Tipo"` com acento
- [ ] Vista posicionada no centro do carimbo; com `x`/`y` em metros na posição certa
- [ ] Tabela sem `x`/`y` fica no canto superior esquerdo do carimbo, dentro da folha (`anchor: top_left`)
- [ ] Tabela com `x`/`y`: o canto superior esquerdo fica no ponto pedido
- [ ] Aviso do Revit durante o posicionamento volta em `warnings`, sem diálogo travando o pedido
- [ ] Tabela posicionada em duas folhas
- [ ] Vista já em outra folha → erro, nada posicionado
