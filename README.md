# MCP Server - Zendesk

<p align="center">
  <strong>Zendesk no Claude, com token por cliente e allowlist de IP.</strong>
</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="Licença MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/MCP-server-6E56CF" alt="Servidor MCP">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker ready">
</p>

<p align="center">
  <a href="#veja-em-ação">Veja em ação</a> ·
  <a href="#funcionalidades">Funcionalidades</a> ·
  <a href="#stack">Stack</a> ·
  <a href="#1-instalação">Instalação</a> ·
  <a href="#4-executando-o-servidor">Deploy</a> ·
  <a href="#9-solução-de-problemas">Troubleshooting</a> ·
  <a href="#licença">Licença</a>
</p>

Servidor MCP (Model Context Protocol) em Python que expõe operações do Zendesk como ferramentas
utilizáveis pelo Claude — chamados, busca, usuários/organizações e artigos do Help Center — pensado
para rodar remotamente atrás de um proxy reverso, com token bearer por cliente e allowlist de IP como
camadas extras de defesa.

---

## Veja em ação

Depois de conectado, basta pedir em linguagem natural:

> "Liste os 5 tickets mais recentes no Zendesk"

O Claude chama `list_tickets`, e a resposta já vem com status, prioridade, solicitante, atendente e
grupo resolvidos por nome — sem chamadas extras para descobrir quem é quem. O mesmo vale para buscar
por texto livre ou sintaxe estruturada (`status:open priority:high`), abrir/editar chamados, adicionar
comentários (públicos ou notas internas) e consultar ou publicar artigos do Help Center.

Lista completa de ferramentas: [seção Funcionalidades](#funcionalidades) abaixo. Passo a passo de
verificação ponta a ponta: [seção 6](#6-verificando-se-está-funcionando).

---

## Funcionalidades

### Chamados (tickets)

| Ferramenta | O que faz |
|---|---|
| `list_tickets` | Lista chamados, com filtro opcional por status, prioridade, e-mail do solicitante ou grupo; ordenação (`sort_by`/`sort_order`), paginação por cursor e `limit` de resultados por chamada (padrão 25). |
| `get_ticket` | Detalhes completos de um chamado por ID; descrição longa é truncada (avisa via `description_truncated`). Campos personalizados vêm com o nome do campo, não só o ID numérico. Quando existe, `satisfaction_rating` traz a nota do cliente (`good`/`bad`/`offered`/`unoffered`) e o comentário, se houver. |
| `create_ticket` | Abre um novo chamado. |
| `update_ticket` | Altera status, prioridade, atendente ou tags. |
| `add_comment` | Adiciona um comentário público ou nota interna (visibilidade sempre explícita). |
| `get_ticket_comments` | Histórico de comentários do chamado, paginado e limitado (`limit`, padrão 20); do mais antigo para o mais recente, ou só o fim da conversa com `sort_order="desc"`; corpo de comentário longo é truncado. Comentário com arquivo anexado traz `attachments` (nome, url, tipo, tamanho). |
| `get_ticket_audits` | Histórico de mudanças do chamado (quem alterou o quê e quando), paginado e limitado (`limit`, padrão 50); aceita `field_name` para ver só as mudanças de um campo (ex.: `status`); valores longos são truncados. |

### Busca

| Ferramenta | O que faz |
|---|---|
| `search_tickets` | Busca por texto livre ou sintaxe estruturada do Zendesk (`status:open priority:high`), com ordenação, paginação por cursor e `limit` de resultados (padrão 25). |

### Usuários e organização

| Ferramenta | O que faz |
|---|---|
| `get_user` | Busca um usuário por ID ou e-mail. |
| `list_organizations` | Lista organizações cadastradas, ou busca por nome parcial (`name`) sem paginar a conta inteira. |
| `list_groups` | Lista grupos de atendimento (usado também para resolver nome de grupo em `list_tickets`). |

### Help Center (guias)

| Ferramenta | O que faz |
|---|---|
| `search_guides` | Busca artigos por palavra-chave; devolve só snippet (≈280 caracteres), nunca o corpo, com limite baixo por padrão (5). |
| `get_guide` | Conteúdo completo de um artigo, com HTML convertido para texto legível e truncado em 8.000 caracteres (avisa via `truncated`). Artigo restrito (403) retorna mensagem clara em vez do erro genérico de credencial. |
| `list_guide_categories` | Categorias com as seções já aninhadas dentro, para navegação exploratória. |
| `create_guide` | Cria um artigo; `draft` (rascunho ou já publicado) e `visibility` são sempre explícitos, nunca têm valor padrão. `section`, `permission_group` e `visibility` aceitam nome ou ID numérico (`visibility` também aceita `"everyone"`). |
| `update_guide` | Altera título, corpo ou status de publicação de um artigo existente (via tradução do `locale`, já que o Zendesk não edita título/corpo pelo endpoint de artigo). |
| `list_guide_permissions` | Lista permission groups e user segments disponíveis, para preencher `permission_group` e `visibility` de `create_guide`. |

### Decisões de design

Todas as respostas trazem só os campos relevantes (não o objeto bruto do Zendesk); as listagens de
chamados já incluem nome do solicitante/atendente/grupo/organização, sem chamadas extras. Campos de
texto longos (descrição de chamado, corpo de comentário, valores de auditoria) são truncados com um
aviso em vez de estourar o contexto, e as tools de listagem/busca aceitam `limit` com um valor baixo
por padrão, para favorecer poucos resultados bem filtrados em vez de páginas longas e genéricas.

As buscas também devolvem `total_matches` — quantos resultados existem no Zendesk, não só quantos
vieram nesta página (`count`). É o que permite ao modelo perceber que a consulta foi ampla demais e
refinar o filtro, em vez de paginar às cegas. Onde uma listagem pode parar antes do fim (grupos,
permission groups, taxonomia do Help Center) o corte é sinalizado por `has_more`, nunca silencioso:
uma lista truncada que se parece completa é pior que uma lista menor que avisa. E `get_ticket_comments`
(`sort_order`) e `get_ticket_audits` (`field_name`) permitem responder a uma pergunta pontual sobre um
chamado longo sem ler a conversa ou o histórico inteiros.

As guias aplicam otimizações extras, pensadas para reduzir volume de contexto: `search_guides` nunca
devolve o corpo do artigo, só um snippet curto; campos irrelevantes da API (labels, autor, contador de
votos, timestamps de criação) são descartados; traduções do mesmo artigo e entradas quase idênticas
(mesma seção, título praticamente igual) são deduplicadas, mantendo a mais recentemente editada; a
ordem de relevância da própria busca do Zendesk é preservada, só promovendo casamento exato de título;
e o corpo de `get_guide` é truncado em 8.000 caracteres para não estourar contexto quando vários
artigos são pedidos em sequência.

Nas guias há também uma otimização de latência: traduzir o `section_id` de um artigo em nome legível
custava percorrer todas as páginas de seções **e** de categorias do Help Center (até 40 requisições por
busca). Agora `search_guides` busca apenas as seções dos resultados que sobraram depois do corte por
`limit`, e `get_guide` traz seção e categoria na própria requisição do artigo
(`?include=sections,categories`) — uma requisição em vez de dezenas.

---

## Stack

- **Python 3.11+**, via [`mcp`](https://pypi.org/project/mcp/) (SDK oficial do Model Context Protocol)
- `httpx` para as chamadas ao Zendesk, com retry/backoff e cache de leitura embutidos (`client.py`)
- `starlette` + `uvicorn` como transporte HTTP do servidor MCP
- `pydantic` para validação
- Deploy via **Docker** (`Dockerfile` + `docker-compose.yml`) com **Caddy** na frente, TLS automático (Let's Encrypt)
- Testes com `pytest` + `pytest-asyncio` + `respx` (`tests/`)

Detalhes de arquitetura (fluxo de request, middleware de auth, cache, convenções de teste) estão em
[`CLAUDE.md`](./CLAUDE.md).

---

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

Opcionalmente (desativado por padrão), `CLIENT_RATE_LIMIT_MAX_REQUESTS` limita quantas chamadas *autenticadas* um mesmo `client_id` pode fazer a cada `CLIENT_RATE_LIMIT_WINDOW_SECONDS` (padrão: 60s) — um limite diferente do de cima, que só conta tentativas de token incorreto. Como todos os clientes compartilham as mesmas credenciais OAuth do Zendesk, um único cliente com bug (preso num loop de retry, por exemplo) pode esgotar o rate limit do Zendesk para todo mundo; esse limite existe para conter isso na origem, por cliente, antes que chegue no Zendesk.

---

## 4. Executando o servidor

O servidor roda via Docker Compose: o `docker-compose.yml` builda a imagem a partir do `Dockerfile` e
sobe, junto com o app, um proxy Caddy na frente dele, com TLS automático (Let's Encrypt) — não é preciso
instalar Nginx/Caddy separadamente na instância.

1. Copie o arquivo de exemplo do Caddy e configure o domínio real:

   ```bash
   cp Caddyfile.example Caddyfile
   ```

   Troque o placeholder pelo domínio público real (o mesmo que será usado para registrar o servidor no
   Claude, [seção 5](#5-configurando-o-claude-para-usar-o-token)):

   ```
   https://mcp-zendesk.suaempresa.com {
       reverse_proxy app:8080
   }
   ```

   Esse domínio precisa já resolver, via DNS, para o IP público desta instância antes de subir os
   containers, senão o Caddy não consegue emitir o certificado. `Caddyfile` (sem sufixo) é o arquivo
   que o `docker-compose.yml` monta de fato — `Caddyfile.example` é só o template versionado no repo.

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

`CACHE_TTL_SECONDS` (padrão: 30) mantém em memória, por até esse número de segundos, o resultado de
toda requisição GET — ou seja, todas as tools de leitura (`list_tickets`, `get_ticket`,
`get_ticket_comments`, `get_ticket_audits`, `search_tickets`, `get_user`, `list_organizations`,
`list_groups`, `search_guides`, `get_guide`, `list_guide_categories`, `list_guide_permissions`), além
das consultas de apoio que elas repetem (nome de grupo, nomes dos campos personalizados, seções do
Help Center) — é o cache que faz essas buscas auxiliares custarem, na prática, uma requisição por
janela e não uma por chamada. Qualquer escrita feita pelo
próprio servidor (`create_ticket`, `update_ticket`, `add_comment`, `create_guide`, `update_guide`)
limpa na hora só o cache do tipo de recurso afetado — escrever um chamado não derruba o cache de
artigos do Help Center (nem de grupos, organizações etc.), e vice-versa. O evento de webhook (abaixo),
por não saber qual recurso mudou, ainda limpa o cache inteiro. O risco remanescente é só de não
enxergar, por até `CACHE_TTL_SECONDS` segundos, mudanças no mesmo tipo de recurso feitas fora do MCP
(direto no Zendesk, ou por outro sistema). Defina `CACHE_TTL_SECONDS=0` para desativar o cache.

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
| `429 Too Many Requests` do Zendesk | Limite de rate limiting do Zendesk atingido | O servidor já tenta de novo sozinho (backoff exponencial); se persistir, reduza o volume de chamadas |
| `429 Too Many Requests` vindo do próprio MCP (não do Zendesk) | `CLIENT_RATE_LIMIT_MAX_REQUESTS` atingido por esse `client_id` | Espere o `Retry-After` indicado, ou aumente o limite no `.env` se for esperado desse cliente |
| Erros 502/503/504 do Zendesk | Zendesk temporariamente indisponível | O servidor já tenta de novo sozinho (backoff exponencial); se persistir, é uma instabilidade do lado do Zendesk |
| Container do `app` reinicia em loop | `.env` incompleto/inválido (`load_settings` falha na subida) | Confira `docker compose logs app` pela mensagem de variável faltando e corrija o `.env` |
| `401` em `POST /webhooks/zendesk` | `ZENDESK_WEBHOOK_SECRET` não bate com o signing secret configurado no Zendesk | Confirme o valor no Admin Center (Apps and integrations > Webhooks) e no `.env`; reinicie o servidor |

---

## Licença

[MIT](./LICENSE)
