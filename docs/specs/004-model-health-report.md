# 004 — model_health_report

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (`authorize_request(request, action=None)`)
- **Rota:** `POST /model_health/` em `revit_mcp/health.py` (POST + JSON, padrão do repositório)
- **Wrapper:** `tools/health_tools.py` (módulo novo)

## Problema
Modelos lentos, pesados e cheios de avisos são uma dor recorrente de BIM
managers. O `get_revit_model_info` só traz a contagem de avisos; este
relatório diz **o que** está pesando e **onde agir**, com IDs para o LLM
seguir (selecionar, isolar, corrigir com outras tools).

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sample_ids` | int | 5 | Quantos IDs de exemplo por item (0–20) |

## Resposta
```json
{
  "status": "success",
  "document": "Projeto.rvt",
  "file_size_mb": 312.4,
  "revit_version": "2025",
  "warnings": {
    "total": 812,
    "flag": "high",
    "by_type": [
      {"description": "Highlighted walls overlap...", "count": 310, "sample_element_ids": [1, 2, 3]}
    ]
  },
  "imported_cad": {"count": 4, "sample_element_ids": [10, 11]},
  "linked_cad": {"count": 2},
  "in_place_families": {"count": 7, "by_category": {"Walls": 5, "Generic Models": 2}},
  "views": {"total": 640, "not_on_sheets": 410, "templates": 35, "unused_templates": 12},
  "revit_links": [{"name": "Estrutura.rvt", "status": "Loaded"}],
  "groups": {"model_types": 22, "instances": 140},
  "design_options": 3,
  "flags": ["warnings_high", "imported_cad", "many_views_not_on_sheets"]
}
```

## Decisões de implementação (2026-10-20)
- Cada seção (avisos, CAD, in-place, vistas, links, grupos, opções de projeto,
  tamanho) roda isolada: se uma falhar, as outras saem e o erro aparece em
  `section_errors`.
- In-place contado por **família** (`Family.IsInPlace`), agrupado por `FamilyCategory`.
- `views.placeable` = vistas que podem ir para folha (exclui navegadores, folhas,
  relatórios, vistas internas e as que não `CanBePrinted`); a flag
  `many_views_not_on_sheets` usa `not_on_sheets / placeable > 50%`.
- Flags possíveis: `warnings_medium|high`, `imported_cad`, `in_place_families`,
  `many_views_not_on_sheets`, `unused_view_templates`, `revit_links_not_loaded`.
- `warning_flag` e `health_flags` testados em `tests/unit/test_route_logic.py`.

## Regras e casos de borda
- Thresholds de `flag` (configuráveis como constantes no módulo):
  avisos `ok` < 200 ≤ `medium` < 500 ≤ `high`; vistas fora de folhas > 50% do total → flag.
- `file_size_mb`: `os.path.getsize(doc.PathName)` se o arquivo existir; modelo
  na nuvem ou nunca salvo → `null`.
- Vistas: ignorar templates, `ViewType` sem colocação em folha (ex.: `ProjectBrowser`,
  `SystemBrowser`, `Internal`, `Undefined`) e vistas que não `CanBePrinted`.
  "Em folha" = `ViewId` de algum `Viewport` ou `ScheduleSheetInstance`.
- Template sem uso = nenhuma vista com `ViewTemplateId` apontando para ele.
- Resposta deve caber no contexto: no máximo 20 tipos de aviso em `by_type`
  (os de maior contagem) e `sample_ids` IDs por item.
- Somente leitura: **nenhuma** transação.

## Revit API
- `doc.GetWarnings()` → `FailureMessage.GetDescriptionText()`, `GetFailingElements()`
- `FilteredElementCollector(doc).OfClass(ImportInstance)` → `IsLinked`
- `FamilyInstance.Symbol.Family.IsInPlace`
- `OfClass(View)`, `View.IsTemplate`, `View.ViewTemplateId`, `View.CanBePrinted`
- `OfClass(Viewport)` → `ViewId`; `OfClass(ScheduleSheetInstance)` → `ScheduleId`
- `OfClass(RevitLinkType)` → `RevitLinkType.IsLoaded(doc, id)` / `GetLinkedFileStatus()`
- `OfClass(GroupType)` (modelo), `OfClass(Group)`, `OfClass(DesignOption)`

## Testes
- Unitário: wrapper chama `/model_health/` com `params={"sample_ids": 5}`; limite 0–20.
- Integração: rodar em `tests/test.rvt` e checar que as chaves existem e os tipos batem.

## Validação manual no Revit
- [ ] Modelo pequeno (test.rvt): roda em < 5 s
- [ ] Modelo grande (> 300 MB): tempo aceitável (< 60 s) e resposta < 20 KB
- [ ] CAD importado vs vinculado contados separadamente
- [ ] Comparar a contagem de avisos com *Manage > Warnings*
