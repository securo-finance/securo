import testmu, { resolveVar, setVar } from '@testmuai/playwright-bindings';
import { expect } from '@playwright/test';

testmu.configure({
  "build": "ff724adf-46c0-468c-9a0a-2ed4a284973b",
  "name": "Create Admin Account and Add Expense Transaction",
  "tcId": "TC-203",
  "network": (process.env.NETWORK ?? "false").toLowerCase() === "true",
  "autoHealVersion": "AH2",
  "defaultActionTimeoutMs": 10000,
  "defaultNavigationTimeoutMs": 60000,
  "kaneRunV4": true
});

testmu.test("Create Admin Account and Add Expense Transaction", async (page) => {
  let element_0, element_1, element_2, element_3, element_6, element_7, element_8, element_9, element_4, element_5, element_12, element_13;
  await testmu.step("Navigate to http://localhost:3000/setup", { instructionId: "1db48caf-8afc-4249-a64b-4139118f6780" }, async () => {
    await page.goto("http://localhost:3000/setup");
  });
  
  await testmu.step("Waiting for the next queued step to run", { instructionId: "fc851c6b-32f8-4a39-af7f-91cff30671f7" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Type \"Jamie Lee\" into \"Your name\"", { instructionId: "56ed604d-14ec-4523-bac7-5e8f581f5211" }, async () => {
    element_0 = page.locator("internal:role=textbox[name=\"Your name\"i]");
    await element_0.click();
    await element_0.fill("Jamie Lee");
  });
  
  await testmu.step("Type \"jamie@example.com\" into \"Email\"", { instructionId: "d483a515-f5bc-4ad4-ace7-3910119e594a" }, async () => {
    element_1 = page.locator("internal:role=textbox[name=\"Email\"i]");
    await element_1.click();
    await element_1.fill("jamie@example.com");
  });
  
  await testmu.step("Type \"Securo-Test-2026!\" into \"Password\"", { instructionId: "52086341-92a0-4082-8acd-faa266a20db9" }, async () => {
    element_2 = page.locator("internal:role=textbox[name=\"Password\"s]");
    await element_2.click();
    await element_2.fill("Securo-Test-2026!");
  });
  
  await testmu.step("Type \"Securo-Test-2026!\" into \"Confirm password\"", { instructionId: "3adfeade-aac4-4472-a98e-ffc5e53de3d4" }, async () => {
    element_3 = page.locator("internal:role=textbox[name=\"Confirm password\"i]");
    await element_3.click();
    await element_3.fill("Securo-Test-2026!");
  });
  
  await testmu.step("Click \"Create Admin Account\"", { instructionId: "877fa0ef-6bac-464a-af58-9a6dfc4c8662" }, async () => {
    element_6 = page.locator("internal:role=button[name=\"Create Admin Account\"i]");
    await element_6.click();
  });
  
  await testmu.step("Waiting for account creation to finish", { instructionId: "7f6d985d-16f4-4fc9-99f4-4d6a8362d8c8" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Checking whether the dashboard is shown", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("dashboard_shown_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {30: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(30).innerText.trim().length > 0);\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=main"], query: "whether the dashboard main content is shown", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The dashboard is shown." }));
  });
  
  await testmu.step("Reading the dashboard greeting", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("dashboard_greeting_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {31: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(31).innerText);\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:text=\"Good afternoon, Jamie Lee\"i"], query: "the greeting text in the dashboard main content", expectedValue: "Jamie Lee", needsUnitConversion: false, operator: "contains", transforms: [], condition: "A greeting includes \"Jamie Lee\"." }));
  });
  
  await testmu.step("Assertion check", { instructionId: "2264636d-7ae6-4b62-93b8-a3430755a913" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The dashboard is shown.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "true", "extracted_value": "{{dashboard_shown_check}}", "store_key": "dashboard_shown_check", "variable_refs": {"{{dashboard_shown_check}}": "true"}}, {"description": "A greeting includes \"Jamie Lee\".", "passed": true, "operator": "contains", "transforms": [], "json_path": null, "expected": "Jamie Lee", "extracted_value": "{{dashboard_greeting_check}}", "store_key": "dashboard_greeting_check", "variable_refs": {"{{dashboard_greeting_check}}": "Good afternoon, Jamie Lee"}}], "sub_checks": [{"description": "The dashboard is shown.", "store_key": "dashboard_shown_check", "expected_value": "true", "extracted_value": "{{dashboard_shown_check}}", "operator": "equals", "transforms": []}, {"description": "A greeting includes \"Jamie Lee\".", "store_key": "dashboard_greeting_check", "expected_value": "Jamie Lee", "extracted_value": "{{dashboard_greeting_check}}", "operator": "contains", "transforms": []}], "composite_operator": "and", "claim": "the dashboard is shown with a greeting that includes \"Jamie Lee\""});
  });
  
  await testmu.step("Waiting 500 ms for the tour to proceed", { instructionId: "8b7afd08-a4b2-4c8f-ae7d-7c5e64b0b7ba" }, async () => {
    await page.waitForTimeout(500);
  });
  
  await testmu.step("Waiting 1 second", { instructionId: "251af3a8-3761-42e7-a5f6-ebf3075f02ad" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Checking whether the Sidebar Navigation tour is shown", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("sidebar_navigation_tour_shown", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {78: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (!!el(78) && el(78).getClientRects().length > 0);\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=heading[name=\"Sidebar Navigation\"i]"], query: "whether the Sidebar Navigation tour is shown", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The \"Sidebar Navigation\" tour is shown." }));
  });
  
  if (await testmu.evaluateBranch([{"description": "The \"Sidebar Navigation\" tour is shown.", "store_key": "sidebar_navigation_tour_shown", "expected_value": "true", "stored_value": "{{sidebar_navigation_tour_shown}}", "operator": "equals", "transforms": [], "extracted_value": "{{sidebar_navigation_tour_shown}}"}], "and")) {
    await testmu.step("Clicking Skip tour button", { instructionId: "d3c01859-1830-499a-bf9b-c62d1b2db67d" }, async () => {
      element_7 = page.locator("internal:role=button[name=\"Skip tour\"i]");
      await element_7.click();
    });
  } else {
    page = await testmu.executeKaneCli("do nothing", page);
  }
  
  await testmu.step("Waiting for 500 ms", { instructionId: "87297617-9aa9-406a-82c5-de867965c871" }, async () => {
    await page.waitForTimeout(500);
  });
  
  await testmu.step("Clicking Transactions navigation link", { instructionId: "90c88f50-d5ee-4004-9c6d-272bb618c813" }, async () => {
    element_8 = page.locator("internal:role=link[name=\"Transactions\"i]");
    await element_8.click();
  });
  
  await testmu.step("Clicking + Add Transaction button", { instructionId: "f24f3403-f239-4049-af4b-1df3b3adc911" }, async () => {
    element_9 = page.locator("internal:role=button[name=\"+ Add Transaction\"i]");
    await element_9.click();
  });
  
  await testmu.step("Waiting for the next input fields to load", { instructionId: "0e2f7902-2e80-46c2-8df3-36216ad28039" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Type \"Weekly groceries\" into \"combobox\"", { instructionId: "bec67fa5-4c8a-4544-91ff-eac54c079176" }, async () => {
    element_4 = page.locator("internal:role=combobox >> internal:has-text=/^$/");
    await element_4.click();
    await element_4.fill("Weekly groceries");
  });
  
  await testmu.step("Type \"45.50\" into \"textbox\"", { instructionId: "c2512472-5663-426b-9bef-8ebd56909663" }, async () => {
    element_5 = page.locator("internal:role=dialog[name=\"Add Transaction\"i] >> input[type=\"text\"]");
    await element_5.click();
    await element_5.fill("45.50");
  });
  
  await testmu.step("Clicking Save transaction button in Add Transaction dialog", { instructionId: "79141f2f-cc94-4b34-8872-112fc203c042" }, async () => {
    element_12 = page.locator("internal:role=button[name=\"Save\"s]");
    await element_12.click();
  });
  
  await testmu.step("Checking the Weekly groceries transaction details", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("weekly_groceries_wallet_amount_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {56: 0};\n  const el = (i) => els[__m[i]];\n  const __v = ((() => { const cells = Array.from(el(56).querySelectorAll('[role=\"cell\"], td')); const text = cells.map(c => c.innerText.trim()); return text.includes('Weekly groceries') && text.includes('Wallet') && text.some(t => t === '\u2212$45.50' || t === '-$45.50'); })());\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=row[name=\"10/16/2026 Weekly groceries\"i]"], query: "whether the transactions list includes the Weekly groceries transaction on the Wallet account with an amount of -$45.50", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The transactions list shows \"Weekly groceries\" on the \"Wallet\" account with an amount of -$45.50." }));
  });
  
  await testmu.step("Assertion check", { instructionId: "3f29cb08-4825-4a62-9e5c-ca9e84ba907c" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The transactions list shows \"Weekly groceries\" on the \"Wallet\" account with an amount of -$45.50.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "true", "extracted_value": "{{weekly_groceries_wallet_amount_check}}", "store_key": "weekly_groceries_wallet_amount_check", "variable_refs": {"{{weekly_groceries_wallet_amount_check}}": "true"}}], "sub_checks": [{"description": "The transactions list shows \"Weekly groceries\" on the \"Wallet\" account with an amount of -$45.50.", "store_key": "weekly_groceries_wallet_amount_check", "expected_value": "true", "extracted_value": "{{weekly_groceries_wallet_amount_check}}", "operator": "equals", "transforms": []}], "composite_operator": "and", "claim": "the transactions list shows \"Weekly groceries\" on the \"Wallet\" account with an amount of -$45.50"});
  });
  
  await testmu.step("Clicking Accounts navigation link in the left sidebar", { instructionId: "7a90408b-f2bc-48a6-8cde-62368025e0f8" }, async () => {
    element_13 = page.locator("internal:role=link[name=\"Accounts\"i]");
    await element_13.click();
  });
  
  await testmu.step("Waiting 1000 ms for the Accounts page to finish loading", { instructionId: "768aa544-4dfd-4ec9-b11f-c6a23cd0b37b" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Reading the Wallet balance in Manual Accounts", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("wallet_balance_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {40: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(40).textContent);\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=main >> internal:text=\"-$\"i"], query: "the balance amount shown for the Wallet entry in the Manual Accounts list", expectedValue: "-$45.50", needsUnitConversion: false, operator: "equals", transforms: ["strip"], condition: "The \"Manual Accounts\" list shows \"Wallet\" with a balance of -$45.50." }));
  });
  
  await testmu.step("Assertion check", { instructionId: "8f685f0f-c0f2-4202-96e5-0105d8fbc281" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The \"Manual Accounts\" list shows \"Wallet\" with a balance of -$45.50.", "passed": true, "operator": "equals", "transforms": ["strip"], "json_path": null, "expected": "-$45.50", "extracted_value": "{{wallet_balance_check}}", "store_key": "wallet_balance_check", "variable_refs": {"{{wallet_balance_check}}": "-$45.50"}}], "sub_checks": [{"description": "The \"Manual Accounts\" list shows \"Wallet\" with a balance of -$45.50.", "store_key": "wallet_balance_check", "expected_value": "-$45.50", "extracted_value": "{{wallet_balance_check}}", "operator": "equals", "transforms": ["strip"]}], "composite_operator": "and", "claim": "the \"Manual Accounts\" list shows \"Wallet\" with a balance of -$45.50"});
  });
});

testmu.run();