# Remote Commander — Beta 0.1.0

**Agente remoto gratuito e local-first para Linux**, com painel web móvel, explorador de arquivos, terminal opt-in, conexão MCP via stdio e assistente de IA local opcional via Ollama. Código aberto sob licença MIT. **Sem dependências Python obrigatórias** e sem limite de chamadas imposto pelo projeto.

> **Estado:** primeira versão beta funcional e experimental. Este projeto **não** fornece acesso remoto gráfico à tela (RDP/VNC), nem disponibiliza automaticamente um conector do ChatGPT. Não é software endurecido para produção.

## Funcionalidades da beta

| Recurso | Disponível? | Condição |
|---|---|---|
| Interface web responsiva (celular/PC) | Sim | Token secreto |
| Navegar e ler arquivos UTF-8 | Sim | Limitado a um workspace |
| Modificar/criar arquivos | Sim | `--allow-write`, confirmação no painel |
| Comandos no Linux | Sim | `--allow-exec`, confirmação no painel |
| Logs locais sem conteúdo de arquivos | Sim | `~/.config/remote-commander/audit.jsonl` |
| IA local que lê e analisa projeto | Sim | Ollama + modelo com *tool calling* |
| IA que propõe escrita ou execução | Sim | `--allow-write` / `--allow-exec`, aprovação humana obrigatória no painel |
| MCP local via stdio | Sim | Cliente MCP compatível, sem token web |
| Acesso por celular fora de casa | Sim | Tailscale Serve via HTTPS, configuração pelo usuário |
| ChatGPT oficial controlando o PC | Não, nesta beta | Depende de conexão MCP remota e disponibilidade do ChatGPT |
| Desktop remoto gráfico | Não | Esta é uma ferramenta de arquivos/comandos, não captura de tela |

## Início rápido no Linux

Requisitos: **Python 3.10+** e uma pasta de trabalho já existente. Não é preciso instalar dependências Python.

```bash
# Descompacte o ZIP e entre na pasta:
cd remote-commander-beta

# Crie um token secreto (uma única vez):
python3 -m remote_commander init

# Inicie o painel. Substitua pelo caminho real dos projetos:
python3 -m remote_commander serve --workspace "$HOME/projetos"
```

Se a pasta ainda não existir, crie-a (`mkdir -p "$HOME/projetos"`) **antes** de iniciar.

No navegador **do mesmo computador**, abra `http://127.0.0.1:8765`. Para visualizar seu token no terminal local:

```bash
python3 -m remote_commander token
```

Cole o token na página de login. O navegador o guarda **apenas na sessão da aba**, não no armazenamento persistente. Proteja o token como uma senha.

O painel web **só escuta em `127.0.0.1`** e não possui opção para abrir o endereço `0.0.0.0`.

### Habilitar edição e terminal

Pare o processo com `Ctrl+C` e reinicie com as permissões que desejar:

```bash
python3 -m remote_commander serve \
  --workspace "$HOME/projetos" \
  --allow-write \
  --allow-exec
```

**Atenção:** qualquer processo Python, Git ou Make iniciado com `--allow-exec` tem os privilégios da conta Linux. **O workspace restringe as APIs de arquivo, mas não limita o sistema operacional acessível aos programas iniciados.** Não use `--allow-exec` em servidores ou computadores sensíveis; prefira conta Linux dedicada, VM ou container sem privilégios. O terminal tem uma pequena lista de programas permitidos, sem shell, porém isso **não é isolamento de segurança**.

## Acesso remoto no celular com Tailscale

**Não abra a porta 8765 no roteador** e **não use Tailscale Funnel**.

1. Instale o [Tailscale](https://tailscale.com/download) no computador Linux e no celular, e conecte ambos à **mesma tailnet** pessoal.
2. Execute o painel web localmente, como acima.
3. No Linux, publique **apenas para os dispositivos da sua tailnet**, por exemplo:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status
   ```

4. Abra no celular o endereço **HTTPS** mostrado pelo `tailscale serve status`.
5. Autentique com o token gerado no computador.

A sintaxe exata do Tailscale Serve pode variar conforme a versão instalada; consulte a [documentação oficial](https://tailscale.com/docs/features/tailscale-serve). Revise as permissões de acesso da tailnet. A publicação do serviço e a conexão dos dispositivos precisam ser feitas pelo operador local.

**Observação:** o serviço web faz HTTP somente em loopback; o HTTPS do acesso remoto deve ser terminado pelo Tailscale Serve. Não encaminhe esse HTTP diretamente à Internet.

## IA totalmente local com Ollama (opcional)

Para usar o chat de IA no navegador, instale o [Ollama](https://ollama.com/download), mantenha o serviço local ativo e baixe um modelo que ofereça suporte a chamadas de ferramentas (*tool calling*). Por exemplo (a escolha e os requisitos de memória do modelo devem ser conferidos):

```bash
ollama pull qwen3:4b
python3 -m remote_commander serve \
  --workspace "$HOME/projetos" \
  --ollama-model qwen3:4b
```

Agora, no painel **Assistente IA**, você pode pedir: "Liste os arquivos do meu projeto e explique a organização". As ferramentas `list_files`, `read_file` e `git_status` podem ser usadas automaticamente.

Para solicitar que a IA proponha a execução de testes ou edição de arquivos, ligue também `--allow-exec` ou `--allow-write`. **Nenhuma operação desse tipo é executada automaticamente pelo chat web:** o sistema mostra o comando/conteúdo antes de você clicar **Autorizar ação**. A aprovação expira após cinco minutos. Mesmo com aprovação, comandos continuam perigosos; revise tudo cuidadosamente.

A IA roda na própria máquina via `http://127.0.0.1:11434`; não há cobrança por chamada da beta, porém o modelo usa seus recursos de CPU/GPU. A interface não transmite conversas para uma API de IA externa. Usar outro cliente MCP com um provedor de IA comercial pode gerar custos no provedor.

## Usar com cliente MCP compatível (stdio)

O servidor MCP local funciona com clientes que aceitam o transporte **stdio**. Exemplo ilustrativo de configuração (substitua os caminhos absolutos):

```json
{
  "mcpServers": {
    "remote-commander": {
      "command": "/usr/bin/python3",
      "args": [
        "-m", "remote_commander", "mcp",
        "--workspace", "/home/SEU_USUARIO/projetos"
      ],
      "env": {
        "PYTHONPATH": "/CAMINHO/ABSOLUTO/remote-commander-beta"
      }
    }
  }
}
```

O MCP fornece `tools/list`, `tools/call`, `initialize` e `ping`, por JSON-RPC via stdio. Usa o protocolo MCP `2025-11-25` para compatibilidade. **O servidor MCP stdio executa diretamente qualquer ferramenta ativada por flags**, sem passar pela aprovação do painel web. Por isso, prefira MCP **apenas de leitura**. Se acrescentar `--allow-write` ou `--allow-exec`, confie integralmente no cliente e na IA controladora.

Esta versão **não expõe MCP Streamable HTTP** e não se conecta automaticamente ao ChatGPT. O chat por Ollama e a integração local MCP funcionam independentemente do ChatGPT.

## Comandos úteis

```bash
python3 -m remote_commander --help
python3 -m remote_commander serve --help
python3 -m remote_commander mcp --help
python3 -m remote_commander token
python3 -m unittest discover -s tests -v
```

## Arquitetura

```text
remote-commander-beta/
├── remote_commander/
│   ├── core.py            # Workspace, permissões, arquivos, comandos, auditoria
│   ├── server.py          # API HTTP protegida + ações pendentes
│   ├── ollama.py          # IA local, tool calling e aprovação
│   ├── mcp_stdio.py       # MCP JSON-RPC via stdin/stdout
│   ├── __main__.py        # CLI
│   └── static/            # Interface HTML/CSS/JS sem frameworks
├── tests/                 # Testes automatizados
├── examples/              # Exemplos de configuração
├── SECURITY.md            # Modelo de ameaças e limitações
├── CHANGELOG.md
└── pyproject.toml
```

### Limites da beta

- Arquivos de texto UTF-8 de até **512 KiB**; saídas de comando até 48 KiB por fluxo.
- **20 segundos** por execução, **2** processos simultâneos no máximo.
- **300** entradas por listagem e histórico de até 10 mensagens anteriores enviado à IA.
- O agente não monitora processos em segundo plano, não oferece upload binário nem terminal interativo.
- Não há sincronização automática de arquivos, ferramentas de administração de usuários ou autenticação multifator própria.
- A restrição de workspace **não é sandbox para subprocessos**. Existe risco residual de ataques por links simbólicos e mudanças concorrentes no sistema de arquivos; use pastas confiáveis e uma conta isolada.
- Arquivos conhecidos como credenciais e pastas de configuração são bloqueados nas APIs de arquivo, mas **não há detecção universal de segredos**.
- Projetos e computadores institucionais só devem ser conectados com a devida autorização e política de segurança.

## Testes, licença e continuidade

Execute `python3 -m unittest discover -s tests -v` no diretório raiz. O projeto usa somente a biblioteca padrão do Python e tem licença [MIT](LICENSE).

**Roadmap:** testes de integração MCP com clientes oficiais, proteção contra concorrência de arquivos, aprovação com diff, sessões e permissões granulares, upload/download, tarefas com logs paginados, transporte remoto MCP autenticado e suporte a containers de execução.

## Prévia visual

As imagens `screenshots/preview-mobile.png` e `screenshots/preview-desktop.png` demonstram o layout do painel com dados fictícios.

Consulte [TEST_REPORT.md](TEST_REPORT.md) para saber exatamente o que foi testado e quais integrações ainda precisam de validação real.
