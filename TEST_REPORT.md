# Relatório de validação — Remote Commander beta 0.1.0

**Data:** 18 de novembro de 2026  
**Plataforma de teste:** Linux, Python 3.13 no ambiente de desenvolvimento.  
**Status:** aprovada como beta experimental; não auditada para produção.

## Testes automatizados

- 22 testes `unittest` concluídos com sucesso.
- Cobertura funcional dos fluxos de token, autenticação, API, navegação/edição, restrição a workspace, bloqueio de symlinks, caminhos relativos, comandos opt-in e limite de saída.
- Testes de protocolo JSON-RPC `initialize`, `tools/list`, `tools/call`, erros MCP e aprovação de ações.
- Teste do assistente Ollama realizado com resposta de modelo **simulada**, sem baixar ou executar modelo real.
- Verificação de compilação Python com `compileall`.

## Testes do painel

- Layout móvel de 390 pixels e desktop de 1260 pixels renderizados em Chromium headless, com respostas de API **simuladas**; sem erros JavaScript no cenário testado.
- Capturas de tela demonstrativas incluídas em `screenshots/` (os dados exibidos são fictícios).
- Inicialização HTTP local confirmada com requisição GET na página principal.

## Não validado nesta etapa

- Instalação e acesso via Tailscale em equipamentos reais.
- Modelo Ollama executando localmente em uma máquina do usuário.
- Pareamento com clientes MCP de terceiros e compatibilidade com ChatGPT.
- Testes de intrusão, análise estática formal, robustez contra alteração concorrente de caminhos e isolamento de processos.
- Funcionamento em todos os sistemas Linux e versões de Git/Python.

**Interpretação:** apta para experimentação local em projeto isolado; não destinada à exposição pública ou ambientes institucionais sem análise e autorização.
