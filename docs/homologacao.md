# Roteiro de homologação (Windows + Revit)

Um roteiro só para validar o fork num PC real. A parte automática é o script
`scripts/smoke.py`, que chama **todas as tools de leitura** e gera um relatório
(`smoke-report.json`). Nada no modelo é alterado por ele.

Ao terminar, envie o `smoke-report.json` e as anotações dos testes manuais
para quem vai corrigir (ou cole no Claude: ele marca os checklists das specs).

## 0. Preparar o PC do Revit
1. Instale/atualize a extensão (pyRevit > Extensions, ou `git pull` + *Reload*).
2. Abra um modelo de teste (pode ser `tests/test.rvt`).
3. **MCP > Bridge > Connect** (pareamento).
4. **Selecione 1 a 3 elementos** (ex.: uma parede e uma porta): o smoke usa a
   seleção para testar `get_selected_elements` e `get_element_parameters`.

## 1. Smoke test local (no próprio PC)
Precisa de terminal no PC (ou peça para alguém rodar uma vez):
```powershell
uv run --frozen python scripts/smoke.py --local
```

## 2. Modo remoto (sem terminal no PC do Revit)
1. **MCP > Remote > Remoto > Iniciar servidor remoto** (aceite instalar o `uv`
   e liberar o firewall em rede privada/domínio, se perguntado).
2. Na janela que abre, **Copiar token** (ou *Ver token e comando de conexão*).
3. Na **sua máquina** (com o clone do repositório):
   ```bash
   uv run --frozen python scripts/smoke.py --url http://<IP-DO-PC>:8000/mcp --token <TOKEN>
   ```
   O smoke remoto também confere que o servidor **recusa** chamadas sem token e
   com token errado (401).

## 3. Como ler o resultado
| Marca | Significado |
|---|---|
| `ok` | Passou |
| `warn` | Funcionou, mas algo merece atenção (ex.: nada selecionado) |
| `skip` | Não testado (ex.: tools de escrita, ou falta de seleção) |
| `FAIL` | Falhou; o motivo está na linha e no `excerpt` do relatório JSON |

Se `get_revit_status` falhar, o resto é pulado: resolva primeiro (pareamento,
pyRevit Routes ligado em *pyRevit > Settings > Routes*, Revit aberto com modelo).

## 4. Testes manuais (o smoke não cobre escrita nem a interface)
Siga os checklists de cada spec e marque o que passou:
- Tools de leitura novas: [001](specs/001-get-selected-elements.md), [002](specs/002-get-element-parameters.md)
- Modo remoto e botão **Remoto**: [006](specs/006-network-access.md)
- Escrita pelo agente remoto: peça ao agente um `color_splash` numa vista e
  confira que o **diálogo de aprovação** aparece no Revit; recuse uma vez e
  aceite outra.
- Autosave (outro repositório): `pyrevit-cvarq-plugins/docs/specs/001-autosave.md`

## 5. Anotar
- Versão do Revit e do pyRevit, Windows, se o PC está em domínio.
- Para cada falha: o que fez, o que esperava, a mensagem (ou print).
