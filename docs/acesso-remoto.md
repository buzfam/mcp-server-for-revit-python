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

## No PC do Revit (Windows)

1. Instale a extensão e faça o pareamento: no Revit, **MCP > Bridge > Connect**.
2. No clone do repositório, crie o token remoto (aparece **uma única vez**;
   guarde num cofre de senhas):
   ```powershell
   uv run --frozen main.py --remote-token new
   ```
3. Libere a porta no firewall **só para a máquina do agente**
   (PowerShell como administrador; troque o IP):
   ```powershell
   New-NetFirewallRule -DisplayName "Revit MCP" -Direction Inbound -Protocol TCP `
     -LocalPort 8000 -RemoteAddress 192.168.0.50 -Action Allow -Profile Domain,Private
   ```
4. Suba o servidor escutando no IP do PC (ou `0.0.0.0` para todas as placas):
   ```powershell
   uv run --frozen main.py --http --host 192.168.0.20 --port 8000
   ```
   O terminal mostra os endereços aceitos. Se o agente for usar um nome
   (ex.: `revit-pc01.empresa.local`), acrescente `--allowed-host revit-pc01.empresa.local`;
   endereços fora da lista são recusados com erro 421.
5. Para revogar o acesso: `uv run --frozen main.py --remote-token revoke`.
   Para trocar o token: rode `--remote-token new` de novo (o antigo deixa de valer).

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
| `401 MCP pairing required` | Token remoto ausente, errado ou revogado |
| `421 Invalid Host header` | O agente usa um nome/IP fora da lista: acrescente `--allowed-host` |
| Tool responde "Use Revit's MCP > Bridge > Connect" | Falta o pareamento no Revit daquele PC |
| Conexão recusada / timeout | Firewall do Windows, IP errado ou `main.py` não está rodando |
| Escrita "trava" | O diálogo de aprovação está aberto no Revit esperando alguém no PC |
