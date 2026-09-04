# Indian Account Aggregator integration

## Purpose

This document records an initial investigation into adding Indian Account Aggregator
(AA) connectivity to Securo. It is a provider-selection and integration-planning
note, not a claim that Securo is an RBI-regulated entity or that any provider has
approved Securo access.

India's AA model is consent-driven. Securo should integrate with an authorised
provider that can serve an FIU, rather than connecting directly to bank websites or
storing bank credentials.

## Candidate providers

| Provider | Evidence found | What still needs verification |
| --- | --- | --- |
| OneMoney | The official site describes OneMoney as an RBI-licensed Account Aggregator and lists bank statements, deposits, mutual funds, insurance, equities, GSTN, and NPS data. It describes OTP login, consent review, account selection, and confirmation flows. | FIU onboarding requirements, API reference, sandbox access, pricing, webhook contract, coverage for the banks Securo users need, and production data-retention terms. |
| Setu | Candidate Indian AA infrastructure provider identified for investigation. | Official AA/FIU product page, current licence or ecosystem role, API documentation, sandbox, pricing, and coverage. |
| Finvu | Candidate Indian AA provider identified for investigation. | Official FIU integration documentation, current licence or ecosystem role, API documentation, sandbox, pricing, and coverage. |
| Anumati | Candidate Indian AA provider identified for investigation. | Official FIU integration documentation, current licence or ecosystem role, API documentation, sandbox, pricing, and coverage. |
| CAMS FinServ | Candidate Indian financial-data and AA provider identified for investigation. | Official FIU integration documentation, current licence or ecosystem role, API documentation, sandbox, pricing, and coverage. |

The shortlist must be validated against current official provider documentation and
Sahamati/RBI information before implementation. Directory listings alone are not
sufficient evidence of Account Aggregator compatibility.

## Recommended first contact

OneMoney is the strongest first candidate from this initial pass because its official
website explicitly describes an Account Aggregator service, personal-finance use
cases, consent-driven journeys, FIU usage, and multiple Indian financial-data sets.
This is a research recommendation only. The maintainer should confirm whether
OneMoney's commercial and technical onboarding model is suitable for Securo before
any connector work begins.

Official source: <https://www.onemoney.in/>

## Expected Securo flow

1. Securo creates an AA consent request for the user's selected data range and data
   types.
2. The user completes the provider's OTP and consent journey on the provider-hosted
   page or approved mobile flow.
3. The provider redirects to Securo or sends a signed status notification.
4. Securo polls or receives consent status until the consent is active, rejected,
   expired, or revoked.
5. Securo requests the available financial information from the provider.
6. The provider obtains data from participating Financial Information Providers
   (FIPs) and returns the consent-scoped result.
7. Securo maps accounts, balances, and transactions into its existing normalized
   provider models.
8. Securo stores only the credentials and consent metadata required for future
   refreshes, with expiry and revocation handled explicitly.

## Mapping to Securo

An Indian AA connector should implement the existing `BankProvider` contract:

- `get_oauth_url` or a provider-specific consent-start method for the hosted flow
- `handle_oauth_callback` for the redirect result, if the provider uses callbacks
- `get_accounts` for discovered accounts and balances
- `get_transactions` for consent-scoped transaction history
- `refresh_credentials` for provider tokens or sessions
- `trigger_refresh` when the provider exposes an on-demand data refresh

The connector should normalize provider data into `AccountData`, `TransactionData`,
and `ConnectionData`. It should not add direct bank login or screen scraping.

Likely implementation areas are:

- `backend/app/providers/`
- `backend/app/providers/__init__.py`
- `backend/app/core/config.py`
- connection API and frontend connection flow
- provider-specific mocked contract tests
- setup and deployment documentation

## Provider due diligence checklist

Before implementation, confirm in writing:

- The provider can onboard Securo as an FIU or through an approved FIU partner.
- The provider's current RBI/Sahamati status and role are suitable for this use case.
- Consent creation, account selection, expiry, renewal, and revocation APIs exist.
- Account, balance, and transaction data are available through documented APIs.
- Redirect, deep-link, webhook, and polling behavior are documented.
- A sandbox and deterministic test data are available.
- Coverage includes the Indian banks and financial institutions needed by Securo users.
- Pricing, minimum commitments, rate limits, and support SLAs are known.
- Data residency, encryption, retention, deletion, and incident procedures are clear.
- The provider permits a self-hosted open-source application to use the service.

## Scope boundaries

This research does not propose:

- Direct connections to bank websites
- Collection or storage of bank passwords
- Screen scraping
- Payment initiation
- An assertion that Securo is itself an AA, FIP, or FIU
- Production credentials or live customer data in tests

CSV, OFX, QIF, CAMT, and existing manual workflows remain available while provider
selection and onboarding are investigated.

## Decision requested from maintainers

1. Is Account Aggregator connectivity useful for the project at this stage?
2. Which provider should be contacted first?
3. Is the maintainer willing to pursue the provider's commercial onboarding process?
4. Should the first implementation be limited to bank accounts, balances, and
   transactions?
5. Which consent and data-retention policy should the connector follow?
