# Status operacional — Securo

Atualizado em 25/08/2026. Este arquivo é o checklist de operação da instância
produtiva. Marque um item como concluído somente após registrar a evidência
correspondente.

## Estado atual

| Área | Estado | Evidência / decisão |
| --- | --- | --- |
| Securo na VPS | Concluído | API saudável; frontend, backend, banco, Redis e workers em execução. |
| Exposição pública | Concluído | Caddy é o único serviço nas portas 80/443; Securo atende apenas em `127.0.0.1`. |
| Acesso à aplicação | Concluído | Login e autenticação de dois fatores configurados pelo administrador. |
| Backup gerenciado da VPS | Configurado | Cronograma semanal gratuito selecionado na Hostinger; o primeiro ponto de restauração será gerado automaticamente. |
| Pluggy | Piloto conectado | Credenciais carregadas; primeira conexão importada e em reconciliação antes de novas contas. |
| MCP e agentes internos | Desligado | Permanecerá desligado até os dados importados e as regras estarem validados. |
| Deploy contínuo | Em correção | O workflow está habilitado, mas será ajustado para construir o commit do fork na VPS em vez de usar a imagem upstream. |

## Regras de segurança

- Nunca registrar `PLUGGY_CLIENT_SECRET`, tokens MCP, senhas, dumps ou conteúdo
  de transações neste repositório, neste arquivo ou em logs de CI.
- Credenciais ficam somente no `.env` de produção, com permissão restrita.
- Uma alteração de infraestrutura deve ter snapshot manual antes de ser feita.
- A primeira conexão bancária é piloto: uma instituição e uma conta por vez.
- Agentes começam em modo somente leitura. Qualquer ferramenta que crie, edite
  ou remova dados exige aprovação explícita antes de ser liberada.

## Fase 1 — recuperação e backup

### 1.1 Backup semanal da Hostinger

- [x] Selecionar o cronograma semanal gratuito no hPanel.
- [ ] Confirmar que o primeiro backup automático aparece em **VPS → Snapshots e Backups** quando a Hostinger o gerar.
- [ ] Registrar a data/hora do primeiro ponto de restauração neste arquivo.
- [ ] Fazer um snapshot manual imediatamente antes da primeira mudança de
      infraestrutura ou migração relevante.
- [ ] Documentar um procedimento de recuperação: restaurar a VPS substitui todo
      o seu conteúdo e deve ser usado somente em incidente ou ambiente de teste.

### 1.2 Backup granular dos dados do Securo — obrigatório antes de dados reais

O backup semanal da Hostinger recupera a VPS inteira, mas não permite recuperar
somente o banco, não possui retenção diária e não substitui uma cópia fora do
provedor. Para fechar esta fase, implementar:

- [ ] `pg_dump` diário consistente do PostgreSQL, executado a partir do
      container do banco e compactado.
- [ ] Criptografia do arquivo antes do envio.
- [ ] Cópia automática para armazenamento externo independente da VPS e,
      idealmente, independente da Hostinger.
- [ ] Retenção: 7 diários, 4 semanais e 6 mensais.
- [ ] Alerta em caso de falha e verificação periódica de que o arquivo remoto
      pode ser lido.
- [ ] Teste de restauração em local isolado, sem sobrescrever a produção.

**Decisão registrada:** backup granular externo foi adiado. O backup semanal da
Hostinger é a proteção vigente da VPS; esta decisão deve ser revisada antes de
conectar todas as instituições ou de guardar um histórico financeiro crítico.

## Fase 2 — pré-configuração Pluggy

O Securo já inclui a integração Pluggy. As credenciais são globais da instância
e devem permanecer apenas no backend. O fluxo de conexão é feito no navegador;
nunca compartilhar login bancário ou códigos de autenticação por mensagem.

- [x] Criar/confirmar a conta no Meu Pluggy e a aplicação de desenvolvimento no
      dashboard Pluggy.
- [x] Associar a aplicação ao Meu Pluggy quando a plataforma solicitar.
- [x] Obter `PLUGGY_CLIENT_ID` e `PLUGGY_CLIENT_SECRET` pelo painel da Pluggy.
- [x] Inserir os dois valores exclusivamente no `.env` de produção.
- [x] Recriar backend, worker e scheduler e confirmar que receberam as
      credenciais, sem revelar os segredos.
- [ ] Registrar o método de sincronização usado pela versão instalada
      (a implementação atual trabalha por polling; webhook só será adicionado
      se o código em produção o exigir).

### Critérios antes da primeira conexão

- [x] Uma conta piloto escolhida; não conectar todas as instituições de uma vez.
- [ ] Definir uma janela de dados inicial e um critério para tratar duplicidades.
- [ ] Confirmar que a conta piloto não será usada para automações de pagamento.

## Fase 3 — importação piloto e qualidade dos dados

- [x] Conectar a conta piloto pelo widget Pluggy no Securo.
- [x] Registrar quantidade de contas e transações importadas, sem dados
      sensíveis, apenas como métrica de validação.
- [x] Conferir saldo, moeda, datas, transações pendentes e cartões.
- [ ] Identificar e tratar duplicidades antes de ligar sincronização contínua.
- [ ] Confirmar que uma nova sincronização não recria lançamentos existentes.
- [ ] Só então repetir para as próximas instituições, uma por vez.

## Fase 4 — regras financeiras

O projeto de referência é `/Users/hugo/Developer/organizze-report-v1`.
Antes de alterar dados no Securo, produzir uma tabela de mapeamento entre as
regras de negócio do Organizze e as categorias, tags, regras e automações do
Securo.

- [ ] Revisar as regras, categorias e exceções existentes no Organizze.
- [ ] Criar o mapeamento proposto e revisar com o administrador.
- [ ] Criar regras em pequenos lotes, com exemplos de transações esperadas.
- [ ] Validar resultados manualmente antes de aplicar o próximo lote.
- [ ] Documentar regras que não puderem ser representadas nativamente, em vez
      de tentar automatizá-las com agentes.

### 4.1 Achado: Entradas/Saídas do mês incluíam pagamento de fatura e autotransferência

Diagnóstico (25/08/2026): o dashboard soma créditos/débitos de todas as contas
do workspace, inclusive cartão de crédito, sem excluir por padrão pagamento de
fatura nem transferência para si mesmo. A exclusão só acontece quando a
transação está pareada (`transfer_pair_id`) ou sua categoria tem
`treat_as_transfer = true` — hoje só "Transferências" e "Investimentos" têm
essa flag por padrão. A categoria "Pagamento de Fatura" do Santander não tinha
a flag, então o débito da fatura na conta corrente contava como Saída do mês.

- [x] Confirmado no código (`_query_filters.counts_as_pnl`, `PLUGGY_CATEGORY_MAP`
      em `connection_service.py`) e verificado contra a documentação oficial da
      Pluggy: os nomes reais de categoria são `"Credit card payment"` e
      `"Same person transfer - PIX/TED/Cash"`, nenhum dos dois estava mapeado.
- [x] PR pequeno aberto: mapear as duas categorias para "Transferências" +
      migration de backfill para transações já sincronizadas sem categoria
      (nunca sobrescreve categorização manual existente).
- [ ] Ação manual pendente do administrador: marcar "Tratar como transferência"
      na categoria "Pagamento de Fatura" já existente (Categorias → editar),
      para corrigir retroativamente os meses já importados que usam essa
      categoria específica. Isso é imediato (filtro é calculado em tempo de
      consulta, sem precisar ressincronizar).
- [ ] Pendência relacionada, ainda não resolvida: sincronização de
      investimentos XP incompleta (nem todo o saldo aparece) e o fluxo de
      "PIX para si mesmo → depois para a XP" precisa de revisão de regras
      separada (tratar como transferência as duas pernas, sem duplicar como
      receita/despesa).

## Fase 5 — MCP e agentes internos

- [ ] Ativar o perfil de agentes somente depois da Fase 3.
- [ ] Inventariar as ferramentas MCP e liberar inicialmente apenas consultas.
- [ ] Criar token MCP individual, com expiração; não reutilizar token pessoal em
      automações.
- [ ] Manter o endpoint MCP atrás do proxy e nunca expor portas internas do
      Docker diretamente.
- [ ] Testar revogação/rotação do token.
- [ ] Exigir aprovação humana para ferramentas de escrita, exclusão, importação
      ou ações financeiras.

## Fase 6 — operação contínua

- [ ] Validar o fluxo de deploy que constrói o commit do fork na VPS.
- [ ] Registrar o tempo e o resultado do primeiro deploy com build local.
- [ ] Conferir logs e health check após cada deploy.
- [ ] Revisar acessos SSH, chaves, tokens e 2FA a cada trimestre.
- [ ] Revisar retenção e testar restauração de backup a cada trimestre.

## Registro de evidências

| Data | Item | Evidência | Responsável |
| --- | --- | --- | --- |
| 2026-08-21 | Saúde da aplicação | `GET /api/health` respondeu `healthy`; seis containers do Securo ativos. | Codex |
| 2026-08-21 | Isolamento de rede | Portas 3000 e 8000 vinculadas a `127.0.0.1`; Caddy atende 80/443. | Codex |
| 2026-08-21 | Backup Hostinger | Cronograma semanal confirmado no hPanel; ainda sem backup gerado. | Administrador |
| 2026-08-21 | Workflow de deploy | PR #2 mergeado em `deploy`: corrige SHA de disparo manual e torna o health check tolerante às migrações iniciais. | Codex |
| 2026-08-22 | Diagnóstico de deploy | O workflow verde usava imagens `latest` do upstream; a correção para construir o commit do fork na VPS está em preparação. | Codex |
| 2026-08-21 | Pluggy | Credenciais configuradas e carregadas em backend, worker e scheduler; `GET /api/health` saudável. | Codex |
| 2026-08-21 | Reconciliação piloto | Faturas do cartão conferidas contra o Organizze; valores compatíveis. Identificado ajuste de subtipo de poupança no provider Pluggy. | Codex |
| 2026-08-25 | Diagnóstico Fase 4 | Entradas/Saídas do mês incluíam pagamento de fatura de cartão sem exclusão automática (categoria sem `treat_as_transfer`). Ver seção 4.1. | Claude |
