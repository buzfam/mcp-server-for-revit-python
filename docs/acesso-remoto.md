# Acesso pela rede (modo remoto)

Permite que um agente (Claude Code, Claude Desktop ou outro cliente MCP) em
**outra máquina** controle o Revit de um PC, informando o IP desse PC.

```
Seu agente (outra máquina) ──HTTP(S) + token remoto──▶ PC do Revit: main.py :8000
                                                          │ 127.0.0.1 + pareamento (igual a hoje)
                                                          ▼
                                                    pyRevit Routes :48884 → Revit
```

- Só o servidor MCP (`main.py`) escuta na rede. A ponte `main.py → Revit`
  continua **só em localhost**; o pyRevit Routes nunca é exposto.
- O cliente remoto usa um **token remoto**, separado do pareamento do Revit.
  Revogar o token remoto não quebra o uso local.
- Ações que alteram o modelo continuam abrindo o **diálogo de aprovação no
  Revit**: alguém no PC precisa clicar *Sim*.

Spec: [`docs/specs/006-network-access.md`](specs/006-network-access.md).

## No PC do Revit (Windows) — pelo botão, sem terminal

Tudo fica em **MCP > Remote > Remoto** no ribbon do Revit:

1. **MCP > Bridge > Connect** (pareamento, uma vez por usuário Windows).
2. **Remoto > Iniciar servidor remoto**. Na primeira vez:
   - cria o token remoto se ainda não existir;
   - se o `uv` não estiver instalado, oferece instalar (baixa de astral.sh, só
     para o seu usuário);
   - o primeiro início baixa as dependências e pode levar alguns minutos;
   - o Windows pode perguntar sobre o firewall: permita **só em redes
     privadas/de domínio**.
3. Quando o servidor sobe, aparece a janela com os **IPs do PC, a porta, o
   token** e o **comando pronto para o Claude Code**, com botões
   *Copiar comando do Claude Code* e *Copiar token*.

Outras opções do menu **Remoto**:

| Opção | O que faz |
|---|---|
| Ver token e comando de conexão | Mostra de novo o token, os IPs e o comando, com botões de copiar |
| Gerar novo token | Troca o token; o antigo para de funcionar na hora (sem reiniciar o servidor) |
| Revogar token | Apaga o token; nenhum agente remoto conecta até criar outro |
| Parar servidor remoto | Encerra o servidor em segundo plano |
| Ver log do servidor | Últimas linhas de `%LOCALAPPDATA%\RevitMCPPython\remote-server.log` |

O servidor roda em segundo plano (sem janela), escuta em `0.0.0.0:8000` e
**continua rodando se o Revit fechar**; use *Parar servidor remoto* para encerrar.

### Restringir o firewall (recomendado)

Para aceitar conexões só da máquina do agente, rode uma vez como administrador
(troque o IP):
```powershell
New-NetFirewallRule -DisplayName "Revit MCP" -Direction Inbound -Protocol TCP `
  -LocalPort 8000 -RemoteAddress 192.168.0.50 -Action Allow -Profile Domain,Private
```

### Alternativa pelo terminal

```powershell
uv run --frozen main.py --remote-token new          # cria/troca o token (mostra uma vez)
uv run --frozen main.py --http --host 0.0.0.0 --port 8000
uv run --frozen main.py --remote-token revoke       # revoga
```
Com um nome em vez de IP (ex.: `revit-pc01.empresa.local`), acrescente
`--allowed-host revit-pc01.empresa.local`; nomes fora da lista recebem erro 421.

## No agente (outra máquina)

**Claude Code:**
```bash
claude mcp add --transport http revit-pc01 http://192.168.0.20:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

**Claude Desktop** (via [`mcp-remote`](https://www.npmjs.com/package/mcp-remote), precisa de Node.js):
```json
{
  "mcpServers": {
    "revit-pc01": {
      "command": "npx",
      "args": ["mcp-remote", "http://192.168.0.20:8000/mcp",
               "--header", "Authorization:${AUTH_HEADER}", "--allow-http"],
      "env": {"AUTH_HEADER": "Bearer <TOKEN>"}
    }
  }
}
```

Vários PCs = uma entrada por PC (`revit-pc01`, `revit-pc02`…), cada uma com o
IP e o token daquele PC.

## TLS (recomendado fora de uma rede confiável)

Sem TLS, o token trafega em texto puro na rede e o servidor avisa ao iniciar.
Com um certificado (da CA interna da empresa, ou gerado com `mkcert`):

```powershell
uv run --frozen main.py --http --host 0.0.0.0 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

O cliente passa a usar `https://...` (no `mcp-remote`, sem `--allow-http`).

## Problemas comuns

| Sintoma | Causa |
|---|---|
| `401 MCP pairing required` | Token remoto ausente, errado ou revogado (confira em *Remoto > Ver token*) |
| `421 Invalid Host header` | O agente usa um nome/IP fora da lista: acrescente `--allowed-host` |
| Tool responde "Use Revit's MCP > Bridge > Connect" | Falta o pareamento no Revit daquele PC |
| Conexão recusada / timeout | Firewall do Windows, IP errado ou servidor parado (*Remoto > Iniciar*) |
| Servidor não inicia | *Remoto > Ver log do servidor*; na 1ª vez precisa de internet para baixar dependências |
| Escrita "trava" | O diálogo de aprovação está aberto no Revit esperando alguém no PC |
