# HANDOFF — buzfam/mcp-server-for-revit-python

Documento de passagem de bastão entre sessões (pessoas ou agentes). Atualize
ao fim de cada sessão de trabalho: o que mudou, o que falta, o que está travado.

_Última atualização: 2026-10-20_

## Estado atual

- Fork sincronizado com o upstream em `6ca46b3` (merge do PR #69 upstream).
- **Homologação iniciada (2026-10-20)** num PC com Revit 2024 + pyRevit 7.0.0:
  modo remoto validado pela rede (401 sem token/token errado, `initialize`,
  24 tools). Toda rota respondia 401 mesmo pareada: bug do pyRevit Routes, que
  não repassa cabeçalhos (pyRevit #3765). Corrigido mandando o segredo no corpo
  JSON (`revit_mcp_auth`). **Revalidado no PC: 15 ok**, 1 aviso (nada
  selecionado, modelo vazio), 2 falhas corrigidas em seguida (vista com acento e
  categoria em inglês num Revit em português); falta rodar o smoke de novo.
  Botão de pareamento único (Connected/Disconnected) ainda não visto no Revit.
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
- Testes (macOS): `185 passed, 10 skipped`; manifest confere 24 tools.
- CI no Windows (GitHub Actions, 2026-10-20): `190 passed` em Python 3.11 e 3.13
  (inclui os testes de DPAPI) e **IronPython 2.7.12 ok**: 21 arquivos do lado
  Revit compilam, helpers puros rodam com semântica Py2 e o token remoto faz a
  ida e volta pelo DPAPI via .NET.
- **Kit de homologação**: `scripts/smoke.py` + `docs/homologacao.md`. Rodado no
  macOS contra um pyRevit Routes falso: 18 ok, 1 aviso (ver pendências), 0 falhas.

## Próximos passos

1. **Homologar no Windows** seguindo `docs/homologacao.md` (smoke + checklists).
   **Aguardando o usuário avisar** quando for testar no PC; até lá não há como validar.
   Depois da homologação, corrigir o bug do `format_response` (ver Pendências),
   já decidido pelo usuário para depois dos testes.
   Em seguida: **validar o modo remoto no Windows** (checklist da spec 006) quando o
   usuário instalar no PC, começando pelo botão **Remoto** (o usuário não terá
   terminal no PC): token DPAPI, instalação do `uv`, início em segundo plano,
   firewall, cliente em outra máquina.
2. Próximas tools do roadmap (`FORK.md`): 005 folhas, 006 templates em lote, 007 tags, 008 exportar tabelas.
3. Validar 001–004 num Windows com Revit (checklist em cada spec), quando o
   usuário instalar no PC. Ponto de maior risco: `element_id_from_int`
   (overload Int64/Int32 do construtor de `ElementId` no IronPython).

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

## Pendências e riscos

- **Sem Revit no Mac**: tudo que roda em `revit_mcp/` precisa de validação no
  Windows. Nada foi validado no Revit até agora pelo fork.
- O remote `origin` aponta para o fork pessoal `karlipegomes`; o `master` local
  rastreia `buzfam/master`, destino dos pushes.
- Fábrica: confirmar no Windows como o pyRevit numera as portas de vários Revits no mesmo PC.
- **Bug herdado do upstream** em `tools/utils.py::format_response`: se a resposta
  tem `message`, devolve só a mensagem e descarta os dados. Ex.:
  `get_current_view_elements` truncado chega ao LLM só com "Results truncated…".
  O smoke marca isso como `warn`. Correção ainda não feita.

## Ambiente

- Clone local: `~/Documents/StaffOps/Contributing/mcp-server-for-revit-python`
- Remotes: `buzfam` (destino), `origin` (pessoal), `upstream` (adicionar quando sincronizar)
- Projeto irmão: `~/Documents/StaffOps/Contributing/pyrevit-cvarq-plugins`
