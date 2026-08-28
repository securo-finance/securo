# Status operacional — Securo

Atualizado em 28/08/2026. Este arquivo é o checklist de operação da instância
produtiva. Marque um item como concluído somente após registrar a evidência
correspondente.

## Estado atual

| Área | Estado | Evidência / decisão |
| --- | --- | --- |
| Securo na VPS | Concluído | API saudável; frontend, backend, banco, Redis e workers em execução. |
| Exposição pública | Concluído | Caddy é o único serviço nas portas 80/443; Securo atende apenas em `127.0.0.1`. |
| Acesso à aplicação | Concluído | Login e autenticação de dois fatores configurados pelo administrador. |
| Backup gerenciado da VPS | Concluído (semanal) | Backup semanal de aproximadamente 2 GB confirmado em 28/08/2026 na Hostinger. |
| Pluggy | Piloto conectado | Credenciais carregadas; primeira conexão importada e em reconciliação antes de novas contas. |
| MCP e agentes internos | Desligado | Permanecerá desligado até os dados importados e as regras estarem validados. |
| Deploy contínuo | Validado | PRs do fork #29 e #30 mergeados; VPS saudável e rodando a precedência regra antes da categoria do provedor. |

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
- [x] Confirmar que o primeiro backup automático aparece em **VPS → Snapshots e Backups** quando a Hostinger o gerar.
- [x] Registrar o primeiro ponto de restauração: 28/08/2026, backup semanal de aproximadamente 2 GB confirmado pelo administrador.
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

**Decisão registrada:** backup granular externo permanece adiado por decisão
explícita do administrador após confirmar o backup semanal da Hostinger em
28/08/2026. A proteção vigente é recuperação semanal da VPS inteira, não
backup diário/restauração granular; revisar esta decisão se a tolerância à
perda de até uma semana de dados mudar.

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
      O primeiro fix deste fork (PR #24) usava
      `_annotate_institution_names` em `pluggy.py`: detectava códigos de
      banco diferentes e preenchia `Account.display_name` apenas quando vazio.
      Ele foi validado em sync real — `BCO XP S.A.` e
      `XP INVESTIMENTOS CCTVM S/A`.

      A sincronização com a v0.14.5 passa a usar o mecanismo oficial do
      upstream: modelo `Institution` e os campos
      `AccountData.institution_name`/`institution_external_id`/
      `institution_logo_url`, com get-or-create automático. A adaptação para
      a Pluggy está no PR upstream [#724](https://github.com/securo-finance/securo/pull/724)
      (fecha [#723](https://github.com/securo-finance/securo/issues/723)) e
      integra esta atualização do fork. Em cada próximo sync normal, as contas
      XP receberão sua instituição individual; `display_name` já existente não
      é apagado, preservando qualquer rótulo personalizado. Não forçar sync:
      validar junto de uma próxima transação real.

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

### 4.4 Consórcio e financiamento invisíveis no orçamento + regra nunca vencia o provedor (27/08/2026)

Achado: os três compromissos de dívida estruturada da família estavam
espalhados em categorias que escondiam o comprometimento de renda real —
"PORTO CONSORCIO" (consórcio imóvel) e "BANCO BRADESCO S.A." caíam em
"Transferências" (`treat_as_transfer`, fluxo neutro), as 4 cotas mensais do
consórcio de veículo ("BRADESCO ADMINISTRADORA...") caíam em "Transporte"
misturadas com gasto de combustível/app, e o financiamento imobiliário
("OPERACOES CREDITO IMOBILIARIO", Santander) estava em "Moradia" misturado
com contas da casa.

- [x] As categorias corretas já existiam no workspace, criadas antes mas
      subutilizadas: "Patrimônio / Consórcios" (grupo Poupança & Patrimônio)
      e "Dívidas" (grupo Necessidades, estava vazia). Histórico
      recategorizado pelo administrador: financiamento imobiliário →
      Dívidas; consórcio imóvel (Porto) e consórcio veículo (Bradesco
      Administradora) → Patrimônio / Consórcios.
- [x] **Causa raiz de a categorização não se manter nos próximos syncs:**
      em `connection_service.py`, a transação nascia já com a categoria do
      Pluggy (`_match_pluggy_category`) preenchida, e só depois rodavam as
      regras (`apply_rules_to_transaction`). Como `apply_rule_actions` nunca
      sobrescreve uma categoria já preenchida, uma regra de descrição
      específica ("contém PORTO CONSORCIO") nunca vencia a categoria
      genérica do provedor — na prática, regras de categorização eram
      inúteis sempre que o Pluggy já opinava sobre a transação. Corrigido:
      a transação agora nasce com `category_id=None`, as regras rodam
      primeiro e reivindicam a categoria quando alguma bate, e a categoria
      do Pluggy só é aplicada depois, como fallback para o que nenhuma
      regra cobriu. Ajustado nos dois pontos de sync (import inicial e
      incremental, incluindo o merge com placeholder de recorrência).
      Dois testes novos em `test_connection_service.py`
      (`test_sync_rule_category_wins_over_provider_category` para o import
      inicial, `test_incremental_sync_rule_category_wins_over_provider_category`
      para o sync incremental) — confirmado manualmente que os dois falham
      sem o fix (`git stash` do arquivo alterado) e passam com ele. Suíte
      completa verde (246 testes).
- [x] Regras de produção revisadas e ampliadas para manter a classificação:
      "Patrimônio e consórcios" cobre `CONSORC` (Porto) e Bradesco
      Administradora por descrição ou beneficiário; "Pagamentos de fatura"
      cobre Banco Bradesco S.A. também por descrição ou beneficiário. Elas
      passam a prevalecer sobre a categoria genérica do provedor depois do
      deploy deste fix.
- [x] Fix commitado e mergeado no fork: PRs [#29](https://github.com/vhsantos26/securo/pull/29)
      e [#30](https://github.com/vhsantos26/securo/pull/30). Confirmado em
      28/08/2026 que a VPS está saudável e já roda regra antes do fallback do
      provedor. PR upstream aberto: [securo-finance/securo#735](https://github.com/securo-finance/securo/pull/735).
- [ ] Validar com a próxima transação real sincronizada de Porto ou Bradesco;
      não forçar sync nem criar transação artificial para esta confirmação.

### 4.7 Orçamentos e metas familiares configurados (28/08/2026)

- [x] Criados 14 orçamentos recorrentes em BRL, vigentes a partir de
      01/09/2026. Limites deliberadamente mais restritivos para Família & Bebê
      (R$ 2.000), Transporte (R$ 2.000), Alimentação (R$ 2.500) e Compras
      (R$ 2.500); "Patrimônio / Consórcios" (R$ 4.550) funciona como gate
      explícito para qualquer nova cota.
- [x] Meta manual "Reserva de Emergência Familiar": alvo e valor atual de
      R$ 150.000, liquidez D+0/D+1, mantida ativa em 100% para visibilidade.
- [x] Meta manual "Lance do Consórcio Imóvel": alvo R$ 310.700, saldo inicial
      R$ 50.000, sem prazo até confirmação do calendário de lance. O alvo foi
      informado pelo assessor e inclui encargos do consórcio.
- [ ] Revisar os orçamentos em meados e no fechamento de setembro; usar
      override mensal para exceções em vez de alterar o limite recorrente.

### 4.5 Próximo passo planejado: meta e ativo rastreados por categoria

Objetivo do administrador: uma meta que acompanhe "quanto já paguei do
consórcio X" automaticamente, sem atualização manual, somando os
lançamentos já corretamente categorizados na seção 4.4. Ampliado em
27/08/2026: o mesmo mecanismo também deveria valer para um `Asset` — por
exemplo, a Previdência XP crescendo automaticamente pela soma dos
lançamentos "XP PREV CERT" categorizados como Previdência, em vez de exigir
atualização manual ou uma `growth_rule` de percentual fixo.

- [ ] Hoje `Goal` (`models/goal.py`) só rastreia progresso por 4 vias:
      manual, saldo de conta, valor de ativo, patrimônio líquido
      (`tracking_type`) — nenhuma soma lançamentos de uma categoria.
- [ ] Hoje `Asset` (`models/asset.py`) só atualiza por 2 vias: manual ou
      `growth_rule` (percentual fixo por período) — nenhuma soma
      lançamentos de uma categoria.
- [ ] Adicionar um mecanismo novo (ex. `tracking_type`/`valuation_method`
      "category") que soma os lançamentos da categoria vinculada desde uma
      data de início, tanto para `Goal` quanto para `Asset`. Mesmo desenho
      por baixo, dois pontos de uso.
- [ ] **Ressalva de correção:** aporte somado não é o mesmo que valor real.
      Um fundo de previdência rende ou perde por conta própria, então um
      ativo alimentado só por soma de aportes diverge do saldo real da XP
      com o tempo. Tratar o valor resultante como "aporte acumulado", não
      como "saldo real", e prever reconciliação periódica manual contra o
      extrato (ou aguardar a Pluggy sincronizar esse saldo diretamente,
      hoje ela não sincroniza investimentos XP por completo).
- [ ] Ainda não iniciado — planejado para depois que a categorização e as
      regras da seção 4.4 estiverem validadas por pelo menos um ciclo de
      sync real.

### 4.6 Auditoria retroativa das 24 regras ativas (27/08/2026)

Com a precedência corrigida (4.4), rodamos um script somente-leitura dentro
do container do backend (usa `evaluate_conditions` real do rule engine,
não SQL equivalente) comparando as 24 regras ativas contra as 3.161
transações do workspace: para cada regra com `set_category`, lista
transações que baralham mas têm hoje uma categoria diferente da que a
regra atribuiria. Achado: 11 das 24 regras tinham transações represadas
pelo mesmo bug de precedência, não só os 3 casos da seção 4.4.

**Dados corrigidos (revisados um a um com o administrador, não em lote):**
- Investimentos: 10 transações, R$ 102.415,58 (TEDs "APLICAÇÃO FUNDOS" presas em Transferências — maior achado da auditoria)
- Previdência: 12 transações, R$ 3.011,60 ("XP PREV CERT", presas em Investimentos)
- Empresa serviços e licitações: 6 transações, R$ 1.039,00 (assinatura "IG*LICITACOESPU" + 1 PIX enviado)
- Alimentação: 6 transações (mercado/confeitaria/padaria via gateway iFood)
- Assinaturas: 1 transação (IFood Club) + 5 transações "MP*MELIMAIS" (Meli+)
- Pagamento de Fatura: 4 transações de R$250 (fatura de cartão XP, ver contexto abaixo)
- Dívidas: 1 transação, R$ 2.730,21 (parcela do financiamento imobiliário com descrição genérica de boleto Santander, não é fatura)
- Pets: 1 transação, R$ 611,80 ("Matilha Equilibrada")
- Transporte: 2 transações, R$ 168,36 (postos de gasolina)

**Contexto relevante levantado durante a revisão:** a previdência privada é
debitada num cartão de crédito da XP; antes a fatura desse cartão era paga
direto pro Santander, hoje o administrador transfere R$250/mês pra XP e
paga a fatura por lá — por isso os 4 lançamentos "PAGAMENTO DE FATURA" de
R$250 são fatura (categoria correta), não confundir com a parcela do
financiamento imobiliário que às vezes aparece com uma descrição genérica
de boleto Santander parecida.

**Falsos positivos encontrados e regras corrigidas (nenhuma transação
alterada, só a condição da regra):**
- "Impostos e taxas": `contains "IOF"` pegava "MP\*BIOFORMULA" por acidente
  de grafia (bio**IOF**rmula) e `contains "JUROS"` pegava rendimento de
  juros (crédito, não taxa). Corrigido para `regex "\bIOF\b"` (word
  boundary) e exigência de `type = debit` no grupo de condições.
- "Educação": tinha "VALSONET" na lista de condições, mas o administrador
  não soube identificar o que é esse serviço. Condição removida; as 11
  transações relacionadas foram para "Outros" até identificar.
- "Compras": tinha "MERCADOLIVRE\*MERCADOLIVRE" na lista de condições —
  mesmo problema de generalização de marketplace que o iFood (produto real
  varia por trás do gateway, às vezes é item de bebê). Condição removida;
  Mercado Livre passa a ser categorizado manualmente, caso a caso.

**Risco de ordem entre regras de mesma prioridade corrigido:** "Saúde"
(farmácia via iFood) e "Assinaturas recorrentes" (IFood Club, Meli+)
tinham prioridade igual à regra ampla "Alimentação" (`starts_with "IFD*"`)
— com prioridades empatadas o desempate é por UUID da regra, arbitrário.
Baixamos "Saúde" e "Assinaturas recorrentes" para prioridade 9 (na frente
de "Alimentação", que ficou em 10), garantindo que a regra mais específica
sempre vença daqui pra frente, não só por sorte de UUID.

**Pendências em aberto, não resolvidas:**
- [ ] PIX recebido de "Bruno Santana dos Santos" (R$ 65,00, 23/12/2025):
      administrador não tem certeza se é reembolso pessoal ou PIX pessoal
      — precisa checar a saída correspondente antes de categorizar.
- [ ] Revisar identidade do serviço "VALSONET" (ver acima) antes de
      recategorizar as 11 transações que foram para "Outros".
- [ ] Regra "Pagamentos de fatura" ainda casa (mas não sobrescreve, por
      decisão do administrador) a transação de R$ 2.730,21 do
      financiamento — divergência aceita e documentada, não é bug.

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

### 5.1 Plano: agente de controle financeiro no Hermes-Agent (27/08/2026)

Direção definida com o administrador: configurar, no Hermes-Agent
(`hermes-vps`, já rodando na mesma VPS do Securo), um agente dedicado a
controle financeiro, consumindo o MCP server do Securo
(`backend/mcp_server/` — já expõe leitura de contas, transações, orçamento
vs. real, patrimônio, fluxo de caixa, metas, grupos, e propostas de escrita
com confirmação humana). Funções esperadas:

- Execução agendada (semanal/diária) puxando o que aconteceu no período e
  preparando um resumo pro início da semana, com base em recorrência,
  gastos e histórico.
- Consulta ad-hoc do administrador ("o que está acontecendo hoje na minha
  conta").
- Avisos de saúde financeira com base nos orçamentos/buckets definidos nas
  seções 4.4/4.5.

Pré-requisito explícito do administrador: só ativar depois que a
categorização e as regras (seção 4.4) estiverem validadas — consistente
com o estado atual "MCP e agentes internos: Desligado" na tabela do topo.
Escopo de código maior que os itens anteriores; tratar como etapa própria
quando chegar a vez, não em paralelo com a Fase 4.

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
| 🟡 PR upstream aberto | Distinguir banco e corretora dentro de uma mesma conexão Pluggy (XP) | [fork #24](https://github.com/vhsantos26/securo/pull/24) | [#723](https://github.com/securo-finance/securo/issues/723) | [#724](https://github.com/securo-finance/securo/pull/724) |
| 🟡 PR upstream aberto | Regra de categorização vence a categoria genérica do provedor (Pluggy) quando é mais específica | [fork #29](https://github.com/vhsantos26/securo/pull/29), [fork #30](https://github.com/vhsantos26/securo/pull/30) | [#730](https://github.com/securo-finance/securo/issues/730) | [#735](https://github.com/securo-finance/securo/pull/735) |
| ⚫ Backlog upstream, sem trabalho no fork | Mostrar estado de liquidação (paga/parcial) de faturas de cartão Pluggy | — | [#681](https://github.com/securo-finance/securo/issues/681) | — |
| ⚫ Backlog upstream, sem trabalho no fork | Falso positivo no pareamento automático quando um reembolso de terceiro coincide em valor com uma compra | — | [#648](https://github.com/securo-finance/securo/issues/648) | — |
| ⬜ Só nossa operação (N/A upstream) | Infra de deploy na VPS (CI/CD, health check) | [fork #1](https://github.com/vhsantos26/securo/pull/1), [fork #2](https://github.com/vhsantos26/securo/pull/2), [fork #4](https://github.com/vhsantos26/securo/pull/4) | — | — |
| ⬜ Só nossa operação (N/A upstream) | Sync de `deploy` com a v0.14.5 do upstream, preservando os ajustes locais e encadeando as migrations novas como 077/078 após a 076 já aplicada | [fork #5](https://github.com/vhsantos26/securo/pull/5), [fork #32](https://github.com/vhsantos26/securo/pull/32) | — | — |
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
| 2026-08-27 | Categorização de consórcio/financiamento + precedência de regras | Recategorizado histórico de consórcio imóvel, consórcio veículo e financiamento imobiliário; corrigida em `connection_service.py` a precedência regra-vs-categoria-do-provedor (regra específica agora vence). Dois testes novos adicionados (import inicial + sync incremental), ambos confirmados como testes de regressão reais; suíte completa verde (246 testes). Ver seção 4.4. Planos registrados para meta por categoria (4.5) e agente financeiro no Hermes-Agent (5.1). Issue aberta no projeto principal: [#730](https://github.com/securo-finance/securo/issues/730). | Claude |
| 2026-08-27 | Auditoria retroativa das 24 regras ativas | Script somente-leitura comparou as 24 regras contra as 3.161 transações do workspace; achou 11 regras com transações represadas pelo mesmo bug de precedência. Revisado um a um com o administrador: ~R$110 mil recategorizados corretamente (destaque: R$102.415,58 de aportes em fundos presos em "Transferências"), 3 regras com falso positivo corrigidas (Impostos e taxas, Educação, Compras), risco de empate de prioridade entre regras corrigido (Saúde e Assinaturas recorrentes para prioridade 9). 2 pendências abertas. Ver seção 4.6. | Claude |
| 2026-08-28 | Backup, deploy e configuração familiar | Backup semanal da Hostinger de aproximadamente 2 GB confirmado; decisão explícita de manter backup granular diário adiado. VPS saudável após merge dos PRs #29/#30 e confirmada com regra antes do fallback do provedor. Orçamentos recorrentes, reserva de emergência e meta de lance do consórcio configurados. Ver seções 1.1, 4.4 e 4.7. | Codex |
