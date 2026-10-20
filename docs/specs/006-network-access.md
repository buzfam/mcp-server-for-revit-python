# 006 — Acesso pela rede (modo remoto direto)

- **Status:** Implementada (não validada no Revit)
- **Tipo:** transporte (sem tools novas)
- **Código:** `main.py` (CLI, Host allowlist, TLS), `tools/remote_access.py`
  (token remoto), `tools/http_security.py` (validador injetável)
- **Ribbon:** `MCP.tab/Remote.panel/Remoto.pushbutton` + `revit_mcp/remote_server.py`
  (iniciar/parar servidor) + `revit_mcp/remote_token.py` (token compartilhado IronPython/CPython)
- **Guia de uso:** [`docs/acesso-remoto.md`](../acesso-remoto.md)

## Problema
O usuário quer controlar Revits de outros PCs a partir do seu agente,
informando o IP do PC onde rodam o MCP e o Revit. É a etapa simples antes da
fábrica com hub (spec 005).

## Comportamento
- `--host <IP ou 0.0.0.0>` com `--http` (ou `--sse`/`--combined`) liga o modo
  remoto. Com `stdio` o modo remoto é recusado.
- Autenticação HTTP no modo remoto: `Authorization: Bearer <token remoto>`.
  O token de pareamento **não** é aceito pela rede, e o token remoto não é
  aceito pelo pyRevit Routes.
- Requisições com cabeçalho `Origin` (navegador) são recusadas.
- Proteção contra DNS rebinding do FastMCP mantida: só os nomes listados na
  inicialização (IP do `--host`, ou nome/IPs do PC com `0.0.0.0`, mais
  `--allowed-host`) são aceitos no cabeçalho `Host`; os demais recebem 421.
- TLS opcional (`--ssl-certfile` + `--ssl-keyfile`); sem TLS, aviso no stderr.
- A ponte com o Revit continua obrigatoriamente em loopback (`REVIT_HOST`).
- Token remoto: 256 bits aleatórios, DPAPI *CurrentUser* em
  `%LOCALAPPDATA%\RevitMCPPython\remote.dpapi`; `--remote-token new` cria/troca
  (mostra uma vez), `--remote-token revoke` apaga.

## Botão Remoto (sem terminal)
- Menu (`CommandSwitchWindow`): ver token e comando, gerar novo, revogar,
  iniciar/parar servidor, ver log. Opções mudam conforme o estado.
- *Ver token*: IPs IPv4 do PC (sem loopback/169.254), porta, token e o comando
  `claude mcp add ...` pronto; botões de copiar (`script.clipboard_copy`).
- *Iniciar*: exige pareamento; cria o token se faltar; pede confirmação
  (aviso de rede e firewall); procura o `uv` (PATH, `~\.local\bin`,
  `~\.cargo\bin`, `%LOCALAPPDATA%\Programs\uv`) e, com consentimento,
  instala pelo instalador oficial; roda `cmd /c "uv run --frozen main.py --http
  --host 0.0.0.0 --port 8000" >> remote-server.log` sem janela, a partir da
  pasta da extensão; PID em `%LOCALAPPDATA%\RevitMCPPython\remote-server.json`;
  espera até 25 s por `Uvicorn running` no log.
- *Parar*: `taskkill /PID <pid> /T /F` (mata cmd → uv → python).
- Trocar o token vale na hora: o servidor lê o token a cada requisição.

## Fora do escopo (vai para a spec 005)
Descoberta automática de PCs, vários Revits por PC num só endpoint, aprovação
central, auditoria central, cadastro de agentes.

## Testes automatizados
`tests/unit/test_remote_access.py`: validação do token (certo, errado, sem
token, com Origin), CLI (`stdio` + host remoto recusado, TLS em par),
descoberta de nomes, app HTTP real aceitando o IP liberado com o token remoto
e recusando token errado e Host fora da lista (421), ponte do Revit em loopback.
Ida e volta do DPAPI testada só no Windows.

Smoke test feito no macOS (2026-10-20), com o token simulado: servidor em
`0.0.0.0:8765` acessado pelo IP da rede → 401 sem token, 200 com token, tool
executada (resposta de pareamento ausente, esperado sem Revit), 421 com Host forjado.

## Validação manual (Windows)
- [ ] `--remote-token new` mostra o token; `remote.dpapi` não contém o token em texto
- [ ] Servidor com `--host <IP>`; de outra máquina, `claude mcp add ...` lista as 22 tools
- [ ] `get_selected_elements` remoto devolve a seleção do PC
- [ ] Escrita remota (ex.: `color_splash`) abre o diálogo no Revit do PC
- [ ] `--remote-token revoke` → cliente remoto recebe 401
- [ ] Regra de firewall com `-RemoteAddress` bloqueia uma terceira máquina
- [ ] TLS com certificado da CA interna
- [ ] Botão Remoto: menu aparece; sem pareamento, *Iniciar* pede o Connect
- [ ] *Iniciar* sem `uv`: oferece instalar, instala e sobe o servidor
- [ ] Primeiro início (baixa dependências) → mensagem "iniciando"; depois *Ver token* mostra RODANDO
- [ ] *Copiar comando do Claude Code* → colar na outra máquina conecta
- [ ] *Gerar novo token* com servidor rodando → token antigo recebe 401 sem reiniciar
- [ ] *Parar servidor* encerra o processo (porta 8000 livre)
- [ ] Texto com acentos aparece correto nos diálogos (IronPython)
