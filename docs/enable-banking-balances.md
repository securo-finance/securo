# Enable Banking balance selection

Banks return several balances for one account. Securo stores one of them, picked
by `ENABLE_BANKING_BALANCE_PRIORITY`: a comma-separated list of Enable Banking
BalanceStatus codes, most preferred first. The default is
`ITBD,CLBD,OPBD,ITAV,CLAV,XPCD,OPAV`.

| Code | Enable Banking meaning | Why it sits there by default |
| --- | --- | --- |
| ITBD | Interim booked | Booked, and the most recent booked figure |
| CLBD | Closing booked | Booked, usually as of the previous close |
| OPBD | Opening booked | Booked, start of the period |
| ITAV | Interim available | Available: may include the credit line |
| CLAV | Closing available | Available: may include the credit line |
| XPCD | Expected | Includes pending entries; may include the credit line |
| OPAV | Opening available | Last resort |

Source: [Enable Banking BalanceStatus](https://enablebanking.com/docs/api/reference/#balancestatus).

Booked balances come first because many banks add the overdraft or credit line
to the available and expected ones. An account holding 1,000.00 with a 500.00
credit line can report 1,500.00 available, which is not what the account holds.

Anyone who prefers the amount they can spend can put the available codes first,
for example `ITAV,CLAV,XPCD,ITBD,CLBD,OPBD,OPAV`. The order used before this
setting existed was `CLBD,ITAV,CLAV,XPCD,OPBD,OPAV`.

If none of the configured codes is returned, Securo keeps the first balance in
the bank's response, as before. Unknown or repeated codes fail configuration
validation at startup. The setting makes no extra API requests, and it does not
apply to credit cards, which read their debt from their own balance types.

Set the variable for the backend and the Celery worker (the supplied Compose
files share it through `x-backend-env`) and restart them for a change to take
effect.
