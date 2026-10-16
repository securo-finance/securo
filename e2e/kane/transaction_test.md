---
mode: testing
url: http://localhost:3000/setup
max_steps: 40
---

# Record an expense

## Create the admin account
Open http://localhost:3000/setup.
Enter "Jamie Lee" as your name, "jamie@example.com" as the email, and "Securo-Test-2026!" in both the "Password" and "Confirm password" fields. Keep "USD" as the currency.
Click "Create Admin Account".
Verify the dashboard is shown with a greeting that includes "Jamie Lee".
If the "Sidebar Navigation" tour is shown, click "Skip tour".

## Add an expense
Click "Transactions" in the sidebar and click the "Add Transaction" button at the top right of the page.
In the "Add Transaction" dialog, enter "Weekly groceries" as the description and 45.50 as the amount.
Keep today's date, "Expense" as the type and "Wallet" as the account.
Click "Save".

## Check the transaction
Verify the transactions list shows "Weekly groceries" on the "Wallet" account with an amount of -$45.50, waiting for it to update if needed.
Click "Accounts" in the sidebar.
Verify the "Manual Accounts" list shows "Wallet" with a balance of -$45.50.
