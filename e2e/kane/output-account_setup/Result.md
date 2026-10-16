---
test: ../account_setup_test.md
status: passed
started: 2026-10-16T11:50:29.823Z
duration_s: 204
session_id: 276ca05a-e707-4b89-9be2-b17dd1180c7c
---

# Set up Securo and add a savings account — Result

## Create the admin account ✓ passed (68.2s)
md5: 12b973dbaaa351c444709ea5415364fb
Open http://localhost:3000/setup.
Enter "Alex Morgan" as your name, "alex@example.com" as the email, and "Securo-Test-2026!" in both the "Password" and "Confirm password" fields. Keep "USD" as the currency.
Click "Create Admin Account".
Verify the dashboard is shown with a greeting that includes "Alex Morgan".

## Skip the tour ✓ passed (51.8s)
md5: dd6fa7338c3cc562818e01d51d2088c7
If the "Sidebar Navigation" tour is shown, click "Skip tour".
Verify the tour is no longer shown.

## Add a savings account ✓ passed (48.4s)
md5: e2de3a27fe0d368901b396420e296733
Click "Accounts" in the sidebar and click "Add Account".
In the "Add Account" dialog, enter "Emergency Fund" as the account name, select "Savings" as the type and enter 1000 as the balance.
Click "Save".

## Check the account ✓ passed (22.6s)
md5: 1682915ea73c26cb83da54e78af71a5f
Verify the "Manual Accounts" list shows "Emergency Fund" of type "Savings" with a balance of $1,000.00.
Verify the "Manual Accounts" list still shows "Wallet" with a balance of $0.00.
