---
test: ../transaction_test.md
status: passed
started: 2026-10-16T11:54:39.454Z
duration_s: 198
session_id: c00ddb63-dd0b-44e0-adee-76140b185dd0
---

# Record an expense — Result

## Create the admin account ✓ passed (95.6s)
md5: a3adb5d43dd67a5b443d368529f6dc18
Open http://localhost:3000/setup.
Enter "Jamie Lee" as your name, "jamie@example.com" as the email, and "Securo-Test-2026!" in both the "Password" and "Confirm password" fields. Keep "USD" as the currency.
Click "Create Admin Account".
Verify the dashboard is shown with a greeting that includes "Jamie Lee".
If the "Sidebar Navigation" tour is shown, click "Skip tour".

## Add an expense ✓ passed (42.5s)
md5: 9374e31f76b9ae5a0c4a471e83f1adf2
Click "Transactions" in the sidebar and click the "Add Transaction" button at the top right of the page.
In the "Add Transaction" dialog, enter "Weekly groceries" as the description and 45.50 as the amount.
Keep today's date, "Expense" as the type and "Wallet" as the account.
Click "Save".

## Check the transaction ✓ passed (51.9s)
md5: f877dd59448cbb7a25f63e8ba5fe1385
Verify the transactions list shows "Weekly groceries" on the "Wallet" account with an amount of -$45.50, waiting for it to update if needed.
Click "Accounts" in the sidebar.
Verify the "Manual Accounts" list shows "Wallet" with a balance of -$45.50.
