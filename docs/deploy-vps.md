# Securo em uma VPS com Caddy

Este guia instala o Securo da branch `deploy` numa VPS já compartilhada com
outros serviços. O Caddy é o único processo que escuta portas públicas; o
Securo fica acessível localmente em `127.0.0.1`.

## Antes de começar

- Escolha um subdomínio, por exemplo `financas.example.com`, e crie um registro
  DNS `A` apontando para o IP público da VPS.
- No firewall, permita apenas SSH (22), HTTP (80) e HTTPS (443). Não exponha
  3000, 8000, 8765, 5432 nem 6379.
- Instale e inicie Docker Compose e Caddy na VPS. O Caddy deve ser compartilhado
  por todos os serviços hospedados nela.

## Instalação

```bash
git clone --branch deploy https://github.com/vhsantos26/securo.git /opt/securo
cd /opt/securo
cp .env.example .env
chmod 600 .env
```

Edite `/opt/securo/.env`. O mínimo seguro é:

```dotenv
SECRET_KEY=<resultado de: openssl rand -hex 32>
FRONTEND_URL=https://financas.example.com
PLUGGY_CLIENT_ID=<seu-client-id>
PLUGGY_CLIENT_SECRET=<seu-client-secret>
PLUGGY_OAUTH_REDIRECT_URI=https://financas.example.com/oauth/callback
```

Para habilitar os agentes e o MCP embutido, acrescente também:

```dotenv
AGENTS_ENABLED=true
COMPOSE_PROFILES=agents
AGENTS_MCP_JWT_SECRET=<resultado de: openssl rand -hex 32>
AGENTS_EXTERNAL_MCP_URL=https://financas.example.com/mcp
```

Suba a stack:

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker compose -f docker-compose.prod.yml ps
```

O arquivo de produção publica frontend, API e MCP apenas em `127.0.0.1` por
padrão. Não defina `*_HOST_BIND=0.0.0.0` numa VPS pública.

## Caddy

Inclua este bloco no `Caddyfile` já usado pela VPS, substituindo o domínio:

```caddyfile
financas.example.com {
    encode zstd gzip

    # API, OAuth, passkeys e demais rotas do backend.
    reverse_proxy /api/* 127.0.0.1:8000

    # MCP é opcional: mantenha esta linha somente se o profile `agents`
    # estiver ativo. A autenticação Bearer é obrigatória pelo Securo.
    reverse_proxy /mcp 127.0.0.1:8765

    # SPA do Securo; este deve ser o fallback final.
    reverse_proxy 127.0.0.1:3000
}
```

Valide e recarregue sem interromper os outros hosts:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

Depois do DNS propagar, valide `https://financas.example.com`. O callback
registrado no dashboard da Pluggy deve ser exatamente
`https://financas.example.com/oauth/callback`.

## Atualizações e backup

O workflow deploy-vps publica automaticamente o commit na VPS depois que o
CI da branch deploy passa. Ele usa uma chave SSH exclusiva e só executa
commits que existam em origin/deploy. A primeira instalação da chave e dos
segredos está documentada no workflow; nunca coloque a chave privada no
repositório ou no .env.

Para uma atualização manual ou uma recuperação pontual:

```bash
cd /opt/securo
git pull --ff-only origin deploy
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

O banco, anexos e a base de conhecimento usam volumes Docker. Faça backup do
Postgres antes de atualizações relevantes e teste a restauração em outro local:

```bash
install -d -m 700 /opt/securo/backups
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U postgres -Fc securo > /opt/securo/backups/securo-$(date +%F).dump
```

O token de acesso ao MCP é emitido na interface do Securo em **Settings → AI
Agents**. Não se configura um token de longa duração no Caddy nem no `.env`.
