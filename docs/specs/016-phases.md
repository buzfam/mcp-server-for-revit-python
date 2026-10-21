# 016 — phases_report e set_element_phases

- **Status:** Implementada (não validada no Revit)
- **Tipo:** `phases_report` leitura (sem transação, sem diálogo); `set_element_phases`
  escrita (diálogo de aprovação: "Set element phases")
- **Rotas:** `POST /phases_report/` e `POST /set_element_phases/` em `revit_mcp/phases.py` (módulo novo)
- **Wrapper:** `tools/phase_tools.py` (módulo novo)
- **Origem:** especificação CVARQ §8 (anteprojeto começa com o existente organizado
  por fases para a planta de reforma) e §11.2 (fases e filtros de fase no template),
  proposta item **R5** (P1)

## Problema
A planta de reforma do anteprojeto depende de o modelo estar organizado em
"existente / demolir / construir". Depois de levantar o imóvel, marcar tudo como
existente e indicar o que será demolido é trabalho repetitivo, elemento por
elemento, na paleta de propriedades. Também não há um jeito rápido de conferir
se as fases, os filtros de fase e as vistas do template estão coerentes (§11.2).

## Parâmetros da tool

### phases_report
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `categories` | lista de texto | — | Só conta estas categorias (nome do Revit em pt, "Paredes", ou inglês/API, "Walls", "OST_Walls") |
| `by_category` | bool | `false` | Detalha cada combinação pelas 10 categorias com mais elementos |
| `limit` | int | `200` | Máximo de nomes de vistas listados no total (1–1000); as contagens são sempre completas |

### set_element_phases
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `changes` | lista | — | 1–200 itens `{element_id, created_phase?, demolished_phase?}` |
| `selector` | objeto | — | `{category?, level?, view_id?, selection?}`: exige `category` ou `selection=true` |
| `created_phase` | texto ou int | — | Fase de criação aplicada a todos os elementos do `selector` |
| `demolished_phase` | texto ou int | — | Fase de demolição aplicada ao `selector`; `"none"`/`"nenhum"` limpa |
| `dry_run` | bool | `true` | Só valida e mostra antes/depois; `false` aplica |
| `expected_count` | int | — | Na execução real, o `matched` da prévia; se o total mudou → 409 e nada é alterado |

Exatamente um entre `changes` e `selector`. Fase por nome (como no Revit,
sem diferenciar maiúsculas e espaços) ou por id.

## Requisição para a rota
```json
{"categories": ["Paredes", "Portas"], "by_category": true, "limit": 200}
```
```json
{"dry_run": true,
 "changes": [{"element_id": 123456, "created_phase": "Existente",
              "demolished_phase": "Nova construção"}]}
```
```json
{"dry_run": true, "selector": {"category": "Paredes", "level": "Térreo"},
 "created_phase": "Existente", "demolished_phase": "none", "expected_count": 12}
```

## Resposta

`phases_report`:
```json
{
  "status": "success",
  "phases": [{"id": 101, "name": "Existente", "sequence": 1},
             {"id": 102, "name": "Nova construção", "sequence": 2}],
  "phase_filters": [{"id": 201, "name": "Mostrar demolido + novo",
                     "presentation": {"new": "by_category", "existing": "overridden",
                                      "demolished": "overridden", "temporary": "not_displayed"}}],
  "elements_total": 312,
  "combinations": [
    {"created_phase": "Existente", "demolished_phase": "Nova construção",
     "status_in_last_phase": "demolished", "count": 14,
     "categories": [{"category": "Paredes", "count": 9}, {"category": "Portas", "count": 5}]},
    {"created_phase": "Existente", "demolished_phase": null,
     "status_in_last_phase": "existing", "count": 250},
    {"created_phase": "Nova construção", "demolished_phase": null,
     "status_in_last_phase": "new", "count": 48}
  ],
  "views": [{"phase": "Nova construção", "phase_filter": "Mostrar demolido + novo",
             "count": 3, "views": ["Planta de reforma - Térreo", "..."]}],
  "views_total": 27,
  "views_truncated": false
}
```

`set_element_phases` (prévia):
```json
{
  "status": "dry_run", "changed": 0, "would_change": 1, "matched": 1, "unchanged": 0,
  "message": "Dry run: Revit accepted the phases of 1 elements (1 would change). ...",
  "results": [{"element_id": 123456, "category": "Paredes", "name": "Tijolo 15",
               "before": {"created_phase": "Nova construção", "demolished_phase": null},
               "after": {"created_phase": "Existente", "demolished_phase": "Nova construção"},
               "changed": true}],
  "warnings": [], "phases": ["Existente", "Nova construção"]
}
```
Execução real: `"status": "success", "changed": N`. Com `selection=true`,
`skipped_without_phases` conta os itens selecionados sem fase (vistas, anotações).

Erro (nada é alterado):
```json
{"status": "error", "error": "1 of 2 elements are invalid; nothing was changed",
 "changed": 0,
 "errors": [{"index": 1, "element_id": 654, "error": "Demolished phase 'Existente' comes before created phase 'Nova construção'"}]}
```

## Regras e casos de borda
- Sem documento aberto → 503. Entrada malformada → 400 (o wrapper também valida antes de chamar o Revit).
- Com `categories`, a coleta usa `ElementMulticategoryFilter` (só lê as categorias pedidas).
- Contagem: só instâncias de categorias de **modelo**, não específicas de vista, com fase
  (`HasPhases`). Ambientes, anotações e tipos ficam de fora.
- `status_in_last_phase` é o status que o Revit mostra numa vista da **última** fase
  (existing, demolished, new, temporary, past, future), para ler a planta de reforma.
- Vistas: só as que têm o parâmetro Fase (`VIEW_PHASE`); modelos de vista ficam de fora.
- `set_element_phases`:
  - demolição não pode vir antes da criação (mesma fase = temporário);
  - o mesmo `element_id` duas vezes em `changes` → 400 com o índice repetido (wrapper e rota);
  - `created_phase` nunca é limpo; `demolished_phase: "none"` limpa a demolição;
  - elemento inexistente, sem fase, com `ArePhasesModifiable() == false` ou com o
    parâmetro Fase de criação/demolição só leitura → erro do lote todo;
  - antes da transação, o Revit também confere cada fase com `Element.IsPhaseCreatedValid` /
    `IsPhaseDemolishedValid` (quando existem na versão). Como esses métodos comparam com a
    outra fase **atual** do elemento, cada um só é chamado quando a outra fase não muda; quando
    as duas mudam, valem a checagem de ordem acima e a validação do Commit;
  - `selector`: `category` filtra pela categoria; `level` pelo `LevelId` do elemento
    (elementos sem nível não entram); `view_id` pelos elementos visíveis nessa vista;
    `selection=true` pela seleção atual do Revit. Mais de 200 elementos → 400
    pedindo para restringir o filtro;
  - `view_id` precisa ser uma vista gráfica (planta, corte, elevação, 3D...): tabela, folha,
    legenda, relatório ou modelo de vista → 400. A coleta por vista devolve o que a vista
    **mostra**, então a fase e o filtro de fase da vista valem (ex.: numa vista com filtro
    "Mostrar novo", elementos demolidos não entram);
  - `expected_count` diferente do total resolvido agora (ex.: a seleção mudou entre a prévia
    e a execução real) → 409, sem diálogo e sem alteração;
  - o diálogo de aprovação mostra, além do pedido, `_resolved_count` (total resolvido) e
    `_element_ids` (até 50 ids) — o que de fato será alterado;
  - tudo ou nada, numa `TransactionGroup` + `Transaction` "MCP: set element phases":
    a prévia passa pelo Commit (o Revit valida e gera avisos) e depois volta
    (`RollBack` do grupo); a execução real é um único passo de desfazer (`Assimilate`);
  - avisos do Revit vão em `warnings` (sem diálogos); erros desfazem o lote;
  - ao mudar a criação de um elemento que já tem demolição, a demolição é limpa antes
    e regravada depois, para o Revit não recusar a ordem intermediária;
  - o diálogo de aprovação só aparece com `dry_run=false`.

## Revit API
- `Document.Phases` (`PhaseArray`, em ordem), `Phase`.
- `PhaseFilter.GetPhaseStatusPresentation(ElementOnPhaseStatus)` →
  `PhaseStatusPresentation` (`ShowByCategory`, `ShowOverriden`, `DontShow`).
- `Element.HasPhases()`, `Element.ArePhasesModifiable()`, `Element.CreatedPhaseId`,
  `Element.DemolishedPhaseId` (leitura e escrita), `BuiltInParameter.PHASE_CREATED`,
  `PHASE_DEMOLISHED`, `VIEW_PHASE`, `VIEW_PHASE_FILTER`.
- `Element.IsPhaseCreatedValid`, `Element.IsPhaseDemolishedValid` (opcionais), `ElementMulticategoryFilter`.
- `FilteredElementCollector(doc[, viewId])`, `Category.CategoryType`, `Element.LevelId`,
  `UIDocument.Selection.GetElementIds()`.
- `TransactionGroup`, `Transaction`, `IFailuresPreprocessor` via `revit_mcp/transactions.py` (`Batch`).
- Revit 2024: `ElementId` com `Int64` (`element_id_from_int`).

## Testes
- Unitário (wrapper): `tests/unit/test_phase_tools.py` — endpoint, payload, sem chaves `None`,
  validação de `changes`/`selector`.
- Unitário (rota, API falsa): `tests/unit/test_phase_route_logic.py` — resolução de fase,
  ordem, status por fase, relatório (com filtro multicategoria), prévia com rollback, commit,
  ordem intermediária, erros por item, `IsPhase*Valid`, ids repetidos, `expected_count` (409),
  prévia no diálogo, vista não gráfica, rejeição do Revit, falha no Commit, seletor e limite de 200.
- IronPython: checagem das funções puras em `scripts/ironpython_check.py`.

## Validação manual no Revit
- [ ] `phases_report` num modelo com "Existente" e "Nova construção": fases na ordem, filtros
      de fase com a apresentação certa (conferir com Gerenciar > Fases > Filtros de fase).
- [ ] Contagens por combinação batem com uma tabela de paredes/portas filtrada por fase.
- [ ] Vistas agrupadas por fase e filtro de fase conferem com o navegador de projeto.
- [ ] `set_element_phases` em prévia não altera nada (Ctrl+Z vazio) e mostra antes/depois.
- [ ] Execução real marca paredes do levantamento como "Existente" num único passo de desfazer,
      com um diálogo de aprovação.
- [ ] Demolir em "Nova construção" faz as paredes aparecerem tracejadas na planta de reforma.
- [ ] `demolished_phase: "nenhum"` limpa a demolição.
- [ ] Demolição antes da criação é recusada sem alterar nada.
- [ ] Seletor com `level` pega paredes, portas e janelas do nível (conferir `LevelId` das
      famílias hospedadas) e `selection=true` usa a seleção atual.
- [ ] Mais de 200 elementos no seletor dá erro pedindo filtro.
- [ ] `view_id` de uma tabela ou folha dá 400; numa planta com filtro "Mostrar novo" só entram os elementos visíveis.
- [ ] Prévia com `selection=true`, mudar a seleção, execução real com `expected_count` da prévia → 409 sem diálogo.
- [ ] O diálogo de aprovação mostra `_resolved_count` e `_element_ids`.
- [ ] `IsPhaseCreatedValid`/`IsPhaseDemolishedValid` existem no Revit 2024 e recusam fases inválidas antes da transação.
- [ ] Portas/janelas hospedadas em parede demolida: avisos do Revit aparecem em `warnings`.
- [ ] Nomes acentuados ("Nova construção") funcionam na entrada e na saída.
