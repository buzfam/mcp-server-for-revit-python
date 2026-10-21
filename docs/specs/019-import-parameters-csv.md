# 019 — import_parameters_from_csv

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogo de aprovação: `"Set element parameters"`, o mesmo do `bulk_set_parameters`); `dry_run` é leitura
- **Rota:** nenhuma nova. O wrapper chama `POST /bulk_set_parameters/` (003) e, com `match_by="room_number"`, `POST /export_rooms/` (010)
- **Wrapper:** `tools/csv_import_tools.py` (módulo novo, só CPython)
- **Depende de:** 003 (`bulk_set_parameters`, que ganhou `normalize_numbers`, agrupamento de alterações repetidas e o aviso para `""` em parâmetro não texto) e 010 (`export_rooms`)

## Problema
O escritório mantém informações em planilhas Excel (acabamentos por ambiente,
marcas de portas, comentários de revisão) e hoje redigita tudo no Revit. Com
esta tool o usuário cola a planilha (ou aponta o `.csv` salvo pelo Excel) e o
LLM grava todos os valores de uma vez, com um único diálogo de aprovação e um
único Ctrl+Z.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `csv_text` | str | — | Texto da planilha (cabeçalho + linhas). Use este **ou** `file_path` |
| `file_path` | str | — | `.csv` dentro da pasta de importação (ver Segurança); nome simples é procurado nela |
| `match_by` | `"element_id"` \| `"room_number"` | `"element_id"` | Como achar o elemento de cada linha |
| `key_column` | str | `element_id`/`id` ou `number`/`Número` | Cabeçalho da coluna-chave |
| `columns` | {cabeçalho: parâmetro} | todas as outras colunas | Renomeia e/ou escolhe as colunas usadas |
| `ignore_columns` | list[str] | — | Colunas a pular (quando `columns` não é passado) |
| `delimiter` | `";"`, `","`, `"tab"` | detectado | Separador; `csv.Sniffer` numa amostra, conferido contra o cabeçalho; senão contagem no cabeçalho (empate → `;`) |
| `normalize_numbers` | bool | True | Repassado à rota: números de parâmetros Double são reescritos no símbolo decimal do projeto (`"2,40"` ↔ `"2.40"`); texto nunca muda |
| `set_empty` | bool | False | Envia células vazias como `""`: limpa parâmetros de texto; nos demais a rota pula com aviso |
| `use_type` | bool | False | Grava no tipo do elemento (repassado ao `bulk_set_parameters`) |
| `level` | str | — | Com `room_number`: só ambientes deste nível |
| `dry_run` | bool | True | Só valida e mostra antes/depois (repassado ao `bulk_set_parameters`) |

## Requisição para a rota
Para `bulk_set_parameters` (a da spec 003, com `normalize_numbers`):
```json
{
  "dry_run": true,
  "use_type": false,
  "normalize_numbers": true,
  "changes": [
    {"element_id": 501, "parameter": "Acabamento de piso", "value": "Porcelanato"},
    {"element_id": 502, "parameter": "Acabamento de piso", "value": "Cerâmica"}
  ]
}
```
Com `match_by="room_number"`, antes disso:
`POST /export_rooms/ {"format": "json", "limit": 1000, "include_unplaced": true, "include_details": false, "level": "Térreo"}`.

## Resposta
A resposta do `bulk_set_parameters`, com `row` (linha da planilha) e `column`
(cabeçalho) acrescentados a cada item de `results`/`errors`, mais
`import_report`:
```json
{
  "status": "dry_run",
  "message": "Dry run: 2 changes are valid. ...",
  "applied": 0,
  "results": [{"index": 0, "element_id": 501, "parameter": "Acabamento de piso",
               "before": "", "after": "Porcelanato", "ok": true, "row": 2,
               "column": "Acabamento de piso"}],
  "number_format": {"decimal_symbol": ",", "normalized": 0},
  "import_report": {
    "source": "acabamentos.csv", "encoding": "cp1252", "delimiter": ";",
    "header_row": 1, "key_column": "Número", "match_by": "room_number",
    "mapped_columns": [{"column": "Acabamento de piso", "parameter": "Acabamento de piso"}],
    "ignored_columns": [], "rows_read": 3, "rows_used": 2, "rows_skipped": 1,
    "skipped": [{"row": 4, "reason": "no room with this number"}],
    "changes": 2
  }
}
```
A rota também pode devolver `warnings` (`""` pulado em parâmetro não texto),
`collapsed` (alteração repetida com o mesmo valor, aplicada uma vez) e, em
cada resultado reescrito, `sent` (o texto enviado ao Revit); o wrapper
acrescenta `row`/`column` também nesses itens.

Erros de validação no wrapper voltam como texto `Error: ...` sem chamar o
Revit (com `import_report` quando a planilha já foi lida: conflitos entre
linhas, "no changes to apply", mais de 500 alterações).

## Segurança
- `file_path` só lê `.csv` dentro da pasta de importação: `REVIT_MCP_IMPORT_DIR`
  ou, por padrão, `~/Documents/RevitMCP/Importar`. O caminho é resolvido com
  `os.path.realpath` (links simbólicos inclusos) e precisa ter
  `os.path.commonpath` igual à pasta; `..`, caminho absoluto fora dela e
  pasta "irmã" com o mesmo prefixo são recusados.
- Com o servidor acessível pela rede (modo remoto, spec 006) `file_path` fica
  desligado: o `main.py` chama `set_remote_mode(True)`; como reserva, a tool
  também recusa se `sys.argv` tiver `--host` fora do loopback.
- Motivos de linha pulada nunca citam o texto das células.

## Regras e casos de borda
- Cabeçalho = primeira linha não vazia. Números de linha são os do Excel
  (linhas vazias contam; um campo entre aspas com quebra de linha continua
  sendo uma linha só).
- Cabeçalhos: espaços nas pontas e BOM removidos; comparação sem diferenciar
  maiúsculas, espaços, `_` e `-` (`Element ID` = `element_id`).
- Cabeçalho repetido entre as colunas usadas → erro (renomear ou usar
  `columns`/`ignore_columns`). Duas colunas para o mesmo parâmetro → erro.
  Coluna com cabeçalho vazio é ignorada.
- Arquivo: só `.csv`, até 5 MB; decodificação UTF-16 (com BOM), UTF-8 (com ou
  sem BOM) e, se falhar, Windows-1252 (o "CSV (separado por vírgulas)" do Excel
  em português). `.xlsx` é recusado: salvar como CSV.
- Linhas puladas (com motivo e número da linha): chave vazia, id inválido,
  ambiente não encontrado, número de ambiente repetido (pede `level`), todas
  as células de parâmetro vazias. Linhas totalmente vazias só são contadas.
- Várias linhas para o mesmo elemento: parâmetros diferentes são somados;
  o mesmo parâmetro com o mesmo valor vira uma alteração só (`collapsed`, com
  `same_as_row`); com valores diferentes é **erro** (`conflicts`: linhas,
  coluna, elemento) e nada vai ao Revit.
- `use_type=true`: elementos do mesmo tipo escrevem no mesmo parâmetro; a rota
  agrupa por (tipo, parâmetro): valores iguais → aplica uma vez (`collapsed`),
  diferentes → erro listando os índices.
- Valores vão como texto, com espaços das pontas removidos. Com
  `normalize_numbers` (padrão no import) a rota lê o símbolo decimal do
  projeto (`doc.GetUnits().DecimalSymbol`) e reescreve só números de
  parâmetros Double: os dois separadores presentes → o último é o decimal;
  um separador uma vez → decimal; repetido → agrupamento (`"1.234.567"`).
  Atenção: `"1.234"` vira 1,234 (decimal), não mil e duzentos.
- Mais de 500 alterações (linhas × colunas) → erro sugerindo quantas linhas
  por chamada. `dry_run=true` por padrão; o diálogo de aprovação é o do
  `bulk_set_parameters` (uma vez para o lote).
- `room_number`: busca até 1000 ambientes (limite do `export_rooms`); se
  houver mais, `import_report.warning` pede `level`. Números comparados sem
  diferenciar maiúsculas e com espaços internos colapsados (como
  `exports._key`). Número repetido: se exatamente um for "placed", ele vence.
  Filtro por fase não existe porque o `export_rooms` não expõe a fase (futuro).

### Chaves estudadas e decisão
Rotas existentes analisadas para casar uma linha com um elemento:
- `/element_parameters/`: lê parâmetros, mas exige `element_ids` (máx. 50 por chamada).
- `/current_view_elements/`: devolve ids, nome e categoria, **sem** valores de parâmetros.
- `/list_category_parameters/`: lista nomes de parâmetros, não elementos.
- `/selection/`: só a seleção atual.
- `/export_rooms/`: devolve `element_id` + `number` + `level` de todos os ambientes. ✔
- `/export_schedule/`: devolve as células da tabela, sem ids.

Por isso: chave `element_id` (direto) e `room_number` (via `export_rooms`).
**Futuro:** casar por valor de parâmetro qualquer (ex.: `Marca` de portas) ou
por `UniqueId` precisa de uma rota nova de leitura (ex.:
`find_elements_by_parameter {category, parameter, values}`); combinar
`current_view_elements` + `element_parameters` em lotes de 50 seria lento e
limitado à vista atual.

## Revit API
Usa as rotas 003 (`Parameter.Set`/`SetValueString` numa `Transaction`; novo:
`Document.GetUnits().DecimalSymbol` para `normalize_numbers`, `Parameter.Id`
e `Element.GetTypeId()` para agrupar) e 010 (`FilteredElementCollector` de
`OST_Rooms`).

## Testes
- Unitário (`tests/unit/test_csv_import_tools.py`): BOM, UTF-16, cp1252,
  pasta de importação (`..`, absoluto fora, link simbólico, pasta irmã),
  modo remoto, detecção de separador (Sniffer, aspas com quebra de linha no
  cabeçalho, vírgula decimal num arquivo `;`), linhas vazias, cabeçalho
  deslocado, cabeçalhos repetidos e com espaços, ids inválidos sem citar a
  célula, duplicatas (somar, colapsar, conflito), payload do
  `bulk_set_parameters`, casamento por número de ambiente (placed vence,
  espaços), limite de 500, `row`/`column` nos resultados.
- Rota (`tests/unit/test_route_logic.py`): `normalize_number_text`,
  `group_changes`, `project_decimal_symbol`, `use_type` com tipo
  compartilhado (igual → colapsa; diferente → erro), `""` em não texto →
  aviso, `normalize_numbers` só em Double.
- Integração: coberta pelas rotas 003 e 010.

## Validação manual no Revit
- [ ] Exportar ambientes com `export_rooms(format="csv")`, editar no Excel pt-BR (salvar CSV com `;`) e importar com `file_path` + `columns={"name": "ROOM_NAME"}`: dry run mostra antes/depois corretos e acentos íntegros.
- [ ] `match_by="room_number"` com coluna `Número` e parâmetro de acabamento: dry run, depois `dry_run=false` → um diálogo, valores gravados, um Ctrl+Z desfaz tudo.
- [ ] Número de ambiente repetido em dois níveis: linha pulada pedindo `level`; com `level` passa.
- [ ] Parâmetro de comprimento com `"2,40"` e com `"2.40"`: com `normalize_numbers` os dois funcionam e `number_format.decimal_symbol` bate com Gerenciar > Unidades do projeto; um parâmetro de texto com `"2,40"` fica igual.
- [ ] `use_type=true` com duas portas do mesmo tipo e valores diferentes → erro; com valores iguais → aplicado uma vez (`collapsed`).
- [ ] `set_empty=true` numa coluna de comprimento: `warnings`, sem erro; numa coluna de texto: valor limpo.
- [ ] `file_path` fora da pasta de importação e com o servidor em `--http --host <IP>`: recusado.
- [ ] Coluna inexistente no Revit: dry run devolve erro com `row` e `column` corretos e nada é aplicado.
- [ ] Planilha com mais de 500 alterações: erro sem chamar o Revit.
- [ ] Recusar o diálogo de aprovação: nada muda.
