# 012 — audit_template

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (`authorize_request(request, action=None)`, sem transação)
- **Rota:** `POST /audit_template/` em `revit_mcp/audit.py` (módulo novo)
- **Wrapper:** `tools/audit_tools.py` (módulo novo)
- **Origem:** proposta R1 (P1) para a Carol Vale Arquitetura; especificação do escritório §11.1 e §11.2
  (19 itens do template). Revit 2024 em português, uma pessoa, um PC.

## Problema
A Fase 6 (novo template) começa por uma auditoria do template atual. Hoje ela
exige abrir diálogo por diálogo (Navegador, Fases, Modelos de vista, Estilos de
objeto, Parâmetros do projeto, Configurações de exportação...). Esta tool lê o
projeto ou template aberto e devolve um inventário em JSON, por seção, marcando
o que está **sem uso**. O agente transforma o resultado na planilha de 19 itens
da skill `revit-template-auditoria`.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sections` | list[str] | todas | Restringe às seções listadas (nomes da tabela abaixo) |
| `limit` | int | 100 | Máximo de itens por lista (1–1000) |
| `naming` | dict[str, str] | — | Regex por tipo de nome; deve casar com o nome **inteiro**. Tipos: `views`, `view_templates`, `sheets`, `sheet_numbers`, `schedules`, `materials`, `families`, `levels` |

O wrapper valida tudo antes de ir ao Revit (seções conhecidas, limite, regex
compilável) e não envia chaves com `None`.

## Requisição para a rota
```json
{
  "sections": ["view_templates", "filters", "families", "purge_preview"],
  "limit": 100,
  "naming": {"views": "(PB|CT|EL|3D) - .+", "sheet_numbers": "A\\d{2}"}
}
```

## Resposta
Cada lista segue o mesmo formato: `{count, unused_count, items[:limit], truncated}`,
com os itens **sem uso primeiro** (para o limite não escondê-los) e
`unused_rule` dizendo o que "sem uso" significa naquela lista. `unused_count`
é `null` quando a API não permite dizer o que está sem uso.

```json
{
  "status": "success",
  "document": "Template CVArq",
  "is_template_file": true,
  "revit_version": "2024",
  "limit": 100,
  "unused_source": "revit_purge",
  "sections": {
    "view_templates": {
      "count": 18, "unused_count": 5, "truncated": false,
      "unused_rule": "no view uses this template",
      "items": [{"id": 1234, "name": "Corte antigo", "view_type": "Section", "views": 0, "filters": 2, "unused": true}]
    },
    "families": {
      "count": 210, "unused_count": 64, "truncated": true,
      "by_category": {"Portas": {"families": 12, "unused": 4}},
      "items": [{"id": 5, "name": "Cadeira", "category": "Mobiliário", "in_place": false,
                 "types": 3, "unused_types": 3, "instances": 0, "unused": true}]
    },
    "purge_preview": {
      "source": "revit_purge", "count": 140, "by_kind": {"FamilySymbol": 90, "Material": 30},
      "items": [{"id": 77, "kind": "Material", "category": null, "name": "Madeira sem uso"}],
      "truncated": true,
      "not_in_revit_purge": {"view_templates": {"count": 5, "...": "..."}, "filters": {"count": 3, "...": "..."}},
      "note": "Preview only, nothing is deleted. ..."
    },
    "naming": {
      "views": {"pattern": "(PB|CT|EL|3D) - .+", "checked": 120, "violations_count": 7,
                "violations": [{"id": 9, "name": "Planta 1"}], "truncated": false}
    }
  },
  "checklist": [
    {"item": 7, "title": "Categorias e visibilidades", "coverage": "conferir a olho",
     "sections": [], "check_manually": "Visibilidade/Gráficos de cada modelo de vista"}
  ],
  "section_errors": {"export_settings": "..."}
}
```

Erros: sem documento → 503; entrada inválida → 400 (`{"error": "..."}`);
falha inesperada → 500. Uma seção que falha não derruba as outras: vai para
`section_errors`.

## Seções
| Chave | Conteúdo | "Sem uso" |
|---|---|---|
| `browser_organization` | Organizações do Navegador (vistas, folhas, tabelas): nome, tipo, se é a atual e, para a atual, os parâmetros de agrupamento (`folders`) | não se aplica |
| `levels_grids` | `levels`: nome, elevação (m), nº de plantas; `without_plan_view`. `grids`: nomes | não se aplica |
| `phases` | `phases`: ordem, nome, nº de vistas por fase. `phase_filters`: vistas/modelos que usam, apresentação de Nova/Existente/Demolida/Temporária | filtro de fase sem vista nem modelo |
| `view_templates` | nome, tipo de vista, nº de vistas que usam, nº de filtros | nenhuma vista usa |
| `filters` | `ParameterFilterElement` e `SelectionFilterElement`: tipo, nº de categorias, uso em vistas e em modelos | não aplicado em vista nem modelo |
| `views` | vistas imprimíveis (sem folhas e tabelas): tipo, escala, modelo, se está em folha; `by_type`, `scales`, `without_template` | fora de prancha |
| `line_styles` | `styles` (subcategorias de Linhas: espessura, cor, padrão, nº de linhas, se é interno), `line_patterns`, `fill_patterns` (desenho/modelo) | estilo do usuário sem linhas no projeto; padrões pela Limpeza do Revit |
| `annotation_types` | `dimension_types` (com `style` e `by_style`, inclui cotas de ponto), `text_types`, `tag_and_symbol_types` (`by_category`, sem carimbos) | sem instâncias colocadas, ou pela Limpeza |
| `families` | famílias carregáveis e no local: categoria, nº de tipos, tipos sem uso, instâncias; `by_category` | sem instâncias, ou pela Limpeza |
| `shared_parameters` | `SharedParameterElement`: GUID, se está vinculado ao projeto, instância/tipo, categorias (até 20 + `categories_count`); `bound_count` | não se aplica |
| `project_parameters` | todos os vínculos de `doc.ParameterBindings`, com `shared` | não se aplica |
| `materials` | nome, classe, categoria | pela Limpeza do Revit (senão `null`) |
| `schedules` | tabelas (sem modelos e sem tabelas de revisão do carimbo): categoria (`<multicategory>`), se é de chave, se está em folha | fora de prancha |
| `sheets` | `sheets`: número, nome, nº de vistas e tabelas, carimbo, se é marcador; `placeholders`, `without_title_block`. `title_blocks`: tipo e nº de folhas | folha vazia; carimbo sem folha |
| `export_settings` | nomes das configurações DWG (com `active`), PDF, de impressão e conjuntos de folhas | não se aplica |
| `purge_preview` | o que a Limpeza do Revit removeria (agrupado por classe) + `not_in_revit_purge` (modelos de vista e filtros sem uso) | — |
| `naming` | só quando `naming` é enviado: violações por tipo de nome | — |

### Como "sem uso" é decidido (e por quê)
- **Revit 2024+**: `Document.GetUnusedElements(categorias vazias)` devolve a
  mesma lista da *Limpeza de itens não utilizados*. É a fonte para famílias,
  tipos, materiais, padrões de linha e de preenchimento e para a prévia de
  limpeza (`unused_source: "revit_purge"`). Materiais **só** são marcados por
  esse caminho: descobrir o uso de material pelos elementos (`GetMaterialIds`
  por elemento, pinturas, parâmetros de material em instâncias) é caro e
  incompleto; sem a API, `materials.unused_count` é `null`.
- **Sem essa API** (versões anteriores): uso por contagem de instâncias, numa
  única passada por todos os elementos (`GetTypeId` → dicionário tipo → nº).
  Tipos usados só dentro de outros tipos (perfis, aninhados) aparecem como sem
  uso nesse modo (`unused_source: "instance_count"`).
- Modelos de vista, filtros, filtros de fase, vistas e tabelas fora de prancha
  e folhas vazias são calculados pela própria tool (a Limpeza não os cobre).
- A Limpeza do Revit pode exigir várias passadas; a prévia é a primeira.

## Mapeamento dos 19 itens (§11.2) → seções
| # | Item da especificação | Seção(ões) | Cobertura | Conferir a olho |
|---|---|---|---|---|
| 1 | Estrutura do navegador de projeto | `browser_organization` | parcial | regras de filtro e ordenação |
| 2 | Níveis e eixos | `levels_grids` | API | — |
| 3 | Fases de projeto | `phases` | API | — |
| 4 | Filtros de fase e de elementos | `phases`, `filters` | parcial | regras e sobreposições de cada filtro |
| 5 | Modelos de vista | `view_templates` | parcial | configurações internas (V/G, detalhe, escala) |
| 6 | Vistas de planta, corte, elevação, 3D e detalhamento | `views` | API | — |
| 7 | Categorias e visibilidades | — | **conferir a olho** | V/G de cada modelo de vista |
| 8 | Escalas e padrões gráficos | `views`, `line_styles` | parcial | aparência nas pranchas impressas |
| 9 | Estilos de linha e pesos de linha | `line_styles` | parcial | tabela de espessuras de linha |
| 10 | Cotas, textos, símbolos e anotações | `annotation_types` | parcial | aparência de cada tipo |
| 11 | Famílias carregadas e famílias padrão | `families`, `annotation_types` | parcial | tipo padrão de cada categoria |
| 12 | Parâmetros compartilhados | `shared_parameters`, `project_parameters` | API | — |
| 13 | Materiais | `materials` | parcial | aparência e ativos de renderização |
| 14 | Tabelas e quantitativos | `schedules` | parcial | campos, filtros e formatação |
| 15 | Pranchas, carimbos e organização de folhas | `sheets` | parcial | conteúdo gráfico do carimbo |
| 16 | Nomenclatura de vistas, folhas e arquivos | `naming` | parcial | nomes de arquivos fora do modelo |
| 17 | Padrões de exportação | `export_settings` | parcial | camadas, cores e opções de cada configuração |
| 18 | Organização das fases existente, demolição e construção nova | `phases` | parcial | sobreposições gráficas de fase |
| 19 | Regras para preparar arquivos de entrega | — | **conferir a olho** | procedimento do escritório |

A mesma tabela volta na resposta em `checklist` (só com as seções presentes).

### O que a API não audita (volta como "conferir a olho")
- Visibilidade/Gráficos por categoria de cada modelo de vista (seria uma
  resposta enorme; fica para uma tool própria se for preciso).
- A tabela de espessuras de linha (Gerenciar > Espessuras de linha) e a
  aparência gráfica final das pranchas.
- Sobreposições gráficas de fase (Gerenciar > Fases > Sobreposições gráficas).
- Conteúdo do carimbo, aparência de cotas, textos e materiais.
- Opções internas das configurações DWG/PDF (camadas, cores). Não há
  configuração DWF salva no projeto que a API liste.
- Nomes de arquivos e regras de entrega, que ficam fora do modelo.

## Regras e casos de borda
- Sem documento aberto → 503. Só leitura: nenhuma transação, nada é apagado.
- Revit em português: nada é comparado por nome exibido; categorias por
  `BuiltInCategory`, parâmetros por `BuiltInParameter`, elementos por classe.
  Os nomes devolvidos vêm no idioma do modelo (`normalize_string`).
- `Element.Name` lido por `DB.Element.Name.GetValue` com fallback.
- Elevação em metros (pés × 0,3048, 3 casas).
- Resposta compacta: limite por lista, categorias de parâmetro limitadas a 20.
- Modelos grandes: coletores por classe, uma passada só para contar
  instâncias; nenhuma geometria é lida. Timeout do wrapper: 300 s. A rota tem
  orçamento de 240 s: passado esse tempo, as seções que faltam não são
  montadas e aparecem em `section_errors` como `"skipped: time budget"`, com
  `time_budget.skipped_sections` (chame de novo só com elas).
- `GetUnusedElements` com conjunto vazio de categorias: se devolver **zero** ids
  enquanto a contagem de instâncias acha tipos de família sem instância, a
  resposta é tratada como não confiável e o método de contagem é usado
  (`unused_source = "instance_count"` e `unused_source_note`).
- A contagem de instâncias por tipo (`_type_counts`) continua numa passada só
  sobre todos os elementos: restringir categorias deixaria de fora etiquetas
  (`IndependentTag`), cotas e textos cujos tipos o relatório também conta.
- Corpo vazio ou ausente → valores padrão (como `{}`), não erro 500.
- Regex de `naming` precisa casar com o nome inteiro (`re.match` + `\Z`, com
  `re.UNICODE`); nome vazio conta como violação.

## Revit API
`FilteredElementCollector.OfClass/OfCategory/WhereElementIsNotElementType`,
`BrowserOrganization` (`GetCurrentBrowserOrganizationForViews/Sheets`,
`GetFolderItems`), `Level`, `Grid`, `Document.Phases`, `PhaseFilter.GetPhaseStatusPresentation`,
`View.ViewTemplateId/GetFilters/Scale/GenLevel`, `ParameterFilterElement`,
`SelectionFilterElement`, `Viewport`, `ScheduleSheetInstance`,
`Category.SubCategories/GetLineWeight/LineColor/GetLinePatternId/GetGraphicsStyle`,
`CurveElement.LineStyle`, `LinePatternElement`, `FillPatternElement`,
`DimensionType.StyleType`, `TextNoteType`, `Family.GetFamilySymbolIds`,
`SharedParameterElement.GuidValue`, `Document.ParameterBindings` (`InstanceBinding`),
`Material`, `ViewSchedule.Definition`, `ViewSheet`, `ExportDWGSettings`
(`GetActivePredefinedSettings`), `ExportPDFSettings`, `PrintSetting`,
`ViewSheetSet`, `Document.GetUnusedElements` (Revit 2024+), `LabelUtils.GetLabelFor`.

## Testes
- Unitário (wrapper, `tests/unit/test_audit_tools.py`): payload padrão só com
  `limit`; `sections` e `naming` enviados quando dados; validação sem ir ao
  Revit; resposta de sucesso devolvida inteira.
- Unitário (rota, `tests/unit/test_audit_route_logic.py`, API falsa):
  validação, regex de nome inteiro, `summarize`/`unused_first`, checklist de 19
  itens, uso de modelos e filtros, vistas e tabelas fora de prancha, famílias
  por contagem de instâncias e pela API de limpeza, prévia de limpeza, erro
  isolado por seção, 503/400 na rota; recuo para contagem quando
  `GetUnusedElements` devolve vazio, orçamento de tempo e corpo vazio.
- `tests/unit/test_publication_security.py` cobre a autorização antes de tudo.

## Validação manual no Revit
- [ ] Abrir o template `.rte` do escritório e rodar `audit_template()` sem
      argumentos: responde em menos de 240 s e sem `section_errors`.
- [ ] `unused_source` é `"revit_purge"` no Revit 2024 (confirma `GetUnusedElements`).
- [ ] `GetUnusedElements` com conjunto vazio devolve ids no Revit 2024 (sem
      `unused_source_note`); se vier a nota, conferir se o conjunto vazio é lido
      como "todas as categorias".
- [ ] Projeto grande: se passar de 240 s, `time_budget.skipped_sections` lista o
      que faltou e a segunda chamada só com essas seções completa.
- [ ] A contagem de `purge_preview` bate com a janela *Limpar itens não utilizados*.
- [ ] `browser_organization.items[0].folders` mostra os parâmetros de agrupamento
      reais (em português) da organização atual.
- [ ] `phases.phase_filters[].presentation` coincide com Gerenciar > Fases > Filtros de fase.
- [ ] Estilos de linha: cores e padrões batem com Gerenciar > Estilos de linha.
- [ ] `naming` com a regra de nomes do escritório lista as vistas fora do padrão.
- [ ] Rodar num projeto grande (`.rvt` representativo) e medir o tempo.
