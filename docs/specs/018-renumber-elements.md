# 018 — renumber_elements

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogo de aprovação: "Renumber elements")
- **Rota:** `POST /renumber_elements/` em `revit_mcp/renumber.py` (módulo novo)
- **Wrapper:** `tools/renumber_tools.py` (módulo novo)
- **Origem:** proposta Carol Vale Arquitetura (numeração de portas, janelas, ambientes e folhas)

## Problema
Renumerar à mão é lento e sujeito a erro: portas pelo número do ambiente
("P-101-01", "P-101-02"), janelas por pavimento, ambientes em sequência por
pavimento, folhas em sequência mantendo a ordem (A-101..A-110) ou a Marca de
qualquer categoria. A tool gera os números a partir de um padrão, na ordem de
leitura da planta ou na ordem atual, e aplica tudo de uma vez (tudo ou nada),
com prévia de antes/depois.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `target` | str | — | `doors`/`portas`, `windows`/`janelas`, `rooms`/`ambientes`, `sheets`/`folhas`/`pranchas`, ou o nome de qualquer categoria (português, inglês ou `OST_`) |
| `pattern` | str | `"{prefix}{seq}"` | Texto com tokens (abaixo); precisa ter `{seq}` |
| `parameter` | str \| null | null | Parâmetro de texto a escrever. Padrão: Marca (`ALL_MODEL_MARK`); `ROOM_NUMBER` para ambientes; `SHEET_NUMBER` para folhas. Aceita o nome em português do Revit ou o nome do `BuiltInParameter` |
| `prefix` | str \| null | `""` | Valor de `{prefix}` |
| `start` | int | 1 | Primeiro número da sequência (≥ 0) |
| `step` | int | 1 | Incremento (≥ 1) |
| `order` | str \| null | `location` (`current` para folhas) | `location`, `current` ou `id` |
| `group_by` | str \| null | null | `room` (portas, janelas e outras instâncias) ou `level`: a sequência recomeça em cada grupo |
| `level` | str \| null | null | Só elementos deste nível |
| `view_name` / `view_id` | str / int | null | Só elementos visíveis nesta vista (planta, forro, corte, elevação, detalhe ou 3D; tabelas, folhas, legendas e vistas de desenho → 400); a fase da vista é usada quando `phase` não é dado |
| `element_ids` | list[int] \| null | null | Só estes elementos (1–200) |
| `phase` | str \| null | null | Nome da fase (sem diferenciar maiúsculas) usada para achar o ambiente das portas/janelas e para escolher quais ambientes renumerar. Não vale para folhas |
| `dry_run` | bool | true | Só prévia (transação desfeita) |

### Tokens do padrão
| Token | Valor |
|---|---|
| `{seq}` | Número da sequência; `{seq:02}`, `{seq:03}`... completam com zeros (`{seq:0N}`, N de 1 a 9) |
| `{prefix}` | O parâmetro `prefix` |
| `{level}` | Nome do nível do elemento |
| `{room_number}` / `{room_name}` | Portas/janelas: ambiente do lado "para" (`ToRoom`), senão do lado "de" (`FromRoom`); outras instâncias: `Room`; ambientes: o próprio ambiente (valores atuais) |

### Fase
Ordem: `phase` (por nome), senão a fase da vista (`VIEW_PHASE`) quando há
filtro de vista, senão a última fase do projeto. A fase usada volta em
`"phase"`. Para portas/janelas, quando a fase é só a padrão (última) e não há
ambiente nela, tenta-se também a fase de criação do elemento
(`CreatedPhaseId`); o item mostra `room_phase` nesse caso. Com `phase` ou vista
explícitos não há essa segunda tentativa.

Chaves soltas (`{` ou `}` sem par) e tokens desconhecidos são recusados (400 /
`Error:` no wrapper).

### Ordem
- `location` (padrão): ordem de leitura da planta. Por elevação do nível; dentro
  do nível, em faixas de cima (norte, Y maior) para baixo, e cada faixa da
  esquerda para a direita (X menor primeiro). Pontos cujo Y difere até 0,5 m do
  primeiro ponto da faixa ficam na mesma faixa. Ponto = `LocationPoint`, meio da
  `LocationCurve` ou centro da caixa envolvente; sem ponto vai para o fim do nível.
- `current`: ordem natural do valor atual (`A-2` antes de `A-10`; vazios no fim).
  Padrão para folhas, que não têm posição na planta (`location` é recusado).
- `id`: ordem do ElementId.
- Com `group_by`, os grupos seguem o número do ambiente (ordem natural) ou a
  elevação do nível; dentro do grupo vale a ordem escolhida.

## Requisição para a rota
```json
{
  "target": "doors",
  "pattern": "P-{room_number}-{seq:02}",
  "group_by": "room",
  "order": "location",
  "start": 1,
  "step": 1,
  "level": "Térreo",
  "dry_run": true
}
```

## Resposta
Prévia (`dry_run`) e sucesso:
```json
{
  "status": "dry_run",
  "message": "Dry run: Revit accepted all 3 new values. Nothing was changed; call again with dry_run=false to apply.",
  "target": "doors", "phase": "Nova construção",
  "pattern": "P-{room_number}-{seq:02}", "order": "location", "group_by": "room",
  "unique_parameter": false,
  "changed": 3, "unchanged": 1, "skipped": {},
  "results": [
    {"element_id": 501, "before": "", "after": "P-101-01", "level": "Térreo", "room_number": "101"}
  ],
  "warnings": []
}
```
`results` lista só os elementos que mudam; os demais entram em `unchanged`.
`status: "success"` no modo real. Se nada muda (todos já têm o valor pedido),
volta `success` com `changed: 0` sem transação nem diálogo.

Erro de planejamento ou do Revit (nada é alterado):
```json
{"status": "error", "error": "1 of 3 new values clash; nothing was changed",
 "changed": 0, "error_count": 1,
 "errors": [{"element_id": 502, "error": "'A-102' is already used by an element outside the batch"}],
 "target": "sheets", "skipped": {}}
```
`skipped` conta `unplaced_rooms` (ambientes não colocados ou não fechados),
`other_phase_rooms` (ambientes de outra fase) e `placeholder_sheets` (folhas de
espaço reservado, puladas quando não há `element_ids`).

## Regras e casos de borda
- Sem documento aberto → 503. Entrada malformada, categoria, nível ou vista
  inexistente, fase inexistente, vista que não mostra elementos de modelo,
  nenhum elemento encontrado ou mais de 200 elementos → 400.
- `authorize_request(request, action=None)` é a primeira linha da rota; o
  diálogo "Renumber elements" só aparece no modo real, depois que o plano foi
  validado e só se algo muda.
- **Tudo ou nada.** Antes da transação: parâmetro inexistente, ambíguo,
  somente leitura ou que não é texto; porta sem ambiente quando o padrão usa
  `{room_*}` ou `group_by="room"`; elemento sem nível quando o padrão usa
  `{level}` ou `group_by="level"`; elemento dentro de grupo de modelo ("edit
  the group in Revit", para não desagrupar sem querer); `element_ids` que não
  são do alvo (ou não visíveis na vista, ou ambiente de outra fase) →
  `status: error` por elemento, nada é escrito.
- **Ambientes são por fase:** no Revit, número de ambiente repetido só gera
  aviso entre ambientes da mesma fase (`ROOM_PHASE`). Por isso só os ambientes
  da fase usada são renumerados e comparados; os de outras fases ficam em
  `skipped.other_phase_rooms`.
- **Colisões:** valores novos repetidos dentro do lote são sempre erro (compara
  sem diferenciar maiúsculas e espaços nas pontas). Valor novo já usado por um
  elemento fora do lote: erro para parâmetros únicos (`SHEET_NUMBER` no modelo
  inteiro, incluindo folhas de espaço reservado; `ROOM_NUMBER` entre os
  ambientes da mesma fase, incluindo os não colocados); só aviso (`warnings`) para Marca e
  outros parâmetros.
- **Troca dentro do lote** (A-101 ↔ A-102): para parâmetros únicos, todos os
  elementos que mudam recebem primeiro um valor temporário único
  (`MCP-TMP-<id>`) e depois o valor final, na mesma transação.
- Uma transação "MCP: renumber elements" dentro de um `TransactionGroup`
  (`revit_mcp/transactions.py`, `Batch`); a
  prévia faz `Commit` (o Revit processa as falhas) e desfaz o grupo; o modo real
  assimila o grupo (um único desfazer). Avisos do Revit vão para `warnings`
  (sem diálogo); erros do Revit desfazem tudo.
- Limite de 200 elementos por chamada; até 100 erros listados (`error_count` tem o total).

## Revit API
`FilteredElementCollector` (com `view.Id` para o filtro de vista),
`OfCategory`/`OfCategoryId`/`OfClass(ViewSheet)`, `ViewSheet.IsPlaceholder`,
`FamilyInstance.get_ToRoom(phase)` / `get_FromRoom(phase)` / `get_Room(phase)`,
`Document.Phases`, `Element.CreatedPhaseId`, `Element.GroupId`, `View.ViewType`,
`BuiltInParameter.VIEW_PHASE`, `ROOM_PHASE`, `ROOM_NUMBER`, `ROOM_NAME`,
`SHEET_NUMBER`, `ALL_MODEL_MARK`, `Parameter.Set(string)`,
`Parameter.Definition.BuiltInParameter`, `LocationPoint`/`LocationCurve`,
`Level.Elevation`, `Transaction`, `TransactionGroup.Assimilate`,
`IFailuresPreprocessor`. Sem diferenças conhecidas entre Revit 2024 e 2026.

## Testes
- Unitário (wrapper, `tests/unit/test_renumber_tools.py`): endpoint, padrão
  sem chaves `None`, todas as opções, validação sem chamar o Revit.
- Unitário (rota, `tests/unit/test_renumber_route_logic.py`, API falsa): ordem
  natural, padrão, ordem de leitura, grupos, colisões, valor temporário,
  portas por ambiente (ToRoom → FromRoom, última fase), janelas por nível,
  ambientes com não colocados, ambientes por fase (pulados, colisão só na
  mesma fase, `phase` por nome), fase da vista × `phase`, fase de criação como
  segunda tentativa, elementos em grupo, vistas não gráficas, `results` só com
  o que muda, troca de números de folhas, recusa do Revit
  desfaz tudo, filtros de vista/ids/nível, parâmetro somente leitura ou
  numérico, fluxo da rota (400/503, aprovação só no modo real).
- Integração (Windows + Revit): pendente.

## Validação manual no Revit
- [ ] Portas por ambiente: `target="portas"`, `pattern="P-{room_number}-{seq:02}"`, `group_by="room"`; conferir na prévia que cada porta pegou o ambiente certo (lado "para") e a ordem de leitura.
- [ ] Porta de fachada (um lado sem ambiente) usa o ambiente do outro lado; porta sem ambiente nenhum dá erro por elemento.
- [ ] Com `view_name` de uma planta em fase "Existente", o ambiente é o da fase da vista; com `phase=` explícito, vale a fase dada.
- [ ] Porta criada na fase "Existente" e sem ambiente na última fase pega o ambiente da fase de criação (`room_phase` na resposta).
- [ ] Porta dentro de um grupo de modelo → erro por elemento, grupo intacto.
- [ ] `view_name` de uma tabela, folha ou legenda → 400.
- [ ] Janelas por nível com `{level}` e `group_by="level"`.
- [ ] Ambientes em sequência por pavimento (`target="ambientes"`, `level=...`, `start=101`); ambientes não colocados aparecem em `skipped`.
- [ ] Número de ambiente já usado em outro pavimento **da mesma fase** → erro de colisão; usado só em outra fase → aceito (o Revit compara números de ambiente por fase). Ambientes de outra fase aparecem em `skipped.other_phase_rooms`.
- [ ] Folhas: troca A-101 ↔ A-102 com `element_ids` e `order="id"`; conferir que `MCP-TMP-*` não sobra em nenhuma folha.
- [ ] Folha com número já usado fora do lote → erro, nada alterado.
- [ ] Marca de outra categoria (`target="Mobiliário"`, `pattern="M{seq:03}"`) e parâmetro por nome em português (`parameter="Marca"`).
- [ ] Marca repetida em elemento fora do lote aparece em `warnings` (e o aviso do Revit de marca duplicada também).
- [ ] Prévia não altera nada (Desfazer vazio); modo real é um único Desfazer "MCP: renumber elements".
- [ ] Diálogo de aprovação "Renumber elements" aparece uma vez no modo real; nada aparece na prévia.
- [ ] Mais de 200 elementos → 400 pedindo filtro.
