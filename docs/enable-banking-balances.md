# Enable Banking balance selection

`ENABLE_BANKING_BALANCE_PRIORITY` is a comma-separated list of Enable Banking
BalanceStatus codes, most preferred first. Its default is
`XPCD,ITAV,ITBD,CLAV,CLBD,OPBD,OPAV`.

| Code | Enable Banking meaning | Selection rationale |
| --- | --- | --- |
| XPCD | Expected / instant balance | Current snapshot, preferred |
| ITAV | Interim available | Available during the day |
| ITBD | Interim booked | Booked during the day |
| CLAV | Closing available | Closing fallback |
| CLBD | Closing booked / accounting balance | May represent the previous close |
| OPBD | Opening booked | Previous report's closing book balance |
| OPAV | Opening available | Start-of-day fallback |

Source: [Enable Banking BalanceStatus](https://enablebanking.com/docs/api/reference/#balancestatus).
These are balance **semantics**, not timestamps: this ordering does not prove
that one bank's response is fresher than another. Available/expected balances
can include pending entries, holds or credit facilities and need not reconcile
to booked transactions. Operators wanting a booked/accounting view can use
`ENABLE_BANKING_BALANCE_PRIORITY=ITBD,CLBD,OPBD` or restore the previous ordering
with `CLBD,ITAV,CLAV,XPCD,OPBD,OPAV`.

If none of the configured codes is returned, selection retains the previous
fallback to the first balance in the bank's response. Invalid/duplicate codes
fail configuration validation. No additional API requests are made.

Set the variable in the backend and worker environments (the supplied Compose
files share it through `x-backend-env`). Restarting those processes is required
for a configuration change to take effect. The frontend is unchanged.
