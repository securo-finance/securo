# Status operacional — Securo

Atualizado em 27/08/2026. Este arquivo é o checklist de operação da instância
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

### 4.2 Itens levantados em 25/08/2026, ainda em avaliação (sem fix aplicado)

- [x] **Final do cartão só aparece na conta, não na transação — validado.** A
      Pluggy manda `creditCardMetadata.cardNumber` (últimos 4 dígitos) por
      transação, mas `providers/pluggy.py` hoje só promove
      `installmentNumber`/`totalInstallments`/`totalAmount`/`purchaseDate`/
      `billId` para colunas de primeira classe — `cardNumber` fica só dentro
      de `raw_data`. Confirmado contra a base real sincronizada (Santander):
      o campo vem preenchido também em transações não parceladas, não só no
      exemplo da documentação. Ver plano de implementação na seção 4.3.
- [ ] **Por que o Platinum Prime aparece com "Linha de crédito
      compartilhada".** Não é uma heurística de "número de cartão diferente
      = mesma linha". `_consolidated_credit_balance_group()` em
      `providers/pluggy.py` só agrupa duas contas quando o Open Finance do
      banco reporta, em `disaggregatedCreditLimits`, uma linha com
      `consolidationType = CONSOLIDADO` e `creditLineLimitType =
      LIMITE_CREDITO_TOTAL` cujo `usedAmount` bate exatamente com o saldo da
      conta — e cria a chave de agrupamento a partir de moeda + nome da linha
      + limite + valor usado (hash SHA-256, sem espaço para falso positivo
      por coincidência). Isso é exatamente como o Open Finance representa um
      cartão adicional/virtual que puxa do mesmo limite do cartão físico —
      se for esse o caso do Platinum Prime, o agrupamento está correto, não é
      bug. Para confirmar: checar na tela Contas qual é a outra conta
      agrupada com o Platinum Prime e se ela é de fato o cartão virtual.
- [x] **XP mostrando duas "contas correntes" com o mesmo final de cartão —
      investigado, não é duplicata.** Consultando a Pluggy ao vivo
      (`GET /accounts` na conexão XP real): as duas contas têm `subtype:
      CHECKING_ACCOUNT` idêntico e o mesmo `masked_number`, mas o código do
      banco embutido em `bankData.transferNumber` diverge — `348` (Banco XP
      S.A.) numa, `102` (XP Investimentos CCTVM S/A) na outra. São duas
      pessoas jurídicas diferentes do mesmo grupo (o banco e a corretora),
      cada uma reportando sua conta como BANK/CHECKING_ACCOUNT — confirmado
      também pelas transações reais de cada uma (PIX/TED numa, resgates de
      fundos/juros de NTN-B/IOF na outra). Bradesco, Inter e Santander não
      têm esse problema: cada um tem só corrente + poupança, com o mesmo
      código de banco nas duas (o `subtype` já resolve, mecanismo existente).
      Fix implementado: `_annotate_institution_names` em `pluggy.py` detecta
      quando duas contas do mesmo `subtype` numa conexão têm código de banco
      diferente, resolve o nome da instituição via BrasilAPI (pública, sem
      chave, com cache Redis) e popula `Account.display_name` — só quando
      vazio, nunca sobrescrevendo customização do usuário. Issue registrada
      no upstream: securo-finance/securo#723.

### 4.3 Titularidade de cartão: adicional vs. conta conjunta (investigado em 25/08/2026)

Motivação: conta Santander com cartões Visa e Mastercard, cada um podendo ter
cartão adicional de outro CPF (conta conjunta). Objetivo: entender o que o
Securo consegue diferenciar hoje com o dado que o Pluggy entrega.

- [x] Confirmado contra o payload real da API Pluggy (conexão Santander em
      produção): `account.owner`/`account.taxNumber` existem no schema, mas
      para contas de crédito o conector do Santander retorna `taxNumber` e
      `creditData.holderType` vazios — não há como atribuir titular/adicional
      por CPF de forma automática com o dado atual do provedor.
- [x] `creditData.additionalCards` traz os últimos 4 dígitos dos cartões
      adicionais por bandeira quando o conector os reporta. Validado: uma das
      duas bandeiras da conta piloto retornou a lista populada, a outra veio
      vazia mesmo havendo adicional físico segundo o titular — confirmar no
      extrato oficial do banco se é atraso do conector ou adicional inativo
      antes de estranhar o dado.
- [x] Confirmado que `creditCardMetadata.cardNumber`, já presente em
      transações reais sincronizadas (ver 4.2), permite — comparando com
      `account.number` e `creditData.additionalCards` — classificar cada
      transação como titular ou adicional, sem nome/CPF do portador.
- [ ] Migration: nova coluna em `accounts` para os finais de cartão adicional
      (ex. `additional_card_numbers`).
- [ ] Migration: nova coluna em `transactions` para o final do cartão usado
      (ex. `card_last4`), extraído de `creditCardMetadata.cardNumber` no
      parser de transações (`providers/pluggy.py`).
- [ ] Mapear `creditData.additionalCards` em `_build_account_data`
      (`providers/pluggy.py`).
- [ ] Exibir final do cartão + classificação titular/adicional na lista de
      transações e nos detalhes do cartão. Fora de escopo nesta etapa:
      atribuir nome/CPF ao portador do adicional (o provedor não entrega esse
      dado) e tela de rotulagem manual de portador.

**Decisão registrada:** conta conjunta com múltiplos CPFs não é resolvível
automaticamente com o dado atual do Pluggy/Santander — `owner`/`taxNumber` no
payload são sempre do titular principal da conexão; nenhum segundo CPF é
reportado pelo conector nas contas testadas.

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

## Contribuições ao projeto principal (securo-finance/securo)

Todo PR que abrimos no projeto principal deveria ter uma issue correspondente
lá (padrão do projeto). Esta tabela é o rastreamento de quais fixes/features
que fazemos no fork já viraram issue/PR no upstream, e o status de cada um.
Atualizar sempre que um PR upstream mudar de estado ou um novo for aberto.

| Status | O que foi feito | PR no fork | Issue upstream | PR upstream |
| --- | --- | --- | --- | --- |
| ✅ Merged | Preservar subtipo de conta poupança da Pluggy | [fork #3](https://github.com/vhsantos26/securo/pull/3) | [#660](https://github.com/securo-finance/securo/issues/660) (closed) | [#662](https://github.com/securo-finance/securo/pull/662) |
| ✅ Merged | Esclarecer label de "ativos investidos" no dashboard | [fork #9](https://github.com/vhsantos26/securo/pull/9) | [#686](https://github.com/securo-finance/securo/issues/686) (closed) | [#687](https://github.com/securo-finance/securo/pull/687) |
| ✅ Merged | Esclarecer gráfico de evolução de saldo | [fork #10](https://github.com/vhsantos26/securo/pull/10) | [#688](https://github.com/securo-finance/securo/issues/688) (aberta — PR usou `Refs`, não `Closes`; fechar manualmente) | [#689](https://github.com/securo-finance/securo/pull/689) |
| ✅ Merged | Corrigir quebra de linha no label de tipo de regra | [fork #13](https://github.com/vhsantos26/securo/pull/13) | [#692](https://github.com/securo-finance/securo/issues/692) (closed) | [#693](https://github.com/securo-finance/securo/pull/693) |
| 🟡 PR aberto | Deduplicar saldo de crédito compartilhado (cartão adicional) | [fork #6](https://github.com/vhsantos26/securo/pull/6) | [#680](https://github.com/securo-finance/securo/issues/680) | [#682](https://github.com/securo-finance/securo/pull/682) |
| 🟡 PR aberto | Detalhar pendente/liquidado no drill-down de categoria + tooltip compartilhado | [fork #20](https://github.com/vhsantos26/securo/pull/20), [fork #21](https://github.com/vhsantos26/securo/pull/21) | [#715](https://github.com/securo-finance/securo/issues/715) | [#716](https://github.com/securo-finance/securo/pull/716) |
| 🟡 PR aberto | Não deixar categoria de transferência encolher o total da fatura do cartão | *(feito direto neste branch, sem PR próprio no fork ainda)* | [#647](https://github.com/securo-finance/securo/issues/647) | [#649](https://github.com/securo-finance/securo/pull/649) |
| 🟠 Issue aberta, sem PR | Total de fatura por cartão individual (Santander) + faturas de cartão compartilhado na sidebar | [fork #7](https://github.com/vhsantos26/securo/pull/7), [fork #8](https://github.com/vhsantos26/securo/pull/8) | [#684](https://github.com/securo-finance/securo/issues/684) | — |
| 🟠 Issue aberta, sem PR | Redesenho do card principal (saldo disponível vs patrimônio líquido) | [fork #11](https://github.com/vhsantos26/securo/pull/11) | [#691](https://github.com/securo-finance/securo/issues/691) — validar se vale propor upstream; é decisão de design mais opinativa | — |
| ⚪ Sem issue nem PR | Deduplicar saldo de crédito compartilhado no card principal do dashboard | [fork #12](https://github.com/vhsantos26/securo/pull/12) | — *(relacionado à mesma causa raiz de [#680](https://github.com/securo-finance/securo/issues/680))* | — |
| ⚪ Sem issue nem PR | Reconhecer categorias Pluggy "pagamento de fatura" e "self-transfer" | [fork #16](https://github.com/vhsantos26/securo/pull/16) | — *(fecha apenas issue interna do fork [#15](https://github.com/vhsantos26/securo/issues/15))* | — |
| ⚫ Backlog upstream, sem trabalho no fork | Mostrar estado de liquidação (paga/parcial) de faturas de cartão Pluggy | — | [#681](https://github.com/securo-finance/securo/issues/681) | — |
| ⚫ Backlog upstream, sem trabalho no fork | Falso positivo no pareamento automático quando um reembolso de terceiro coincide em valor com uma compra | — | [#648](https://github.com/securo-finance/securo/issues/648) | — |
| ⬜ Só nossa operação (N/A upstream) | Infra de deploy na VPS (CI/CD, health check) | [fork #1](https://github.com/vhsantos26/securo/pull/1), [fork #2](https://github.com/vhsantos26/securo/pull/2), [fork #4](https://github.com/vhsantos26/securo/pull/4) | — | — |
| ⬜ Só nossa operação (N/A upstream) | Sync de `deploy` com a v0.14.4 do upstream | [fork #5](https://github.com/vhsantos26/securo/pull/5) | — | — |
| ⬜ Só nossa operação (N/A upstream) | Resolução de revisão duplicada do Alembic (conflito de numeração só no fork) | [fork #14](https://github.com/vhsantos26/securo/pull/14) | — | — |
| ⬜ Só nossa operação (N/A upstream) | Registro de investigação no STATUS.md (seções 4.2 e 4.3), ainda sem código | [fork #17](https://github.com/vhsantos26/securo/pull/17), [fork #18](https://github.com/vhsantos26/securo/pull/18) | — | — |

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
| 2026-08-25 | Titularidade de cartão | Validado contra payload real da conexão Santander: `holderType`/`taxNumber` vazios em crédito, `additionalCards` e `creditCardMetadata.cardNumber` preenchidos e suficientes para classificar transação por cartão físico, sem CPF/nome do portador. Ver seção 4.3. | Claude |
