# 017 — export_sheets_pdf

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita de arquivos fora do modelo (diálogo de aprovação: "Export sheets to PDF"); o modelo não muda, sem transação
- **Rota:** `POST /export_sheets_pdf/` em `revit_mcp/pdf_export.py` (módulo novo)
- **Wrapper:** `tools/pdf_export_tools.py` (módulo novo)
- **Origem:** proposta Carol Vale Arquitetura, item R6 (espec. §10 e §13)

## Problema
A emissão final em PDF é feita à mão: escolher as folhas, imprimir, renomear
cada arquivo pela regra do escritório (projeto, folha, revisão, data) e anotar
o que foi emitido. A tool exporta as folhas com o **exportador de PDF nativo**
do Revit (2022+, sem impressora virtual), já com o nome certo, e grava um
manifesto `emissao.json` (quais folhas, qual revisão, qual arquivo) para a
gestão registrar no Notion e para o histórico de revisões da §10.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `sheets` | list[str] \| null | null | Números das folhas (1–200), na ordem desejada. Número inexistente → 404 e nada é exportado |
| `sheet_set` | str \| null | null | Nome de um conjunto de folhas salvo (`ViewSheetSet`, "Imprimir > Configurar"). Não pode vir junto com `sheets` |
| `folder` | str \| null | null | Pasta de saída. Padrão: `<Documentos>\RevitMCP\Emissoes\<projeto>\<AAAA-MM-DD_HHMMSS>`. Na execução real, passe a pasta devolvida pela prévia |
| `issued_at` | str \| null | null | Data-hora ISO devolvida pela prévia (`2026-10-21T09:05:12`): mantém `{date}` e a pasta padrão iguais entre prévia e execução real |
| `name_pattern` | str | `{project_number}_{sheet_number}_{revision}_{date}` | Nome do arquivo (sem `.pdf`) |
| `combine` | bool | false | Um único PDF com todas as folhas |
| `paper_format` | str | `default` | `default` = tamanho de cada folha; ou um nome de `ExportPaperFormat` (`ISO_A1`) |
| `zoom_percent` | int \| null | null | 10–500; null = ajustar à página |
| `color` | str | `color` | `color`, `grayscale`, `black_line` |
| `raster_quality` | str | `high` | `low`, `medium`, `high`, `presentation` |
| `hide_crop_boundaries` | bool | true | Ocultar regiões de recorte |
| `hide_scope_boxes` | bool | true | Ocultar caixas de escopo |
| `hide_reference_planes` | bool | true | Ocultar planos de referência |
| `hide_unreferenced_view_tags` | bool | true | Ocultar marcas de vista não referenciadas |
| `overwrite` | bool | false | Substituir arquivos que já existem na pasta (com cópia de segurança, abaixo) |
| `dry_run` | bool | true | Só planeja (pasta e nomes); nada é escrito |

Limites: a prévia planeja até 200 folhas; a execução real gera no máximo
**60 PDFs separados por chamada** (com `combine=true`, um PDF de até 200
folhas). O wrapper espera `max(600, 20 × folhas)` segundos quando sabe quantas
folhas são, 1800 s com `sheet_set` ou todas, e 120 s na prévia. **Depois de um
timeout o Revit pode continuar exportando**: confira a pasta e o
`emissao.json` (ou rode a prévia de novo e veja `exists`) antes de repetir.

Sem `sheets` nem `sheet_set`: todas as folhas, menos as de espaço reservado e
as não imprimíveis, em ordem natural de número (A-2 antes de A-10).

### Regra de nome
- Tokens: `{project_number}` `{project_name}` (Informações do projeto),
  `{sheet_number}` `{sheet_name}`, `{revision}` e `{revision_date}` (revisão
  atual da folha: `SHEET_CURRENT_REVISION` / `SHEET_CURRENT_REVISION_DATE`),
  `{date}` (data da exportação, `AAAA-MM-DD`). Token desconhecido → 400.
- Caracteres proibidos pelo Windows (`<>:"/\|?*` e de controle) nos valores
  viram `-`; no texto fixo do padrão são recusados. Acentos são mantidos.
- Token vazio some junto com os separadores (`_ - . espaço`) antes dele:
  folha sem revisão → `P-07_A-101_2026-10-21`. Separadores nas pontas são
  removidos; nome vazio vira `Emissao`; nomes reservados (`CON`, `NUL`...)
  ganham `_`; nome com mais de 120 caracteres é cortado.
- Nomes repetidos (sem diferenciar maiúsculas) ganham `_2`, `_3` e aparecem em
  `renamed_to_avoid_duplicates`.
- `combine=true`: os tokens de folha ficam vazios e `{revision}` só é
  preenchido se todas as folhas têm a mesma revisão.

### Pasta
- Dada pelo usuário: caminho absoluto num drive local (`D:\Projetos\...`).
  Recusa caminho de rede/UNC, relativo, com `.`/`..`, raiz do drive, trecho
  terminado em ponto/espaço, trecho com `~` (nome curto 8.3), trecho cujo nome
  base é dispositivo reservado (`CON`, `NUL`, `COM1`, `LPT1`, `con.txt`...) e
  pastas do sistema (`Windows`, `Program Files`, `Program Files (x86)`,
  `ProgramData` e os valores de `SystemRoot`, `ProgramFiles`, `ProgramData`
  do ambiente).
- Checagens no disco (via .NET, também para a pasta padrão): o drive precisa
  ser `DriveInfo.DriveType` `Fixed` ou `Removable` (recusa unidade de rede
  mapeada e `SUBST` que aponte para rede); nenhuma pasta existente no caminho
  pode ser junção ou link simbólico (`FileAttributes.ReparsePoint`).
- É criada se não existir (só na execução real).
- Caminho final acima de 259 caracteres → 400.

## Requisição para a rota
```json
{"sheets": ["A-101", "A-102"], "folder": "D:\\Projetos\\Casa X\\Emissao",
 "name_pattern": "{project_number}_{sheet_number}_{revision}_{date}",
 "combine": false, "paper_format": "default", "color": "color",
 "raster_quality": "high", "hide_crop_boundaries": true, "hide_scope_boxes": true,
 "hide_reference_planes": true, "hide_unreferenced_view_tags": true,
 "overwrite": false, "dry_run": true}
```

## Resposta
Prévia (`dry_run=true`):
```json
{"status": "dry_run", "message": "Dry run: 2 PDF file(s) for 2 sheet(s) planned. ...",
 "folder": "D:\\Projetos\\Casa X\\Emissao", "folder_exists": false, "combine": false,
 "manifest": "D:\\Projetos\\Casa X\\Emissao\\emissao.json",
 "files": [{"file": "P-07_A-101_R02_2026-10-21.pdf", "sheets": ["A-101"], "revision": "R02", "exists": false},
           {"file": "P-07_A-102_2026-10-21.pdf", "sheets": ["A-102"], "revision": null, "exists": false}],
 "skipped": [{"sheet_number": "A-900", "sheet_name": "Reservada", "reason": "placeholder"}]}
```
A prévia também devolve `issued_at` (para repetir na execução real) e
`warnings` de opções não críticas que o Revit recusou.

Se algum PDF já existe: na prévia vem `exists: true` e `warning`; na execução
real sem `overwrite=true` → HTTP 409 com `existing` e nada é exportado. Um nome
planejado que é uma **pasta** → 409 com `conflicts`.

### Aprovação
O diálogo do Revit mostra o destino resolvido, não só o pedido: o texto da
ação é `Export N PDF(s) to <pasta>` (+ ` and REPLACE K existing file(s)`), e a
prévia do pedido ganha `_resolved_folder`, `_files` (nomes planejados) e, se
houver substituição, `_will_replace` (caminhos completos). Se um arquivo
planejado aparecer na pasta enquanto o diálogo está aberto → 409, nada é
exportado (não foi aprovado para substituir).

### Substituição (`overwrite=true`) sem perda de dados
1. Antes de exportar, cada PDF existente é renomeado para
   `<nome>.pdf.bak-<AAAAMMDD_HHMMSS>`. Se algum não puder ser renomeado (aberto
   num leitor de PDF), as renomeações já feitas são desfeitas → HTTP 423
   `file in use or locked: ...`, nada é exportado.
2. Depois da exportação: o `.bak` de quem saiu certo é apagado; o de quem
   falhou volta ao nome original.
3. O `emissao.json` anterior **nunca é apagado**: o novo é gravado em
   `emissao.json.tmp` e movido para o lugar; o anterior fica como
   `emissao.json.bak-<carimbo>`. Cada execução acrescenta uma linha (JSON
   ASCII) em `emissao_historico.jsonl`.

Execução real:
```json
{"status": "success", "folder": "D:\\...\\Emissao", "combine": false,
 "manifest": "D:\\...\\Emissao\\emissao.json", "exported": 2,
 "files": [{"sheet_number": "A-101", "sheet_name": "Planta Térreo", "revision": "R02",
            "revision_date": "20/10/26", "revision_description": "Emissão executivo",
            "file": "P-07_A-101_R02_2026-10-21.pdf", "size_bytes": 482113}],
 "skipped": [], "errors": []}
```
Falha em parte das folhas: `status: "error"`, `error: "1 of 2 PDF file(s) failed; the others were written"`,
`errors: [{"file", "sheets", "error"}]` e os arquivos gerados continuam em `files`
(o wrapper devolve o JSON inteiro nesse caso). A resposta real traz ainda
`issued_at`, `history` (caminho do `.jsonl`), `exported` e `replaced`.

### Opções de `PDFExportOptions`
- Críticas: `FileName`, `Combine`, `PaperFormat`, `ZoomType`
  (`ZoomPercentage` quando há zoom), `ColorDepth`. Se o Revit recusar uma
  delas → HTTP 501 com `details`, **antes** do diálogo e de qualquer escrita.
- Não críticas (viram `warnings`): `RasterQuality`, os quatro `Hide*`,
  `StopOnError=False`, `PaperPlacement=Center`.
- `paper_format` é conferido contra `System.Enum.GetNames(ExportPaperFormat)`,
  sem diferenciar maiúsculas.

Motivos em `skipped`: `placeholder`, `not_printable`, `not_a_sheet` (vista que
não é folha dentro do conjunto).

### Manifesto `emissao.json`
ASCII puro (acentos como `\uXXXX`, escrito por um gerador próprio porque
`json.dumps` falha com acento no IronPython):
```json
{"project_number": "P-07", "project_name": "Casa \u00c1rvore", "document": "Casa \u00c1rvore",
 "issued_at": "2026-10-21T09:05:12", "user": "carol", "revit_version": "Autodesk Revit 2024 (24.2.0)",
 "folder": "D:\\...", "combined": false,
 "files": [{"sheet_number": "A-101", "sheet_name": "Planta T\u00e9rreo", "revision": "R02",
            "revision_date": "20/10/26", "revision_description": "...", "file": "....pdf", "size_bytes": 482113}],
 "skipped": [], "errors": []}
```

## Regras e casos de borda
- Corpo que não é objeto JSON → 400.
- Sem documento aberto → 503. Revit sem `PDFExportOptions` (anterior a 2022) → 501.
- Entrada inválida (padrão, pasta, cor, zoom, `sheets` + `sheet_set`) → 400, antes do diálogo.
- `paper_format` que não existe em `ExportPaperFormat` → 400.
- Conjunto de folhas inexistente → 404 com `available_sheet_sets`.
- Mais de 200 folhas selecionadas → 400 (dividir por `sheets`/`sheet_set`).
- Nenhuma folha imprimível → 400 com `skipped`.
- O diálogo de aprovação aparece só na execução real, depois das validações.
- Uma chamada `Document.Export` por arquivo, com `Combine=true` e `FileName`:
  com `Combine=false` o Revit ignora `FileName` e usa a regra de nomes dele;
  assim o nome sai exato. Com `combine=true`, uma só chamada com todas as
  folhas, na ordem pedida (ou por número).
- Depois de cada exportação o arquivo é conferido (existe, tamanho). O
  manifesto é gravado quando ao menos um PDF saiu.

## Revit API
`DB.PDFExportOptions` (Revit 2022+): `FileName`, `Combine`, `ColorDepth`
(`ColorDepthType`), `RasterQuality` (`RasterQualityType`), `PaperFormat`
(`ExportPaperFormat`), `ZoomType` (`ZoomType.FitToPage`/`Zoom`),
`ZoomPercentage`, `HideCropBoundaries`, `HideScopeBoxes`, `HideReferencePlane`,
`HideUnreferencedViewTags`; `Document.Export(String, IList<ElementId>, PDFExportOptions)`;
`ViewSheet.IsPlaceholder`, `View.CanBePrinted`, `ViewSheetSet.Views`;
`ProjectInfo.Number`/`Name`; `BuiltInParameter.SHEET_CURRENT_REVISION`,
`SHEET_CURRENT_REVISION_DATE`, `SHEET_CURRENT_REVISION_DESCRIPTION`;
`Application.VersionName`/`VersionBuild`; `PDFExportOptions.StopOnError`,
`PaperPlacement` (`PaperPlacementType.Center`); `System.Enum.GetNames`,
`System.IO.DriveInfo.DriveType`, `System.IO.File.GetAttributes`. Cada
propriedade das opções é atribuída dentro de `try/except` (críticas → erro,
outras → `warnings`).

## Testes
- Unitário (wrapper, `tests/unit/test_pdf_export_tools.py`): payload padrão sem
  `None`, todas as opções, `sheet_set`, falha parcial mantém `files`, validações sem chamar o Revit.
- Unitário (rota, `tests/unit/test_pdf_export_route_logic.py`): regra de nome,
  tokens vazios, nomes reservados e repetidos, validação de pasta (8.3,
  dispositivos), checagens de disco com funções injetadas, pasta padrão,
  gerador de JSON ASCII (acentos, emoji, ordem, uma linha), prévia sem
  escrever, `issued_at`, exportação real com manifesto e histórico, prévia do
  diálogo, `combine`, conjunto de folhas, folha inexistente, arquivo
  existente/`overwrite` com backup, restauração em falha, arquivo travado
  (423), arquivo que aparece durante o diálogo, nome planejado que é pasta,
  limite de 60, opção crítica recusada, corpo não-objeto.

## Validação manual no Revit
- [ ] Revit 2024 em português: `dry_run` sem parâmetros lista as folhas em ordem natural e pula as de espaço reservado.
- [ ] Execução real: diálogo "Export sheets to PDF" aparece; PDFs saem com o nome exato (`FileName` com `Combine=true` por folha).
- [ ] Conferir que o Revit acrescenta `.pdf` ao `FileName` (o arquivo esperado é `<nome>.pdf`).
- [ ] PDF no tamanho da folha (A1/A2/A3) com `paper_format="default"` e ajustar à página; e com `zoom_percent=100` a escala impressa confere.
- [ ] `PaperFormat`, `ZoomType`, `ZoomPercentage`, `RasterQuality`, `ColorDepth` e os quatro `Hide*` aceitos sem `warnings` no 2024.
- [ ] Regiões de recorte, caixas de escopo e planos de referência não aparecem no PDF.
- [ ] `combine=true`: um PDF com as folhas na ordem pedida.
- [ ] `sheet_set` com nome acentuado encontra o conjunto salvo.
- [ ] Revisão atual e data da revisão corretas no nome e no `emissao.json`.
- [ ] Nome de folha com acento e `/` gera arquivo válido; `emissao.json` abre no Notion/Python com acentos certos.
- [ ] Pasta padrão criada em Documentos (inclusive com Documentos redirecionado para o OneDrive).
- [ ] Pasta em `C:\Windows` e caminho `\\servidor\...` recusados.
- [ ] Arquivo existente → 409 sem `overwrite`; com `overwrite=true` substitui.
- [ ] `Document.Export` chamado de dentro da rota (contexto do pyRevit Routes) não reclama de transação aberta nem de contexto de API.
- [ ] Tempo de exportação de ~30 folhas dentro do timeout (600 s).
- [ ] `FileName` com pontos (`P-07_A-101_R1.2_2026-10-21`): o Revit não corta o nome no primeiro ponto nem troca a extensão.
- [ ] `combine=true`: a ordem das páginas segue a ordem da lista de ids.
- [ ] Arquivo criado na pasta com o diálogo aberto → 409, nada exportado.
- [ ] PDF aberto no leitor com `overwrite=true` → 423 e os arquivos ficam como estavam.
- [ ] Falha numa folha com `overwrite=true` → o PDF anterior volta; sem `.bak` sobrando.
- [ ] Segunda emissão na mesma pasta: `emissao.json.bak-*` guardado e `emissao_historico.jsonl` com duas linhas.
- [ ] Unidade de rede mapeada (`Z:`) e pasta que é junção recusadas; pendrive (`Removable`) aceito.
- [ ] Diálogo mostra `_resolved_folder`, `_files` e `_will_replace`.
- [ ] `StopOnError` e `PaperPlacement` aceitos (sem `warnings`) no 2024.
