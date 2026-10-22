# HANDOFF — buzfam/mcp-server-for-revit-python

Documento de passagem de bastão entre sessões (pessoas ou agentes). Atualize
ao fim de cada sessão de trabalho: o que mudou, o que falta, o que está travado.

_Última atualização: 2026-10-22_

## Estado atual

- Fork sincronizado com o upstream em `6ca46b3` (merge do PR #69 upstream).
- **Homologação iniciada (2026-10-20)** num PC com Revit 2024 + pyRevit 7.0.0:
  modo remoto validado pela rede (401 sem token/token errado, `initialize`,
  24 tools). Toda rota respondia 401 mesmo pareada: bug do pyRevit Routes, que
  não repassa cabeçalhos (pyRevit #3765). Corrigido mandando o segredo no corpo
  JSON (`revit_mcp_auth`). **Revalidado no PC: 15 ok**, 1 aviso (nada
  selecionado, modelo vazio), 2 falhas corrigidas em seguida (vista com acento e
  categoria em inglês num Revit em português). **Smoke após as correções: 16 ok,
  0 falhas reais** (vista "Nível 1" exportada; "Walls" achada como Paredes; só
  avisos de modelo vazio). Depois, com 2 paredes criadas pelo MCP: parâmetros
  lidos, `bulk_set_parameters` em "Comentários" com dry run, recusa (403), aceite
  e **um Ctrl+Z desfazendo as duas**. Botões Connected/Disconnected e Atualizar
  funcionando. Achados e corrigidos no caminho: cabeçalhos do pyRevit 7, acentos
  (JSON da ponte, `json.dumps` no diálogo, URL da vista), categorias e parâmetros
  em português. Checklists marcados nas specs; o que falta está desmarcado lá.
  Botão de pareamento único (Connected/Disconnected) ainda não visto no Revit.
- **2026-10-21: 9 tools novas (33 no total), não validadas no Revit**: folhas
  (007), templates (008), tags (009), exportação (010) e modelagem (011:
  paredes, ambientes, portas/janelas). Todas de escrita têm `dry_run` padrão,
  tudo ou nada e um Ctrl+Z. Checklists de validação no fim de cada spec. Pontos
  de maior risco: `IndependentTag.Create` e detecção de tags existentes, o
  `FailuresPreprocessor` suprimir os diálogos de aviso no Revit 2024, e
  `NewRoomBoundaryLines` numa vista que não é a ativa. Também: `format_response`
  corrigido (dados não somem quando há mensagem) e botão **Bridge > Log**.
- **2026-10-21 (2): 5 tools de leitura (38 no total), não validadas no Revit**:
  `audit_template` (012), `check_sheets` (013), `check_render_materials` (014),
  `room_finishes` + `quantities_by_rule` (015), itens R1–R4 da proposta do
  hermes-vps. Riscos: `Document.GetUnusedElements` (esperado no 2024),
  `BrowserOrganization`, tempo da auditoria num `.rvt` grande (timeout 120 s),
  "viewport vazio" na prática, tamanho das esquadrias (`size_source`) e fases
  em FromRoom/ToRoom. Padrões de medição (vãos > 2 m² etc.) **precisam ser
  confirmados com o escritório**.
- **2026-10-21 (3): 6 tools novas (44 no total), não validadas no Revit**: fases
  (016), PDF nativo (017), renumeração (018), pisos/forros pelos ambientes (020);
  CSV (019) integrado depois da revisão (45 tools). **Toda tool desta leva e das anteriores (007–015) passou
  por revisão de outro agente** e as correções foram aplicadas (ver CHANGELOG).
  `revit_mcp/transactions.py` (`Batch`) é o padrão para escrita: grupo de
  transação, avisos na resposta, dry run que roda e desfaz. `startup.py` registra
  cada módulo separadamente. Histórico de aprovações: botão **Bridge > Histórico**.
- 24 tools: as 20 do upstream + `get_selected_elements` (001),
  `get_element_parameters` (002), `bulk_set_parameters` (003) e
  `model_health_report` (004), no `master`. **Nenhuma validada no Revit.**
- **Modo remoto direto (spec 006) implementado**: `main.py --http --host <IP>` +
  token remoto. Testado no macOS (unitários + smoke test pela rede com token
  simulado). **Não validado no Windows/Revit.** Guia: `docs/acesso-remoto.md`.
- Botão **MCP > Remote > Remoto** (sem terminal): token, comando de conexão,
  iniciar/parar servidor, log. **Nunca rodou num Revit** (IronPython, .NET Process,
  diálogos do pyRevit conferidos só no código-fonte do pyRevit).
- Spec 005 (fábrica com hub) escrita e **adiada** a pedido do usuário: primeiro
  funcionar com o IP direto.
- Guias de agente criados: `AGENTS.md`, `CLAUDE.md`, `FORK.md`, specs 001–004,
  skills `add-mcp-tool`, `sync-upstream` e `session-handoff`.
- Testes (macOS, 2026-10-21): `669 passed, 10 skipped`; manifest confere 38 tools; CI Windows + IronPython 2.7.12 verde em `6c20134`.
- CI no Windows (GitHub Actions, 2026-10-20): `190 passed` em Python 3.11 e 3.13
  (inclui os testes de DPAPI) e **IronPython 2.7.12 ok**: 21 arquivos do lado
  Revit compilam, helpers puros rodam com semântica Py2 e o token remoto faz a
  ida e volta pelo DPAPI via .NET.
- **Kit de homologação**: `scripts/smoke.py` + `docs/homologacao.md`. Rodado no
  macOS contra um pyRevit Routes falso: 18 ok, 1 aviso (ver pendências), 0 falhas.

## Próximos passos

1. **Próximo teste no PC: seguir `docs/roteiro-de-testes.md`** (2026-10-22): ~250 itens com
   o pedido ao agente e o resultado esperado de cada um, a ordem do teste mínimo da
   primeira sessão, os riscos e as decisões a confirmar com o escritório. O resumo
   abaixo continua valendo como visão geral (o usuário avisa quando for testar).
   Antes: **MCP > Update > Atualizar**. Abrir um projeto com famílias de porta e
   janela e um carimbo carregados (o modelo vazio da homologação não tinha nenhum).
   Rodar o smoke primeiro (`scripts/smoke.py`). Depois, em ordem:
   - [ ] **Token**: *Remoto > Gerar novo token* (o atual vazou no chat); o token
         antigo deve receber 401 sem reiniciar o servidor.
   - [ ] **Sem janela de erro do pyRevit**: mandar código com erro proposital pelo
         `execute_revit_code`; o agente recebe o erro e nenhuma janela abre.
   - [ ] **Botão Bridge > Log** mostra esse erro no `revit-mcp.log`.
   - [ ] **"Permitir tudo até fechar o Revit"**: depois de escolher, escritas
         seguintes não pedem diálogo; *Connected > Voltar a pedir aprovação* religa.
   - [ ] **`format_response`**: `get_current_view_elements` com `limit` pequeno
         devolve os elementos junto com o aviso de corte (o smoke deixa de dar aviso).
   - [ ] **Modelagem (011)**: refazer a planta da homologação (9,30 × 6,25 m,
         8 ambientes) com `create_walls` → `create_rooms` → `place_hosted_families`
         (portas e janelas). Conferir dry run sem alterar o modelo, um diálogo só,
         um Ctrl+Z, áreas em m² e que avisos do Revit não abrem janela.
   - [ ] **Folhas (007)**: `create_sheets` com o carimbo padrão e um escolhido;
         `place_views_on_sheets` com a planta e uma tabela.
   - [ ] **Templates (008)**: `apply_view_template` por tipo de vista.
   - [ ] **Tags (009)**: `tag_elements` em ambientes, portas e janelas; rodar de
         novo e conferir que pula as já etiquetadas.
   - [ ] **Exportação (010)**: `export_rooms` (bater áreas com o Revit) e
         `export_schedule` em CSV.
   - [ ] **Conferências (012–015)**, de preferência num `.rvt` real do escritório:
         `audit_template` (comparar a prévia com *Purge Unused*), `check_sheets`
         com um checklist de exemplo da spec 013, `check_render_materials` num
         móvel conhecido, `room_finishes` (bater um ambiente à mão) e
         `quantities_by_rule` em CSV.
   - [ ] **016–020**: fases num projeto de reforma, PDF (nome, pasta,
         `emissao.json`, substituição com backup, arquivo aberto no Acrobat → 423),
         renumerar portas por ambiente e trocar A-101 ↔ A-102, pisos/forros pelos
         ambientes (altura do forro, fase). Checklists no fim de cada spec.
   - [ ] **Painel Conferir (sem IA)**: os 4 botões com o agente desligado; Emitir
         PDF com arquivo aberto no Acrobat; Auditar template no `.rte`.
   - [ ] **CSV (019)**: `DecimalSymbol` em Revit pt-BR é "Comma"? planilha do
         Excel pt-BR com `;`; `file_path` recusado no modo remoto.
   - [ ] **Botão Bridge > Histórico** mostra permitidos/recusados/automáticos.
   - [ ] Itens ainda abertos das specs 001–004 e 006 (checklists em cada spec):
         seleção grande truncada, parâmetro compartilhado, Sim/Não, `"110 cm"`,
         *Parar servidor* liberando a porta 8000.
   Riscos a observar: `IndependentTag.Create` e a detecção de tags existentes; o
   `FailuresPreprocessor` suprimir os diálogos de aviso no Revit 2024;
   `NewRoomBoundaryLines` numa vista que não é a ativa; face da parede com
   `location_line`.
2. Corrigir o que o teste acima encontrar e marcar as specs como validadas.
3. Próximas ideias (não decididas): ver a lista em "Ideias" abaixo.

## Ideias (não decididas, 2026-10-21)

- Próximos da proposta do hermes-vps (P1): `phases_report` + `set_element_phases`,
  `export_sheets_pdf` (exportador nativo) com manifesto da emissão, botões sem IA
  no ribbon reaproveitando 013/014/PDF.
- `create_floors` / `create_ceilings` a partir do contorno dos ambientes.
- `renumber_elements` (portas por ambiente, folhas em sequência) com dry run.
- `duplicate_views` em lote (dependentes, com detalhamento) e `create_levels` / `create_grids`.
- `import_parameters_from_csv`: planilha → `bulk_set_parameters` com mapeamento de colunas.
- `purge_unused` com prévia (famílias, tipos, materiais) e relatório no `model_health_report`.
- `export_sheets_pdf` (PDF/DWG em lote) — escreve arquivos, precisa de aprovação e pasta permitida.
- Botão no ribbon com o histórico de aprovações da sessão (auditoria local).
- Fábrica (spec 005): hub central para vários PCs.

### Sugestões do hermes-vps (2026-10-21, a partir da Espec. da Carol Vale Arquitetura)

Documento completo: `buzfam/cvarq-skills` → `docs/propostas/2026-10-21-mcps-plugins-revit.md` (commit 99843fe).

- **P1 leitura:** `audit_template` (19 itens da §11.2, marca o que está sem uso,
  inclui o purge com prévia); `check_sheets` (checklist JSON por tipo de
  prancha antes da emissão); `check_render_materials` (regras de material da
  §7.3 antes do Lumion); `room_finishes` + `quantities_by_rule` (quantitativos
  com a regra de medição junto de cada número).
- **P1 escrita/arquivo:** `phases_report` + `set_element_phases` (reforma);
  `export_sheets_pdf` com nome pela regra do escritório e manifesto JSON da
  emissão; botões sem IA no ribbon (Checar pranchas, Checar materiais, Emitir
  PDF, Novo projeto) reaproveitando o código das rotas.
- **P2:** `link_survey_dxf` + `walls_from_dxf` (paredes existentes a partir da
  layer do levantamento); `get/set_project_location` + `setup_sun_study`
  ("configurado, não validado"); `revisions_report` + `add_revision_to_sheets`;
  `quantities_snapshot` + `quantities_diff`.
- Endossa da nossa lista: importar CSV, renumerar, purge (dentro do audit),
  pisos/forros, histórico de aprovações. Adiaria: duplicar vistas, níveis/eixos, fábrica.
- Antes de reescrever, ler servidores MIT/Apache que já cobrem partes:
  HorizunGroup/horizun-revit-mcp, LuDattilo/revit-mcp-server,
  Demolinator/revit-mcp-server. Conferir a API de exportação PDF do Revit 2024.

## Decisões tomadas

| Data | Decisão | Motivo |
|---|---|---|
| 2026-10-20 | Mudanças do fork não vão para o upstream | Liberdade de escopo; upstream mira o Autodesk Marketplace |
| 2026-10-20 | Autosave e plugins sem LLM ficam em `buzfam/pyrevit-cvarq-plugins` | MCP só reage a pedidos; autosave silencioso não combina com o diálogo de aprovação |
| 2026-10-20 | Priorizar tools de escrita em lote | O MCP nativo do Revit 2027 só lê o modelo |
| 2026-10-20 | Sem `Co-Authored-By` de IA nos commits | Autor único: Carlos Felipe Gomes |
| 2026-10-20 | Rotas de leitura com parâmetros usam POST + JSON | Padrão do repositório (`/current_view_elements/`); nenhuma rota usa query string |
| 2026-10-20 | Campo de ID nas respostas: `element_id` | Consistência com as rotas existentes |
| 2026-10-20 | Fábrica = hub central + agente por PC (conexão de saída); ponte com o Revit segue loopback | Não expor execução de código na rede; token DPAPI não sai do PC; escala para N PCs |
| 2026-10-20 | Fábrica: estações de trabalho, aprovação de escrita no hub, diálogo no Revit mantido, hub numa VM | Decisão do usuário |
| 2026-10-20 | Antes do hub: modo remoto direto pelo IP do PC (spec 006) | Usuário quer funcionar simples primeiro |
| 2026-10-20 | Token remoto separado do pareamento; TLS opcional com aviso | Revogar acesso pela rede sem quebrar o local; facilitar o piloto em LAN |
| 2026-10-20 | Gerenciar token e servidor remoto pelo ribbon (botão Remoto) | O usuário instala via pyRevit e não terá terminal no PC |
| 2026-10-20 | Servidor remoto continua rodando se o Revit fechar | Simplicidade; parar é explícito pelo botão |
| 2026-10-21 | Botões sem IA (painel Conferir) ficam neste repo, não no `pyrevit-cvarq-plugins` | Reaproveitam o código das rotas (`revit_mcp/`); mover exigiria duplicar ou empacotar `revit_mcp`. Reavaliar se o plugin irmão precisar deles sem o MCP instalado |
| 2026-10-21 | `export_sheets_pdf` usa o exportador nativo do Revit (`Document.Export` + `PDFExportOptions`, Revit 2022+) | Preferência do usuário; dispensa impressora PDF virtual |

## Pendências e riscos

- **Sem Revit no Mac**: tudo que roda em `revit_mcp/` precisa de validação no
  Windows. Nada foi validado no Revit até agora pelo fork.
- O remote `origin` aponta para o fork pessoal `karlipegomes`; o `master` local
  rastreia `buzfam/master`, destino dos pushes.
- Fábrica: confirmar no Windows como o pyRevit numera as portas de vários Revits no mesmo PC.
- ~~Bug do `format_response`~~ corrigido em 2026-10-21.

## Ambiente

- Clone local: `~/Documents/StaffOps/Contributing/mcp-server-for-revit-python`
- Remotes: `buzfam` (destino), `origin` (pessoal), `upstream` (adicionar quando sincronizar)
- Projeto irmão: `~/Documents/StaffOps/Contributing/pyrevit-cvarq-plugins`
