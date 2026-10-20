# 005 — Fábrica: hub central + agentes nos PCs

- **Status:** Proposta
- **Tipo:** arquitetura (novo modo de operação; o modo local atual continua igual)
- **Código:** `fleet/hub/` (servidor central) e `fleet/agent/` (um por PC)

## Problema
A empresa terá vários Revits abertos em vários PCs. Hoje cada Revit só pode
ser controlado por um cliente MCP rodando **no mesmo PC** (loopback +
pareamento DPAPI). Queremos controlar todos a partir de um ponto central,
consultar e agir em vários modelos de uma vez, com segurança e auditoria.

## Decisões (2026-10-20)
| Tema | Decisão |
|---|---|
| Tipo de PC | **Estações de trabalho** (arquitetos usando o Revit enquanto a automação age) |
| Aprovação | **Política no hub**: leitura livre; escrita e execução de código precisam de aprovação no hub. O diálogo no Revit continua ligado por padrão (a pessoa no PC é dona da sessão) |
| Onde roda o hub | **VM/servidor na rede interna** |
| Rede | A ponte com o Revit continua **só loopback**; só o agente fala com a rede, por conexão **de saída** para o hub |

## Arquitetura

```
Cliente MCP (Claude) ──HTTPS + token de usuário──▶ HUB (VM)
                                                   ├─ /mcp       servidor MCP (streamable HTTP)
                                                   ├─ /agent     WebSocket dos agentes
                                                   ├─ /approvals página de aprovação
                                                   ├─ registro de agentes/instâncias
                                                   ├─ política + fila de aprovações
                                                   └─ log de auditoria (SQLite)
                                                        ▲ WSS (sai do PC; token do agente)
                     ┌──────────────────────────────────┼─────────────────────────┐
                Agente PC-01                      Agente PC-02               Agente PC-N
                (sessão do usuário Windows)
                  │ HTTP 127.0.0.1 + pareamento DPAPI (igual a hoje)
             pyRevit Routes :48884, :48885 …  (um por Revit aberto)
```

### Agente (`fleet/agent/`, CPython 3.11, Windows)
- Roda **na sessão do usuário** (Agendador de Tarefas "ao fazer logon"), não
  como serviço do Windows: o segredo de pareamento é DPAPI *CurrentUser* e o
  Revit roda na sessão do usuário.
- **Descoberta:** a cada 15 s testa as portas 48884–48893 em `127.0.0.1`
  (`GET /revit_mcp/status/` com o cabeçalho de pareamento) e monta a lista de
  instâncias: porta, versão do Revit, documento ativo, usuário Windows.
- **Conexão:** abre `wss://<hub>/agent` com `Authorization: Bearer <token do agente>`;
  envia `hello` (máquina, usuário, versão do agente) e `heartbeat` com as instâncias.
  Reconexão com backoff exponencial (1 s → 60 s).
- **Execução:** recebe `call {id, port, method, endpoint, data, timeout}` e
  repassa para `http://127.0.0.1:<port>/revit_mcp<endpoint>` usando o mesmo
  código de `main.py` (`_revit_call`). Responde `result {id, status, body}`.
- **Allowlist de endpoints:** só as rotas registradas em `startup.py`; qualquer
  outro endpoint é recusado no agente.
- **Política local** (`%LOCALAPPDATA%\RevitMCPPython\fleet-agent.json`), que o
  hub não pode sobrescrever:
  - `allow_write` (padrão `true`), `allow_code_execution` (padrão `false`);
  - `local_review` (padrão `true`): mantém o diálogo de aprovação no Revit.
- **Consentimento:** o agente só se conecta depois do *Connect* no ribbon
  (pareamento já existente) **e** do cadastro no hub (`enroll`).

### Hub (`fleet/hub/`, CPython 3.11, VM)
- **Servidor MCP** (`/mcp`, streamable HTTP) com:
  - `list_revit_instances(filter?)`: PCs, usuários, versões e documentos abertos;
  - as tools atuais, cada uma com o parâmetro novo **`target`** (ID da instância,
    ex.: `PC-01:48884`, ou apelido);
  - `run_on_instances(tool, args, targets | filter)`: executa uma tool **de
    leitura** em várias instâncias e junta os resultados (fase 3);
  - `get_approval_status(request_id)`.
- **Autenticação:**
  - usuários: token pessoal (`Authorization: Bearer`), guardado como hash; papéis
    `viewer` (só leitura), `operator` (pede escrita), `admin` (aprova e gerencia);
  - agentes: token por agente, emitido no `enroll` e revogável.
- **Política:**
  - tools em `READ_ONLY` (`tools/registration.py`) → executam direto;
  - demais tools → criam um **pedido de aprovação** e respondem
    `{"status": "pending_approval", "request_id": ...}`; um `admin` aprova ou
    recusa em `/approvals` (ou pelo CLI do hub); a tool `get_approval_status`
    devolve o resultado depois;
  - `execute_revit_code` → aprovação **e** `allow_code_execution` no agente.
- **Auditoria:** cada chamada vira uma linha no SQLite: horário, usuário,
  tool, instância, payload, decisão da política, aprovador, status, duração.
  Retenção configurável (padrão 180 dias). O payload pode conter dados de
  projeto: o banco fica só na VM, com backup e acesso restrito.
- **TLS:** obrigatório. Certificado da CA interna da empresa (ou proxy reverso
  Caddy/nginx na frente do uvicorn). Sem TLS o hub não inicia, salvo
  `--insecure-dev` explícito para testes locais.

### Protocolo agente ↔ hub (JSON sobre WebSocket)
```json
{"type": "hello", "agent_id": "pc-01", "machine": "PC-01", "user": "CORP\\ana", "agent_version": "0.1.0"}
{"type": "heartbeat", "instances": [{"port": 48884, "revit_version": "2025", "document": "Torre A.rvt"}]}
{"type": "call", "id": "c-8f2", "port": 48884, "method": "POST", "endpoint": "/selection/", "data": {"limit": 200}, "timeout": 30}
{"type": "result", "id": "c-8f2", "status": 200, "body": {"status": "success", "total": 0}}
```

## O que muda no código atual
- `main.py`: a função `_revit_call` passa para um módulo reutilizável
  (`revit_mcp_client.py` ou `tools/bridge.py`) usado pelo `main.py` e pelo agente.
- `tools/*`: as tools recebem `revit_get/revit_post` injetados (já é assim);
  o hub injeta versões que roteiam para o agente certo a partir de `target`.
  O modo local continua sem `target`.
- `tools/registration.py`: `READ_ONLY` vira a fonte da política do hub.
- `SECURITY.md`/`AGENTS.md`: o modo fábrica é **opt-in**; a regra "só loopback"
  continua valendo para a ponte com o Revit.

## Fases
| Fase | Entrega | Critério de pronto |
|---|---|---|
| 1 | Agente + hub mínimos, **só leitura**, sem TLS (`--insecure-dev`), 1 PC | `list_revit_instances` e `get_selected_elements(target=...)` funcionam de outro PC |
| 2 | Cadastro de agentes, tokens de usuário, TLS, auditoria | Agente sem token é recusado; toda chamada aparece no log |
| 3 | Escrita com aprovação no hub, `run_on_instances`, política local | Escrita fica pendente até aprovação; leitura em 3 PCs de uma vez |
| 4 | Painel web (frota, pedidos, auditoria), instalador do agente | Instalar um PC novo em < 10 min seguindo o README |

## Riscos e pontos em aberto
- **Pessoa usando o PC:** a automação age na sessão de alguém. O diálogo no
  Revit e a política local são a proteção dessa pessoa; comunicar a equipe.
- **Revit ocupado:** com o usuário dentro de um comando, a rota espera
  (`ExternalEvent`). O hub precisa de timeout por chamada e nunca repetir
  escrita automaticamente.
- **Licenças Autodesk:** cada Revit aberto usa uma licença; nada muda nas
  estações de trabalho, mas automação sem ninguém usando exigiria revisão.
- **Portas:** confirmar no Windows como o pyRevit atribui portas a várias
  instâncias do Revit no mesmo PC (hipótese: 48884 + n).
- **LGPD:** o log guarda nomes de usuários e dados de projeto; definir retenção
  e quem tem acesso.

## Validação manual
- [ ] Fase 1: dois PCs na mesma rede, hub numa VM, `list_revit_instances` mostra os dois
- [ ] Dois Revits no mesmo PC aparecem como instâncias diferentes
- [ ] Fechar o Revit some da lista em até 30 s; derrubar a rede reconecta sozinho
- [ ] Fase 3: escrita pendente → aprovação → diálogo no Revit → aplicada; recusa no hub não chega ao Revit
