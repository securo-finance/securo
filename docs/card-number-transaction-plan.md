# Spec de produto — final do cartão nas transações

**Status:** primeiro recorte implementado; aguarda merge e deploy.
**Objetivo:** permitir distinguir transações feitas com cartões físicos ou
virtuais diferentes que pertencem à mesma conta de cartão, usando apenas o
final do cartão informado pela Pluggy.

## 1. O problema exato

Hoje a aplicação já guarda `Account.masked_number`, isto é, o final informado
para a **conta** de cartão. A transação, porém, não guarda o
`creditCardMetadata.cardNumber` que vem da Pluggy. Por isso uma fatura
compartilhada aparece como uma única conta, sem indicar qual cartão fez cada
compra.

O final da conta não pode ser usado para inferir titularidade. Na amostra real
da VPS, duas contas tinham um número da conta que quase não aparecia nas suas
transações. Logo, a regra "final diferente = cartão adicional" classificaria
incorretamente quase tudo como adicional.

O produto deve responder somente:

> "Qual final de cartão foi usado nesta transação, e qual apelido a família
> escolheu para ele?"

Ele não deve responder automaticamente "quem é o titular".

## 2. Evidência que justifica o trabalho

O teste feito com as cinco contas de crédito reais encontrou
`creditCardMetadata.cardNumber` em 1.975 de 2.007 transações (98,4%). Por
conta, os finais se repetem em grupos pequenos e estáveis — de um a quatro
finais —, o que é útil para cartões adicionais e virtuais recorrentes.

Cobertura não é garantia: conectores podem omitir o campo e o formato pode
variar. Ausência do dado é um estado normal da interface, não um erro.

## 3. Escopo

### Incluído

- Capturar o campo no provider Pluggy e reduzir imediatamente ao final de
  quatro caracteres alfanuméricos.
- Persistir o final mascarado em cada transação, inclusive quando ela mudar de
  `pending` para `posted` durante o sync.
- Exibir o final em listas, detalhe e drill-down de transações de crédito.
- Agrupar e filtrar transações por final dentro da mesma conta.
- Permitir que a família atribua um **apelido local e opcional** ao par
  `conta + final`: por exemplo, "Cartão da Maria" ou "Virtual assinaturas".
- Backfill idempotente do histórico que já possui `raw_data` com o campo.

### Explicitamente fora de escopo

- Inferir titular, adicional, CPF ou nome do portador.
- Armazenar número completo do cartão, mesmo temporariamente no banco.
- Criar orçamento, rateio por pessoa, regra de categoria ou alerta com base no
  final do cartão.
- Alterar saldo, ciclo, fatura, categoria ou duplicação de cartões.
- Mudança automática de apelido a partir de dados do banco.

## 4. Fluxo de dados proposto

```text
Pluggy transaction.creditCardMetadata.cardNumber
                 ↓
mask_last4()  →  "1234" ou null
                 ↓
Transaction.card_masked_number
                 ↓
API TransactionRead → interface de transações
                 ↓
CardIdentifier (apelido opcional por conta + final)
```

O utilitário `mask_last4()` já usado para `Account.masked_number` remove
separadores e preserva apenas os últimos quatro caracteres. Ele deve ser
reutilizado; o código não deve criar uma segunda lógica de mascaramento.

O valor bruto recebido da Pluggy continuará eventualmente no `raw_data` tal
como outros campos do payload de provider, mas o novo campo de domínio e a API
devem expor somente os quatro caracteres. Logs, testes e interfaces nunca
devem mostrar o número completo.

## 5. Modelo de dados

### 5.1 Transação

Adicionar a coluna opcional abaixo em `transactions`:

| Campo | Tipo sugerido | Regra |
| --- | --- | --- |
| `card_masked_number` | `String(4)`, nullable, indexado junto da conta | Apenas final normalizado; nulo quando não informado ou inválido. |

Ela fica na transação, não na conta. A conta pode representar vários cartões.

### 5.2 Identificador/apelido local

Criar uma tabela pequena, por exemplo `card_identifiers`:

| Campo | Regra |
| --- | --- |
| `id`, `workspace_id`, `account_id` | Escopo normal do Securo; conta deve pertencer ao workspace. |
| `masked_number` | Quatro caracteres normalizados. |
| `display_name` | Apelido escrito pelo usuário; obrigatório após criar o identificador. |
| `color` | Opcional e decorativa; não pode ser a única forma de diferenciar cartões. |
| `is_archived` | Permite esconder cartão desativado sem reclassificar o histórico. |
| unicidade | `workspace_id + account_id + masked_number`. |

`CardIdentifier` é um dicionário de apresentação. Excluir/renomear o apelido
não altera `Transaction.card_masked_number`, e dois cartões de contas
diferentes podem ter o mesmo final sem conflito.

## 6. Comportamentos de sincronização

O ponto técnico crítico não é apenas a primeira importação. O sync já possui
caminhos distintos para novo lançamento, atualização por `external_id`, fusão
`pending → posted` e reconciliação com lançamento manual. Todos devem carregar
o mesmo campo quando a transação de entrada o fornecer.

| Situação | Comportamento esperado |
| --- | --- |
| Novo lançamento Pluggy com `cardNumber` | Normalizar e gravar o final. |
| Novo lançamento sem `cardNumber` | Gravar `null`; não inventar o final da conta. |
| Re-sync da mesma transação com final | Atualizar o campo se a Pluggy passar a informar/corrigir o dado. |
| Pendente que vira postado | Preservar/atualizar o final na mesma transação consolidada. |
| Fusão com lançamento manual | Copiar o final informado pela transação sincronizada. |
| Provider não-Pluggy ou lançamento manual | Manter `null`; não mostrar campo de edição nesta fase. |
| Backfill | Preencher apenas `null`, de forma idempotente, a partir de `raw_data`. |

## 7. Experiência de uso

### 7.1 Lista e detalhe de transações

Exibir um badge discreto ao lado dos badges de parcela/pendência, apenas em
contas de crédito com final disponível:

```text
Spotify Premium    •1234  [Cartão da Maria]     R$ 21,90
```

- Sem apelido: `•1234`.
- Com apelido: `•1234  Cartão da Maria`.
- Sem dado: nenhum badge — não usar "desconhecido" em cada linha.
- O badge tem texto acessível; cor, se existir, é complementar.

No detalhe da transação, o campo aparece como somente leitura:

```text
Cartão utilizado
•1234 · Cartão da Maria                 [Gerenciar apelido]
```

O botão abre o gerenciamento do identificador, não permite digitar ou
substituir o número associado à transação.

### 7.2 Filtros e agrupamento

Na página de transações, quando a conta selecionada for cartão de crédito e
houver pelo menos dois finais no resultado, oferecer o filtro "Cartão":

```text
Todos os cartões | •1234 — Cartão da Maria | •8172 — Virtual assinaturas
```

No drill-down de fatura/categoria, o agrupamento por cartão fica atrás de uma
ação explícita "Agrupar por cartão". Não habilitar por padrão para não deixar
a lista cotidiana mais densa.

### 7.3 Gerenciamento de apelidos

O melhor ponto inicial é a página de detalhe da conta de crédito, abaixo das
informações da conta. A tela é derivada das transações existentes:

| Final | Compras no período escolhido | Apelido | Ação |
| --- | --- | --- | --- |
| •2338 | 16 | — | Identificar |
| •8172 | 16 | Cartão principal | Editar |

Criar o apelido exige apenas nome; a cor é opcional. A interface deve avisar:
"Este apelido é local ao Securo e não confirma a titularidade informada pelo
banco."

## 8. Protótipo e estados importantes

O protótipo focado está em
[`card-number-transaction.html`](prototypes/card-number-transaction.html).
Ele usa exemplos fictícios e mostra lista, filtro e gerenciamento de apelidos.
Não chama API nem altera dados.

Estados que precisam ser desenhados/testados:

1. Conta com um único final: mostrar badge, mas não sugerir agrupamento.
2. Conta com vários finais sem apelido: convidar a identificar somente no
   detalhe/gerenciamento, sem pop-up a cada transação.
3. Cartão com apelido: exibir final e nome juntos.
4. Transação sem `cardNumber`: não exibir espaço vazio nem erro.
5. Mesmo final em outra conta: manter apelidos independentes.
6. Identificador arquivado: preservar histórico e esconder dos atalhos de
   filtro por padrão.

## 9. Plano de entrega

| Etapa | Alteração | Validação |
| --- | --- | --- |
| 1. Contrato e migration | Campo em `Transaction`, `TransactionData`, schemas/API e migration. | Migration sobe/desce; API nunca retorna número completo. |
| 2. Sync Pluggy | Extração via `mask_last4()` nos caminhos de criação e atualização. | Fixtures com cartão nulo, formatado e múltiplos finais; import e incremental. |
| 3. Backfill seguro | Job/script único, somente `null`, relatório agregado por conta/final. | Duas execuções não mudam o resultado; logs não vazam PAN. |
| 4. Leitura na UI | Badge na lista/mobile/detalhe e filtro por final. | Responsivo, acessível e sem regressão em contas não-cartão. |
| 5. Apelidos locais | API/tabela `CardIdentifier` e gerenciamento no detalhe da conta. | Unicidade por conta; renomear/arquivar não muda histórico. |
| 6. Agregação opcional | Agrupar drill-down/fatura por identificador. | Totais por cartão somam exatamente o total original da fatura. |

O backfill deve acontecer após as etapas 1 e 2, mas antes de validar a UI com
dados reais. Assim, os cartões já existentes aparecem desde o primeiro uso.

## 10. Casos de teste mínimos

- `"**** **** **** 1234"`, `"1234"`, `"12-34"` e `null` no provider.
- Número com menos de quatro caracteres retorna `null`.
- Duas transações da mesma conta, finais diferentes, são filtradas corretamente.
- Duas contas diferentes com final igual não compartilham apelido.
- Re-sync atualiza final nulo sem alterar categoria, valor, fatura ou regra.
- Pendente/postado não cria duas transações nem perde o final.
- Usuário de outro workspace não lê nem cria identificadores da conta.
- `TransactionRead` e exportação expõem somente o final mascarado.
- A soma das transações de todos os grupos por cartão coincide com a fatura
  antes e depois de atribuir apelidos.

## 11. Critérios de aceite

Considerar a mudança pronta quando:

1. Uma nova transação Pluggy de cartão apresenta `•NNNN` quando o campo vier.
2. O histórico disponível após backfill mostra os finais sem guardar/exibir PAN.
3. Um usuário pode apelidar um final uma vez e reencontrar esse apelido em
   todas as transações da mesma conta.
4. A ausência do campo não degrada o sync nem a interface.
5. Nenhuma tela afirma que um cartão é adicional/titular sem ação humana.
6. Totais de fatura, saldo e categorização permanecem idênticos: isto é uma
   melhoria de identificação, não uma mudança financeira.

## 12. Decisões a confirmar antes de codar

1. **Apelido por cartão:** quer apenas exibir `•NNNN` primeiro, ou já incluir
   a criação de apelidos nesta mesma entrega? Minha recomendação é incluir:
   sem apelido o dado resolve pouco o caso de cartão compartilhado.
2. **Cores:** usar cor opcional nos apelidos ou somente texto/ícone na primeira
   versão? Minha recomendação é texto primeiro; a paleta pode entrar depois.
3. **Backfill:** preencher todo o histórico disponível desde já ou somente
   novos syncs? Minha recomendação é backfill idempotente, pois vocês já têm
   alta cobertura comprovada nos dados históricos.

## 13. Entrega no fork e no upstream

### Fork / instância da família

1. O PR contra `deploy` contém a migration `079`, captura no sync, badge da UI
   e o script de backfill.
2. O deploy aplica primeiro a migration, que apenas cria a coluna e o índice.
   Ela não modifica nenhum lançamento histórico.
3. Depois do deploy, executar novamente o dry-run no workspace familiar e
   comparar com a prévia aprovada.
4. Com a prévia confirmada, executar uma única vez
   `scripts/backfill_transaction_card_numbers.py --apply --workspace-id …`.
   A operação atualiza somente linhas com o campo ainda nulo.
5. Executar o dry-run de verificação; `would backfill` deve ser zero. Novos
   syncs passam a preencher o campo normalmente, sem precisar de agendamento.

### Projeto principal (upstream)

O PR upstream deve incluir exatamente o código de produto, a migration e o
script/documentação de operação. Ele **não** deve executar o backfill em uma
migration, Celery beat ou workflow de deploy. Cada administrador decide se e
quando quer preencher o histórico da sua própria instalação.

Motivos:

- é uma escrita única em dados financeiros históricos;
- instalações podem ter bancos grandes, provedores diferentes ou payloads
  antigos sem `cardNumber`;
- novos syncs já populam a coluna sem qualquer job adicional;
- o script é idempotente e tem dry-run como padrão, logo a decisão fica
  auditável e reversível por backup.
