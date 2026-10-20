# AGENTS.md — buzfam/mcp-server-for-revit-python

Guia para agentes de IA (Claude Code, Codex, Kiro etc.) que trabalham neste
repositório. Leia também `HANDOFF.md` (estado atual e próximos passos) antes de
começar qualquer tarefa.

## O que é

Servidor MCP que dá a um LLM acesso a um Revit aberto. Este repositório é o
**fork da org buzfam** de `mcp-servers-for-revit/mcp-server-for-revit-python`.
As mudanças do fork **não são enviadas para o upstream**: veja `FORK.md`.

Projeto irmão: [`buzfam/pyrevit-cvarq-plugins`](https://github.com/buzfam/pyrevit-cvarq-plugins)
(plugins pyRevit sem LLM, como o autosave). Se uma ideia não precisa de LLM nem
de MCP, ela pertence ao projeto irmão.

## Arquitetura (dois processos, dois Pythons)

```
Cliente MCP (Claude)  --stdio/HTTP-->  main.py + tools/    (CPython 3.11+, FastMCP)
                                           |  HTTP 127.0.0.1:48884, Bearer de pareamento
                                           v
                       startup.py + revit_mcp/  (IronPython 2.7, dentro do Revit, pyRevit Routes)
```

| Pasta/arquivo | Roda em | Papel |
|---|---|---|
| `main.py` | CPython | Cria o FastMCP, `revit_get`/`revit_post`/`revit_image`, transports |
| `tools/*_tools.py` | CPython | Uma função `register_<x>_tools(mcp, ...)` por módulo; cada tool chama uma rota |
| `tools/catalog.py` | CPython | **Descrição pública** de cada tool (fonte única, usada também no manifest) |
| `tools/registration.py` | CPython | `ReviewedTools`: exige pareamento, aplica descrição e `READ_ONLY` |
| `startup.py` | IronPython | Registra os módulos de rotas no `routes.API("revit_mcp")` |
| `revit_mcp/*.py` | IronPython | Rotas `@api.route(...)` que chamam a Revit API |
| `revit_mcp/security.py` | IronPython | `authorize_request`: pareamento + diálogo de aprovação no Revit |
| `revit_mcp/pairing.py` | Ambos | Segredo DPAPI por usuário Windows |
| `MCP.tab/` | IronPython | Botões Connect/Disconnect/Privacy no ribbon |
| `mcp-tool-manifest.json` | — | Inventário gerado; o CI falha se divergir das tools registradas |

## Comandos

```bash
uv sync --frozen --extra test                                    # dependências
uv run --frozen --extra test python -m pytest tests/unit -q      # testes unitários (rodam no Mac/Linux; alguns pulam fora do Windows)
uv run --frozen python scripts/export_mcp_manifest.py            # regenerar o manifest depois de mudar tools
uv run --frozen python scripts/export_mcp_manifest.py --check    # o que o CI roda
uv run --frozen --extra test python -m pytest tests/integration -m integration  # só no Windows com Revit aberto e pareado
```

Baseline em 2026-10-20 (macOS): `97 passed, 9 skipped`; manifest com 20 tools.

## Regras obrigatórias

1. **IronPython 2.7 em `revit_mcp/`, `startup.py` e `MCP.tab/`**: sem f-strings,
   sem type hints, sem `pathlib`, use `"{}".format(...)` e `# -*- coding: utf-8 -*-`.
   `main.py` e `tools/` são CPython 3.11+ e podem usar sintaxe moderna.
2. **Toda rota chama `authorize_request` antes de qualquer coisa.**
   - Só leitura: `authorize_request(request, action=None)`.
   - Altera o modelo, arquivos ou executa código: `action="<verbo legível>"`,
     que abre o diálogo de aprovação com o payload. Nunca remova esse diálogo.
3. **Alterações no modelo dentro de `DB.Transaction(doc, "<nome>")`**, uma por
   requisição (em lote: uma transação para o lote todo, não uma por elemento).
4. **Tool nova = 6 lugares** (use a skill `add-mcp-tool`):
   rota em `revit_mcp/`, registro em `startup.py`, wrapper em `tools/`,
   registro em `tools/__init__.py`, descrição em `tools/catalog.py`,
   `READ_ONLY` em `tools/registration.py` se for só leitura. Depois:
   regenerar o manifest, teste do wrapper em `tests/unit/`, `CHANGELOG.md`.
5. **A ponte com o Revit é só loopback.** `main.py → pyRevit Routes` sempre
   em `127.0.0.1`; nunca exponha o pyRevit Routes na rede. Acesso pela rede
   existe só por dois caminhos documentados:
   - **modo remoto direto** (spec 006): o `main.py` escuta num IP com `--http --host`,
     autenticado pelo **token remoto** (`tools/remote_access.py`), com a lista de
     `Host` do FastMCP e TLS opcional;
   - **fábrica** (spec 005, futura): hub central + agentes.
   Não crie outro caminho de rede, não aceite o token de pareamento pela rede e
   não desligue a proteção de DNS rebinding.
6. **Respostas JSON pequenas.** Tools de listagem precisam de filtro e limite
   (padrão sugerido: `limit=200`) para não estourar o contexto do LLM.
7. **Erros**: rota devolve `routes.make_response(data={"error": ...}, status=4xx/5xx)`;
   wrapper devolve `format_response(response)`.
8. **Sem Revit no Mac.** Não diga que algo "funciona no Revit" sem validação
   num Windows. Marque no `HANDOFF.md` o que falta validar.

## Git e autoria

- **Não adicione `Co-Authored-By` de IA nem "Generated with Claude Code"** em
  commits ou PRs. O autor de tudo é Carlos Felipe Gomes.
- Remotes: `buzfam` = este fork (destino dos pushes); `origin` = fork pessoal
  `karlipegomes`; upstream original só para sincronizar (skill `sync-upstream`).
- Mensagens no estilo Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Nunca abra PR para `mcp-servers-for-revit/*`. Com `gh pr create`, passe
  sempre `--repo buzfam/mcp-server-for-revit-python`, porque o padrão de um
  fork é o repositório pai.

## Onde está cada coisa

- Estado atual, decisões e próximos passos: `HANDOFF.md`
- Diferenças em relação ao upstream e roadmap: `FORK.md`
- Specs das tools planejadas: `docs/specs/`
- Histórico de mudanças do fork: `CHANGELOG.md`
- Segurança e privacidade herdadas do upstream: `SECURITY.md`, `PRIVACY.md`
