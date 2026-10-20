# Changelog

Mudanças do fork `buzfam/mcp-server-for-revit-python`. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Mudanças herdadas
do upstream aparecem só como "Sincronizado com upstream `<sha>`".

## [Unreleased]

### Adicionado
- Modo remoto (spec 006, [guia](docs/acesso-remoto.md)): `main.py --http --host <IP>`
  permite que um cliente MCP em outra máquina controle o Revit do PC, com token
  remoto próprio (`--remote-token new|revoke`, DPAPI), lista de `Host` aceitos
  (`--allowed-host`) e TLS opcional (`--ssl-certfile/--ssl-keyfile`).
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
- `PairingMiddleware` recebe o validador por parâmetro (pareamento no modo local, token remoto no modo rede).
- `CLAUDE.md` deixa de ser ignorado pelo `.gitignore`.
- README com aviso de fork apontando para `FORK.md`.

### Base
- Fork criado a partir do upstream `6ca46b3`.
