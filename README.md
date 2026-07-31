# MCP Server - Zendesk

Servidor MCP (Model Context Protocol) em Python que expõe operações do Zendesk como ferramentas utilizáveis pelo Claude.

## Funcionalidades

**Chamados (tickets)**
- `list_tickets` — lista chamados, com filtro opcional por status, prioridade, e-mail do solicitante ou grupo; ordenação (`sort_by`/`sort_order`) e paginação por cursor.
- `get_ticket` — detalhes completos de um chamado por ID.
- `create_ticket` — abre um novo chamado.
- `update_ticket` — altera status, prioridade, atendente ou tags.
- `add_comment` — adiciona um comentário público ou nota interna (visibilidade sempre explícita).
- `get_ticket_comments` — histórico de comentários do chamado, em ordem cronológica (paginado).
- `get_ticket_audits` — histórico de mudanças do chamado (quem alterou o quê e quando).

**Busca**
- `search_tickets` — busca por texto livre ou sintaxe estruturada do Zendesk (`status:open priority:high`), com ordenação e paginação por cursor.

**Usuários e organização**
- `get_user` — busca um usuário por ID ou e-mail.
- `list_organizations` — lista organizações cadastradas.
- `list_groups` — lista grupos de atendimento (usado também para resolver nome de grupo em `list_tickets`).

Todas as respostas trazem só os campos relevantes (não o objeto bruto do Zendesk); as listagens de
chamados já incluem nome do solicitante/atendente/grupo/organização, sem chamadas extras.

## 1. Instalação

Na instância de Cloud, clone o projeto (as dependências são instaladas dentro do container pelo
`Dockerfile`, não é preciso criar um virtualenv na instância):

```bash
git clone <url-do-repositorio> mcp-zendesk
cd mcp-zendesk
```

Copie o arquivo de exemplo de variáveis de ambiente:

```bash
cp .env.example .env
```

---

## 2. Configuração das credenciais do Zendesk

1. Acesse o **Admin Center** do Zendesk (ícone de engrenagem).
2. Vá em **Apps and integrations > APIs > OAuth clients**.
3. Clique em **Add OAuth client**. Marque como **Confidential** — obrigatório para o grant type
   usado aqui (`client_credentials`); clients públicos não são aceitos.
4. Dê um nome descritivo (ex.: `mcp-server`), salve e copie o **Identifier** (`client_id`) e o
   **Secret** (`client_secret`). O secret só é exibido uma vez.
5. Abra o arquivo `.env` e preencha:

```
ZENDESK_SUBDOMAIN=suaempresa
ZENDESK_OAUTH_CLIENT_ID=<Identifier copiado no passo 4>
ZENDESK_OAUTH_CLIENT_SECRET=<Secret copiado no passo 4>
```

> A autenticação junto ao Zendesk usa OAuth 2.0 (grant type `client_credentials`): o `client.py`
> troca `client_id`/`client_secret` por um `access_token` de curta duração em
> `POST https://suaempresa.zendesk.com/oauth/tokens`, renovado automaticamente antes de expirar —
> não há mais token de API estático nem Basic Auth, e não existe refresh token nesse grant type
> (ao expirar, o servidor simplesmente pede um novo). Ações feitas pelo servidor aparecem no
> histórico do Zendesk como tendo sido feitas pelo usuário que **criou** o OAuth client
> (normalmente um admin), diferente do e-mail de agente usado anteriormente.

---

## 3. Geração do token de acesso ao MCP

O servidor exige um token próprio (diferente do token do Zendesk acima), **um por cliente**, para proteger o acesso a ele mesmo. Cada cliente (Claude Desktop, Claude Code, um colega da equipe etc.) recebe seu próprio token, revogável individualmente sem afetar os demais.

Gere um token por cliente com o script incluído no projeto:

```bash
uv run scripts/generate_token.py
```

Isso imprime um token aleatório seguro, por exemplo:

```
Token gerado: 8f3a1c2e9b7d4f6a0c1e5b8d2a7f4c9e
```

(Alternativa sem o script: `openssl rand -hex 32` gera um token igualmente válido.)

Monte o `.env` com um objeto JSON `{"nome-do-cliente": "token"}`, um par por cliente:

```
MCP_SERVER_API_KEYS={"claude-desktop": "8f3a1c2e9b7d4f6a0c1e5b8d2a7f4c9e", "ana-claude-code": "9d2b7f4c1e6a0c9e8f3a1c2e5b8d4f7a"}
```

> Guarde esses tokens em um cofre de senhas ou gerenciador de segredos da equipe. Não podem ser recuperados depois se forem perdidos, apenas substituídos por novos (veja [seção 8](#8-rotação-de-tokens)).

Tentativas de autenticação incorretas são limitadas por IP de origem, com backoff exponencial: depois de `AUTH_RATE_LIMIT_MAX_ATTEMPTS` falhas seguidas (padrão: 5), cada nova tentativa dobra o tempo de bloqueio (`AUTH_RATE_LIMIT_BASE_SECONDS`, padrão: 1s, teto de 5 min) até que um token correto seja apresentado.

---

## 4. Executando o servidor

O servidor roda via Docker Compose: o `docker-compose.yml` builda a imagem a partir do `Dockerfile` e
sobe, junto com o app, um proxy Caddy na frente dele, com TLS automático (Let's Encrypt) — não é preciso
instalar Nginx/Caddy separadamente na instância.

1. Configure o domínio no `Caddyfile` (raiz do projeto), trocando o placeholder pelo domínio público
   real (o mesmo que será usado para registrar o servidor no Claude, [seção 5](#5-configurando-o-claude-para-usar-o-token)):

   ```
   https://mcp-zendesk.suaempresa.com {
       reverse_proxy app:8080
   }
   ```

   Esse domínio precisa já resolver, via DNS, para o IP público desta instância antes de subir os
   containers, senão o Caddy não consegue emitir o certificado.

2. Com o `.env` preenchido (seções 2-3), suba os dois containers:

   ```bash
   docker compose up -d --build
   ```

Os dois serviços já sobem com `restart: unless-stopped`, então voltam sozinhos se a instância reiniciar
ou algum dos containers cair.

Comandos úteis:

```bash
docker compose logs -f          # logs dos dois serviços (app + proxy)
docker compose up -d --build    # reconstrói e reinicia após atualizar o código
docker compose down             # para e remove os containers
```

Os logs do `app` são estruturados em JSON (um objeto por linha, sem tokens ou dados sensíveis). O nível é
`INFO` por padrão; defina `LOG_LEVEL=DEBUG` no `.env` para logs mais verbosos.

### Testando localmente, sem domínio ainda

**Sem TLS (mais simples):**

```
http://localhost {
    reverse_proxy app:8080
}
```

```bash
docker compose up -d --build
claude mcp add --transport http zendesk http://localhost/mcp \
  --header "Authorization: Bearer <seu-token>"
```


Pra remover o registro de teste depois: `claude mcp remove zendesk`. Antes de ir para produção, volte o
`Caddyfile` para o domínio real (passo 1 acima) — na nuvem o registro usa o mesmo `claude mcp add`, só que
com a URL pública HTTPS real.

---

## 5. Configurando o Claude para usar o token

Depois que o servidor estiver no ar e a URL pública definida (ex.: `https://mcp-zendesk.suaempresa.com/mcp`), registre-o no Claude usando o token do cliente específico gerado na [seção 3](#3-geração-do-token-de-acesso-ao-mcp) — cada cliente usa **seu próprio** token, nunca o de outro.

### Claude Desktop ou Claude Code

Edite o arquivo de configuração de MCP servers do seu cliente e adicione:

```json
// Configuração para Windows
"mcpServers": {
    "zendesk": {
      "command": "cmd",
      "args": [
        "/c",
        "npx",
        "-y",
        "mcp-remote",
        "https://mcp-zendesk.suaempresa.com/mcp",
        "--header",
        "Authorization:${AUTH_TOKEN}"
      ],
      "env": {
        "AUTH_TOKEN": "Bearer <TOKEN>"
      }
    }
  }
```

Substitua o valor após `Bearer` pelo token do cliente que está configurando (seção 3). Onde fica esse arquivo:

**Claude Desktop (Windows ou Linux):** o caminho do arquivo varia entre versões do app, então a forma confiável de achá-lo é pela própria interface: **Configurações > Desenvolvedor > Servidores MCP**, e usar a opção de editar a configuração ali (abre o `claude_desktop_config.json` correspondente à sua instalação no editor de texto padrão).

**Claude Code:** `%USERPROFILE%\.claude.json` no Windows, `~/.claude.json` no Linux. Em vez de editar o arquivo à mão, também é possível registrar o servidor via CLI (funciona igual em ambos os sistemas):

```bash
claude mcp add --transport http zendesk https://mcp-zendesk.suaempresa.com/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

Para remover depois (trocar de servidor, ou desfazer o registro):

```bash
claude mcp remove zendesk
```

Depois de editar o arquivo (ou rodar o comando), reinicie o Claude Desktop, ou recarregue/reabra o Claude Code, para que o servidor apareça na lista de MCPs conectados.

### Claude.ai (conector customizado)

1. Vá em **Configurações > Conectores**.
2. Adicione um novo conector remoto, informando a URL pública do servidor.
3. No campo de autenticação, informe o token como Bearer token (mesmo valor usado acima).

---

## 6. Verificando se está funcionando

No próprio Claude, após conectar o MCP, peça algo simples como:

> "Liste os 5 tickets mais recentes no Zendesk"

Se a resposta trouxer tickets reais, a integração está funcionando de ponta a ponta (IP liberado, token aceito, credenciais do Zendesk válidas).

Para testar o servidor isoladamente, sem o Claude, é possível fazer uma chamada direta (a partir de um IP autorizado):

```bash
curl -H "Authorization: Bearer <TOKEN>" https://mcp-zendesk.suaempresa.com/mcp
```

Uma resposta 401 indica token incorreto. Uma conexão recusada ou timeout, vindo de um IP não autorizado, indica que o firewall está bloqueando corretamente.

---

## 7. Cache de leitura e webhook do Zendesk (opcional)

Duas otimizações desabilitadas por padrão se as variáveis correspondentes não estiverem no `.env`.

### Cache de leitura

`CACHE_TTL_SECONDS` (padrão: 30) mantém em memória, por até esse número de segundos, o resultado das
tools de leitura (`list_tickets`, `get_ticket`, `search_tickets`, `get_user`, `list_organizations`,
`list_groups`). Qualquer escrita feita pelo próprio servidor (`create_ticket`, `update_ticket`,
`add_comment`) limpa o cache inteiro na hora, então o risco é só de não enxergar, por até
`CACHE_TTL_SECONDS` segundos, mudanças feitas fora do MCP (direto no Zendesk, ou por outro sistema).
Defina `CACHE_TTL_SECONDS=0` para desativar o cache.

### Webhook do Zendesk

Para reduzir essa janela sem baixar o TTL, o servidor pode expor `POST /webhooks/zendesk`: a cada evento
recebido, ele limpa o cache imediatamente. Esse endpoint não usa o bearer token do MCP (seção 3) — é
autenticado pela assinatura HMAC que o próprio Zendesk envia junto do evento. Ele só existe quando
`ZENDESK_WEBHOOK_SECRET` está definido; com a variável vazia/ausente, o servidor não expõe esse caminho.

Para configurar:

1. No Admin Center do Zendesk, vá em **Apps and integrations > Webhooks** e crie um webhook apontando
   para `https://mcp-zendesk.suaempresa.com/webhooks/zendesk`, método `POST`, com autenticação por
   **assinatura** (signing secret) habilitada.
2. Copie o signing secret exibido e coloque em `ZENDESK_WEBHOOK_SECRET` no `.env`.
3. Crie um trigger (ou automation) para os eventos de ticket desejados (ex.: criado/atualizado) que
   aciona esse webhook.
4. Reinicie o servidor para carregar `ZENDESK_WEBHOOK_SECRET`.

---

## 8. Rotação de tokens

Caso o token de **um cliente específico** precise ser trocado ou revogado (saída de alguém da equipe, suspeita de exposição):

1. Gere um novo token (seção 3).
2. Em `MCP_SERVER_API_KEYS`, substitua (ou remova, para revogar sem substituir) apenas a entrada daquele cliente — os demais tokens continuam válidos.
3. Reinicie o processo do servidor para que a variável seja recarregada.
4. Atualize a configuração no Claude Desktop daquele cliente (seção 5) com o novo token.
5. O token antigo deixa de funcionar imediatamente após o restart do servidor, sem afetar os outros clientes.

Para trocar as credenciais do Zendesk (`ZENDESK_OAUTH_CLIENT_ID`/`ZENDESK_OAUTH_CLIENT_SECRET`):
gere um novo secret para o OAuth client no Admin Center (Apps and integrations > APIs > OAuth
clients), atualize o `.env` e reinicie o servidor. Não existe refresh token nesse grant type — a
cada expiração o servidor simplesmente solicita um novo access token automaticamente com as
mesmas credenciais.

Para trocar o `ZENDESK_WEBHOOK_SECRET` (seção 7): gere um novo webhook (ou regenere o signing secret do
existente) no Admin Center, atualize o `.env` e reinicie o servidor — sem isso, os eventos recebidos
falham na verificação de assinatura e são rejeitados com 401.

---

## 9. Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `401 Unauthorized` ao conectar do Claude | Token errado/ausente, ou entrada removida de `MCP_SERVER_API_KEYS` | Confirme se o token daquele cliente ainda está no `.env` do servidor e no config do Claude |
| Conexão recusada/timeout | IP do cliente não está na allowlist do firewall | Adicione o IP no security group da Cloud |
| Erros vindos do Zendesk (401/403) | `ZENDESK_OAUTH_CLIENT_ID`/`ZENDESK_OAUTH_CLIENT_SECRET` inválidos, ou grant `client_credentials` não habilitado nesse OAuth client | Confirme as credenciais no Admin Center (Apps and integrations > APIs > OAuth clients); gere um novo secret se necessário |
| `404 Not Found` em recursos que deveriam existir | Recurso não encontrado (não é problema de credencial) | Confirme o ID do recurso |
| `429 Too Many Requests` | Limite de rate limiting do Zendesk atingido | Aguarde o retry automático (backoff exponencial) ou reduza o volume de chamadas |
| Container do `app` reinicia em loop | `.env` incompleto/inválido (`load_settings` falha na subida) | Confira `docker compose logs app` pela mensagem de variável faltando e corrija o `.env` |
| `401` em `POST /webhooks/zendesk` | `ZENDESK_WEBHOOK_SECRET` não bate com o signing secret configurado no Zendesk | Confirme o valor no Admin Center (Apps and integrations > Webhooks) e no `.env`; reinicie o servidor |
