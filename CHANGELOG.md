# Changelog

Mudanças do fork `buzfam/mcp-server-for-revit-python`. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Mudanças herdadas
do upstream aparecem só como "Sincronizado com upstream `<sha>`".

## [Unreleased]

### Adicionado
- `scripts/smoke.py`: homologação automática que chama todas as tools de leitura
  contra um Revit real (local por stdio ou remoto pelo IP + token), confere a
  recusa sem token/token errado e gera `smoke-report.json`. Roteiro em
  `docs/homologacao.md`.
- Modo remoto (spec 006, [guia](docs/acesso-remoto.md)): `main.py --http --host <IP>`
  permite que um cliente MCP em outra máquina controle o Revit do PC, com token
  remoto próprio (`--remote-token new|revoke`, DPAPI), lista de `Host` aceitos
  (`--allowed-host`) e TLS opcional (`--ssl-certfile/--ssl-keyfile`).
- Botão **MCP > Remote > Remoto** no Revit: ver e copiar o token e o comando de
  conexão, gerar novo token, revogar, iniciar/parar o servidor remoto em segundo
  plano (instala o `uv` com consentimento) e ver o log. Sem terminal.
- Tool `get_selected_elements` (spec 001): elementos selecionados pelo usuário,
  com categoria, família, tipo e nível. Rota `POST /selection/`.
- Tool `get_element_parameters` (spec 002): parâmetros de instância e tipo com
  valor bruto, valor exibido, tipo de armazenamento e se é editável. Rota
  `POST /element_parameters/`.
- `element_id_from_int` em `revit_mcp/utils.py` (construtor de `ElementId` por versão do Revit).
- Guias para agentes de IA: `AGENTS.md`, `CLAUDE.md` e skills em `.claude/skills/`
  (`add-mcp-tool`, `sync-upstream`, `session-handoff`).
- `FORK.md` (propósito e roadmap do fork), `HANDOFF.md` e este `CHANGELOG.md`.
- Specs em `docs/specs/`: 001 `get_selected_elements`, 002 `get_element_parameters`,
  003 `bulk_set_parameters`, 004 `model_health_report`, 005 fábrica (hub + agentes).

### Alterado
- `main.py` usa `argparse`; flags antigas (`--sse`, `--http`, `--streamable-http`, `--combined`) continuam iguais.
- Armazenamento do token remoto movido para `revit_mcp/remote_token.py` (compatível com IronPython e CPython).
- `PairingMiddleware` recebe o validador por parâmetro (pareamento no modo local, token remoto no modo rede).
- `CLAUDE.md` deixa de ser ignorado pelo `.gitignore`.
- README com aviso de fork apontando para `FORK.md`.

### Base
- Fork criado a partir do upstream `6ca46b3`.
