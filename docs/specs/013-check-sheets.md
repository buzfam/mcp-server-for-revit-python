# 013 — check_sheets

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (sem diálogo de aprovação, sem transação)
- **Rota:** `POST /check_sheets/` em `revit_mcp/sheet_checks.py` (módulo novo)
- **Wrapper:** `tools/sheet_check_tools.py` (módulo novo)
- **Origem:** proposta Carol Vale Arquitetura, item R2 (espec. §10 e §11.3)

## Problema
Antes de emitir, o escritório confere cada prancha a olho contra o checklist do
seu tipo (planta de layout, demolir/construir, forro, elétrica, cortes,
detalhes): se as vistas, tabelas e legendas estão lá, se a escala é a do
padrão, se o carimbo está preenchido e se há revisão atual. A tool faz essa
conferência pela API e devolve "faltando X na folha A-03". O que a API não
consegue verificar (cota visualmente certa, conteúdo das notas) volta como
`manual_checks`, para conferir a olho.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `checklists` | list[regra] \| null | null | 0–50 regras (abaixo). Cada folha usa a **primeira** regra cujo `match` serve |
| `sheets` | list[str] \| null | null | Só estes números de folha (1–500); números inexistentes vão para `summary.not_found` |
| `generic` | bool | true | Checagens genéricas (abaixo) |
| `only_problems` | bool | false | Omite da lista as folhas `ok` (continuam contadas no resumo) |
| `limit` | int | 200 | Folhas conferidas, em ordem de número (1–1000); o resto é cortado com `truncated: true` |
| `unplaced_limit` | int | 50 | Vistas fora de folha listadas por nome (0–200); a contagem é sempre total |
| `unplaced_include_schedules` | bool | false | Também conta tabelas que não estão em nenhuma folha |

`checklists` vazio com `generic=false` → erro ("nada para conferir").

### Regra (checklist de um tipo de prancha)
| Chave | Tipo | Descrição |
|---|---|---|
| `match` | {number_prefix?, number_regex?, name_regex?} | **Obrigatória**, ao menos uma chave; todas as dadas precisam servir. Prefixo sem diferenciar maiúsculas; regex = busca (`re.search`) sem diferenciar maiúsculas (use `^`/`$` para ancorar) |
| `name` | str | Rótulo da regra na resposta (padrão `"#<índice>"`) |
| `required_views` | list[{view_type?, name_regex?, min?=1}] | Vistas exigidas na folha. `view_type` é o nome do `ViewType` da API: `FloorPlan`, `CeilingPlan`, `Section`, `Elevation`, `Detail`, `DraftingView`, `Legend`, `ThreeD`, `AreaPlan`, `EngineeringPlan`, `Walkthrough`, `Rendering` |
| `required_schedules` | list[regex] | Tabelas exigidas (nome da tabela posicionada; a tabela de revisões do carimbo é ignorada) |
| `required_legends` | list[regex] | Legendas exigidas (vistas `Legend` na folha) |
| `allowed_scales` | list[int] | Escalas permitidas (50 = 1:50) para as vistas da folha, exceto legendas |
| `title_block_params` | list[str] | Parâmetros que precisam estar preenchidos: procurados na folha, depois na instância do carimbo, depois em Informações do projeto; pelo nome em português do Revit **ou** pelo nome do `BuiltInParameter` (`SHEET_ISSUE_DATE`) |
| `require_revision` | bool | Exige revisão atual na folha (`SHEET_CURRENT_REVISION`) |
| `min_viewports` / `max_viewports` | int | Quantidade de vistas na folha (sem contar tabelas) |
| `manual` | list[str] | Itens que só uma pessoa confere; voltam em `manual_checks` |

Chaves desconhecidas (por exemplo `required_view`) são recusadas, para pegar
erro de digitação, tanto no wrapper quanto na rota.

### Checagens genéricas (`generic=true`)
- `no_views`: folha sem nenhuma vista nem tabela.
- `title_block`: folha sem carimbo.
- `empty_viewport`: vista de planta, forro, corte, elevação, detalhe ou 3D que
  não mostra nenhum elemento de modelo. Checagem barata e aproximada:
  `FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().WhereElementIsViewIndependent()`
  excluindo níveis, eixos, planos de referência, câmeras, caixas de corte,
  scope boxes e pontos base, e `GetElementCount() == 0`. Elementos de vínculo
  (RVT/CAD) contam como conteúdo; uma vista só com anotações conta como vazia.
- `unplaced_views` (no relatório, não por folha): vistas que não estão em
  nenhuma folha, sem modelos de vista, vistas de navegador/sistema, relatórios,
  folhas e vistas não imprimíveis; tabelas só com `unplaced_include_schedules`.
  Vista dependente posicionada não marca a vista mãe como posicionada.

Folhas de espaço reservado (`IsPlaceholder`) são puladas e contadas em
`summary.placeholders_skipped`.

### Status por folha
- `missing`: algo exigido está ausente (vista, tabela, legenda, carimbo,
  parâmetro, revisão, folha sem vistas).
- `review`: só itens que pedem um olhar (`scale`, `empty_viewport`,
  `viewport_count`) ou a regra tem itens `manual`.
- `ok`: nada encontrado e nada para conferir a olho.

Os textos de `detail` estão em português, prontos para mostrar à arquiteta.

## Requisição para a rota
```json
{"checklists": [{"name": "Planta de layout", "match": {"number_prefix": "A-1"},
                 "required_views": [{"view_type": "FloorPlan", "name_regex": "layout"}],
                 "allowed_scales": [50], "require_revision": true,
                 "manual": ["cotas conferidas"]}],
 "sheets": ["A-101", "A-102"], "generic": true, "only_problems": false,
 "limit": 200, "unplaced_limit": 50, "unplaced_include_schedules": false}
```

## Resposta
```json
{"status": "success",
 "summary": {"sheets_checked": 2, "sheets_total": 2, "truncated": false,
             "ok": 0, "missing": 1, "review": 1, "sheets_without_rule": 0,
             "problems_by_check": {"required_view": 1, "scale": 1, "revision": 1},
             "placeholders_skipped": 0, "not_found": []},
 "sheets": [
   {"id": 12345, "number": "A-101", "name": "Planta de Layout", "rule": "Planta de layout",
    "status": "review", "revision": "R01", "viewports": 2, "schedules": 1,
    "problems": [], "manual_checks": ["cotas conferidas"]},
   {"id": 12346, "number": "A-102", "name": "Planta de Layout Superior", "rule": "Planta de layout",
    "status": "missing", "revision": null, "viewports": 1, "schedules": 0,
    "problems": [{"check": "required_view", "detail": "faltando vista FloorPlan nome ~ 'layout' (0 de 1)"},
                 {"check": "scale", "detail": "vista 'Superior' em 1:75 (permitidas: 1:50)"},
                 {"check": "revision", "detail": "folha sem revisão atual"}],
    "manual_checks": ["cotas conferidas"]}],
 "unplaced_views": {"count": 7, "by_type": {"FloorPlan": 4, "Section": 3},
                    "views": [{"id": 2222, "name": "Estudo", "type": "FloorPlan"}],
                    "truncated": false}}
```
Valores de `check`: `no_views`, `title_block`, `empty_viewport`,
`required_view`, `required_schedule`, `required_legend`, `scale`,
`title_block_param`, `revision`, `viewport_count`.

Erro de entrada → HTTP 400 `{"error": "Invalid checklists", "errors": [{"index": 1, "error": "match must be ..."}]}`.

## Exemplo de checklists do escritório (ilustrativo)
Os checklists reais vêm da Carol Vale Arquitetura (espec. §10: "o escritório já
possui checklists do conteúdo normalmente incluído em cada prancha"). O JSON
abaixo é **só um exemplo** de como eles ficam; numeração, nomes de vistas,
escalas e parâmetros do carimbo precisam ser trocados pelos do template real.

```json
[
  {"name": "Planta de layout", "match": {"name_regex": "layout"},
   "required_views": [{"view_type": "FloorPlan", "name_regex": "layout"}],
   "required_legends": ["legenda.*layout|mobili"],
   "allowed_scales": [50, 75],
   "title_block_params": ["Desenhado por", "SHEET_ISSUE_DATE", "Cliente"],
   "require_revision": true,
   "manual": ["cotas gerais dos ambientes", "nomes e áreas dos ambientes legíveis"]},

  {"name": "Planta demolir/construir", "match": {"name_regex": "demoli|construir"},
   "required_views": [{"view_type": "FloorPlan", "name_regex": "demoli|construir"}],
   "required_legends": ["legenda.*(demolir|construir)"],
   "allowed_scales": [50],
   "title_block_params": ["Desenhado por", "SHEET_ISSUE_DATE"],
   "require_revision": true,
   "manual": ["hachuras de demolir e construir conferidas", "paredes existentes no filtro de fase certo"]},

  {"name": "Planta de forro", "match": {"name_regex": "forro"},
   "required_views": [{"view_type": "CeilingPlan"}],
   "required_legends": ["legenda.*forro"],
   "allowed_scales": [50],
   "require_revision": true,
   "manual": ["cotas de nível do forro", "luminárias alinhadas ao forro"]},

  {"name": "Planta elétrica", "match": {"name_regex": "el[eé]tric|pontos"},
   "required_views": [{"view_type": "FloorPlan", "name_regex": "el[eé]tric|pontos"}],
   "required_legends": ["legenda.*(el[eé]tric|pontos)"],
   "allowed_scales": [50],
   "require_revision": true,
   "manual": ["alturas dos pontos indicadas", "circuitos conforme projeto complementar"]},

  {"name": "Cortes", "match": {"name_regex": "^cortes?"},
   "required_views": [{"view_type": "Section", "min": 2}],
   "allowed_scales": [50],
   "require_revision": true,
   "manual": ["cotas verticais e níveis", "pé-direito de todos os ambientes"]},

  {"name": "Detalhes", "match": {"name_regex": "detalhe"},
   "required_views": [{"view_type": "Detail"}],
   "allowed_scales": [5, 10, 20, 25],
   "min_viewports": 1, "max_viewports": 8,
   "require_revision": true,
   "manual": ["materiais e especificações nas notas"]}
]
```

## Regras e casos de borda
- Sem documento aberto → 503.
- Rota só leitura: `authorize_request(request, action=None)` é a primeira
  linha; nada de transação.
- Regras e entrada validadas no wrapper (falha rápida) e de novo na rota (400).
- Nomes comparados sem diferenciar maiúsculas e espaços (`name_key`), números de
  folha também (`a-101` = `A-101`).
- Nome de elemento via `DB.Element.Name.GetValue` (reaproveita
  `sheets.element_name`), textos via `normalize_string`; resposta via
  `routes.make_response`, sem `json.dumps` no IronPython.
- Parâmetro tipo ElementId vazio (`InvalidElementId`) conta como vazio; textos
  só com espaços contam como vazios.

## Revit API
`ViewSheet` (`SheetNumber`, `IsPlaceholder`, `SHEET_CURRENT_REVISION`),
`Viewport` (`SheetId`, `ViewId`), `ScheduleSheetInstance` (`OwnerViewId`,
`ScheduleId`, `IsTitleblockRevisionSchedule`), `View` (`ViewType`, `Scale`,
`IsTemplate`, `CanBePrinted`), `ViewSchedule`, title blocks por
`BuiltInCategory.OST_TitleBlocks` (`OwnerViewId`), `Element.GetParameters`,
`Element.get_Parameter(BuiltInParameter)`, `Document.ProjectInformation`,
`FilteredElementCollector(doc, viewId)` com `WhereElementIsViewIndependent`,
`ElementMulticategoryFilter(..., inverted=True)` e `GetElementCount`.
Tudo existe no Revit 2024 (versão do escritório).

## Testes
- Unitário (wrapper): `tests/unit/test_sheet_check_tools.py` — payload padrão,
  sem chaves `None`, validação das regras sem chamar o Revit.
- Unitário (rota, API falsa): `tests/unit/test_sheet_check_route_logic.py` —
  escolha da regra, cada tipo de problema, status, parâmetros pelo nome
  localizado/BuiltInParameter/Informações do projeto, filtro e limite,
  vistas fora de folha, rota só leitura, 400 e 503.
- IronPython: checagem dos helpers puros em `scripts/ironpython_check.py`.

## Validação manual no Revit
- [ ] Rodar com o exemplo acima num RVT do escritório e conferir os problemas apontados.
- [ ] `empty_viewport`: confirmar que vista com só vínculo conta como cheia e que
      vista realmente vazia aparece; medir o tempo em projeto com muitas folhas.
- [ ] `title_block_params` com nomes em português do carimbo real e com
      parâmetros que o carimbo lê de Informações do projeto.
- [ ] `require_revision` em folha com e sem revisão emitida.
- [ ] `unplaced_views`: comparar a contagem com o `model_health_report`.
