---
mode: testing
url: http://localhost:3000/setup
max_steps: 40
---

# Set up Securo and add a savings account

## Create the admin account
Open http://localhost:3000/setup.
Enter "Alex Morgan" as your name, "alex@example.com" as the email, and "Securo-Test-2026!" in both the "Password" and "Confirm password" fields. Keep "USD" as the currency.
Click "Create Admin Account".
Verify the dashboard is shown with a greeting that includes "Alex Morgan".

## Skip the tour
If the "Sidebar Navigation" tour is shown, click "Skip tour".
Verify the tour is no longer shown.

## Add a savings account
Click "Accounts" in the sidebar and click "Add Account".
In the "Add Account" dialog, enter "Emergency Fund" as the account name, select "Savings" as the type and enter 1000 as the balance.
Click "Save".

## Check the account
Verify the "Manual Accounts" list shows "Emergency Fund" of type "Savings" with a balance of $1,000.00.
Verify the "Manual Accounts" list still shows "Wallet" with a balance of $0.00.
