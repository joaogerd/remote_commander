# Segurança — leia antes de executar

Esta versão é **beta**, para testes em **computador pessoal** ou VM. Não foi auditada por terceiros.

## Modelo de segurança

1. **Rede**: o servidor HTTP sempre escuta `127.0.0.1`. Não altere isso nem exponha via port forwarding. Para celular, use Tailscale Serve restrito à sua tailnet, com HTTPS.
2. **Autenticação**: token aleatório de 256 bits em `~/.config/remote-commander/token`, modo `0600`. É exigido no cabeçalho `Authorization: Bearer ...` das APIs. A UI usa `sessionStorage` por aba. Nunca envie o token por chat/e-mail.
3. **Arquivos**: os endpoints de leitura/edição aceitam apenas caminhos relativos dentro do workspace, bloqueiam nomes comuns de credenciais e links simbólicos. Não há isolamento do kernel nem garantia absoluta contra *time-of-check/time-of-use* (TOCTOU) se outros processos controlam os caminhos.
4. **Execução**: desligada por padrão. Habilitar `--allow-exec` equivale a permitir programas com privilégios do usuário Linux, mesmo com allowlist de executáveis e sem shell. Python/Git/Make podem contornar a pasta de trabalho. **Execute numa conta dedicada, VM ou container sem privilégios.**
5. **IA web**: leituras automáticas; escrita e execução exigem aprovação manual de cada ação. Trate propostas do modelo como não confiáveis. Instruções maliciosas presentes em arquivos podem influenciar a IA (prompt injection), e a revisão humana é essencial.
6. **MCP stdio**: clientes MCP já habilitados podem usar ferramentas sem aprovação na UI, portanto não habilite escrita/execução em clientes não confiáveis.
7. **Auditoria**: eventos mínimos em `audit.jsonl` (instante UTC, operação, alvo reduzido e resultado). Os comandos completos e conteúdos não são salvos; isso protege a privacidade, mas limita a investigação forense.

## Não faça

- Não rode como `root`.
- Não use `tailscale funnel`, `ngrok` público nem abra portas no roteador.
- Não aponte o workspace para `~`, `/`, `/etc`, `/p` inteiro ou pastas de dados sensíveis.
- Não execute o agente em computador institucional sem autorização.
- Não instale modelos ou dependências de procedência duvidosa.

## Plano de resposta a incidente

Encerre o processo do agente, desligue Tailscale Serve (`tailscale serve reset`, verificando documentação e serviços existentes), gere novo token com uma rotação manual segura, revise `audit.jsonl`, o histórico do shell e os arquivos alterados. Se suspeitar de comprometimento, isole a máquina e trate credenciais acessíveis ao processo como potencialmente expostas.
