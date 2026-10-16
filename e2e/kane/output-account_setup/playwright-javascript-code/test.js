import testmu, { resolveVar, setVar } from '@testmuai/playwright-bindings';
import { expect } from '@playwright/test';

testmu.configure({
  "build": "c06e3d92-d0c0-47bf-a9a8-ddf53543e3ac",
  "name": "Setup Admin and Add Savings Account",
  "tcId": "TC-202",
  "network": (process.env.NETWORK ?? "false").toLowerCase() === "true",
  "autoHealVersion": "AH2",
  "defaultActionTimeoutMs": 10000,
  "defaultNavigationTimeoutMs": 60000,
  "kaneRunV4": true
});

testmu.test("Setup Admin and Add Savings Account", async (page) => {
  let element_0, element_1, element_2, element_3, element_7, element_8, element_9, element_10, element_4, element_5, element_6, element_14;
  await testmu.step("Navigate to http://localhost:3000/setup", { instructionId: "39a6ef5d-e654-4fa2-a599-e693970ed4dd" }, async () => {
    await page.goto("http://localhost:3000/setup");
  });
  
  await testmu.step("Waiting for the form fields to be filled in", { instructionId: "49bc893e-af61-4f56-9df5-6839fbd5abcd" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Type \"Alex Morgan\" into \"Your name\"", { instructionId: "fa8f0d9b-59a3-415f-b863-f47f914a2355" }, async () => {
    element_0 = page.locator("internal:role=textbox[name=\"Your name\"i]");
    await element_0.click();
    await element_0.fill("Alex Morgan");
  });
  
  await testmu.step("Type \"alex@example.com\" into \"Email\"", { instructionId: "89b19e09-2b65-4399-8d3e-0aa67e9e219b" }, async () => {
    element_1 = page.locator("internal:role=textbox[name=\"Email\"i]");
    await element_1.click();
    await element_1.fill("alex@example.com");
  });
  
  await testmu.step("Type \"Securo-Test-2026!\" into \"Password\"", { instructionId: "b165c315-5d52-4809-a4ff-7123df52bbb1" }, async () => {
    element_2 = page.locator("internal:role=textbox[name=\"Password\"s]");
    await element_2.click();
    await element_2.fill("Securo-Test-2026!");
  });
  
  await testmu.step("Typing the confirm password", { instructionId: "a981f33c-72e7-477e-b909-c79f820f2123" }, async () => {
    element_3 = page.locator("internal:role=textbox[name=\"Confirm password\"i]");
    await element_3.click();
    await element_3.fill("Securo-Test-2026!");
  });
  
  await testmu.step("Clicking Create Admin Account button", { instructionId: "33df3014-1893-4c03-8236-5b370df6c35b" }, async () => {
    element_7 = page.locator("internal:role=button[name=\"Create Admin Account\"i]");
    await element_7.click();
  });
  
  await testmu.step("Waiting for admin account creation to finish", { instructionId: "97fa8b74-bcc6-45b5-a078-8294bbae78ca" }, async () => {
    await page.waitForTimeout(1500);
  });
  
  await testmu.step("Checking whether the dashboard is shown", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("dashboard_shown_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {30: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(30).innerText.includes('Available balance'));\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=main"], query: "whether the dashboard content is present in the main page area", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The dashboard is shown." }));
  });
  
  await testmu.step("Reading the dashboard greeting", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("dashboard_greeting_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {31: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(31).innerText);\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:text=\"Good afternoon, Alex Morgan\"i"], query: "the greeting text in the dashboard main area", expectedValue: "Alex Morgan", needsUnitConversion: false, operator: "contains", transforms: [], condition: "A greeting includes \"Alex Morgan\"." }));
  });
  
  await testmu.step("Assertion check", { instructionId: "784ff798-8e1f-40ee-968b-e24fc3dd663a" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The dashboard is shown.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "true", "extracted_value": "{{dashboard_shown_check}}", "store_key": "dashboard_shown_check", "variable_refs": {"{{dashboard_shown_check}}": "true"}}, {"description": "A greeting includes \"Alex Morgan\".", "passed": true, "operator": "contains", "transforms": [], "json_path": null, "expected": "Alex Morgan", "extracted_value": "{{dashboard_greeting_check}}", "store_key": "dashboard_greeting_check", "variable_refs": {"{{dashboard_greeting_check}}": "Good afternoon, Alex Morgan"}}], "sub_checks": [{"description": "The dashboard is shown.", "store_key": "dashboard_shown_check", "expected_value": "true", "extracted_value": "{{dashboard_shown_check}}", "operator": "equals", "transforms": []}, {"description": "A greeting includes \"Alex Morgan\".", "store_key": "dashboard_greeting_check", "expected_value": "Alex Morgan", "extracted_value": "{{dashboard_greeting_check}}", "operator": "contains", "transforms": []}], "composite_operator": "and", "claim": "the dashboard is shown with a greeting that includes \"Alex Morgan\""});
  });
  
  await testmu.step("Checking whether the Sidebar Navigation tour is present", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("cp_1", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {78: 0};\n  const el = (i) => els[__m[i]];\n  const __v = (el(78).textContent.trim() === 'Sidebar Navigation');\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=heading[name=\"Sidebar Navigation\"i]"], query: "whether the Sidebar Navigation tour is shown", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The \"Sidebar Navigation\" tour is shown." }));
  });
  
  if (await testmu.evaluateBranch([{"description": "The \"Sidebar Navigation\" tour is shown.", "store_key": "cp_1", "expected_value": "true", "stored_value": "{{cp_1}}", "operator": "equals", "transforms": [], "extracted_value": "{{cp_1}}"}], "and")) {
    await testmu.step("Skipping the tour by clicking Skip tour button", { instructionId: "33ea6bce-30d9-41c9-899f-fb53ae9454e1" }, async () => {
      element_8 = page.locator("internal:role=button[name=\"Skip tour\"i]");
      await element_8.click();
    });
  } else {
    page = await testmu.executeKaneCli("do nothing", page);
  }
  
  await testmu.step("Waiting for conditional checkpoint branch action to finish", { instructionId: "ab00e0f2-ee84-4997-9b8f-32cb5790cd77" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("whether the guided tour is visible on the page", { instructionId: "5d03b7c0-356b-4887-a02f-65d2283be452" }, async () => {
    setVar("tour_no_longer_shown", await testmu.visionQuery(page, "whether the guided tour is visible on the page", ""));
  });
  
  await testmu.step("Assertion check", { instructionId: "b7c3c2d0-4c47-497d-a515-6e1fa16da218" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The tour is no longer shown.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "false", "extracted_value": "{{tour_no_longer_shown}}", "store_key": "tour_no_longer_shown", "variable_refs": {"{{tour_no_longer_shown}}": "false"}}], "sub_checks": [{"description": "The tour is no longer shown.", "store_key": "tour_no_longer_shown", "expected_value": "false", "extracted_value": "{{tour_no_longer_shown}}", "operator": "equals", "transforms": []}], "composite_operator": "and", "claim": "The tour is no longer shown."});
  });
  
  await testmu.step("Clicking Accounts navigation link in the left sidebar", { instructionId: "4aad62d8-f449-42f0-b238-63de4ed8f0a7" }, async () => {
    element_9 = page.locator("internal:role=link[name=\"Accounts\"i]");
    await element_9.click();
  });
  
  await testmu.step("Clicking Add Account button", { instructionId: "94f1727c-e4bb-475d-abed-cf849c041beb" }, async () => {
    element_10 = page.locator("internal:role=button[name=\"Add Account\"i]");
    await element_10.click();
  });
  
  await testmu.step("Waiting for the next action to complete", { instructionId: "afba8510-a5f1-4d2d-937c-4b4e2530dd55" }, async () => {
    await page.waitForTimeout(1000);
  });
  
  await testmu.step("Type \"Emergency Fund\" into \"textbox\"", { instructionId: "41d59b44-536d-4a13-b561-4f2cb0e16d45" }, async () => {
    element_4 = page.locator("internal:role=textbox");
    await element_4.click();
    await element_4.fill("Emergency Fund");
  });
  
  await testmu.step("Select \"Savings\" in \"combobox\"", { instructionId: "b2612988-bd53-4d7a-a1a3-9f11c1719706" }, async () => {
    element_5 = page.locator("internal:role=combobox >> nth=0");
    await element_5.selectOption({ label: "Savings" });
  });
  
  await testmu.step("Type \"1000\" into \"spinbutton\"", { instructionId: "ea1ceca0-1ef3-4edc-90e2-b0fca73a310b" }, async () => {
    element_6 = page.locator("internal:role=spinbutton");
    await element_6.click();
    await element_6.fill("1000");
  });
  
  await testmu.step("Waiting for the populated Add Account dialog to remain stable", { instructionId: "8c1ce4d2-cd91-4e46-8b66-5a12d5841987" }, async () => {
    await page.waitForTimeout(500);
  });
  
  await testmu.step("Clicking the Save button", { instructionId: "785a86e4-1741-4074-9414-1a781e40b2b1" }, async () => {
    element_14 = page.locator("internal:role=button[name=\"Save\"i]");
    await element_14.click();
  });
  
  await testmu.step("Checking the Emergency Fund manual account details", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("emergency_fund_account_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {38: 0, 41: 1};\n  const el = (i) => els[__m[i]];\n  const __v = (el(38).innerText.includes('Emergency Fund') && el(38).innerText.includes('Savings') && el(41).innerText.trim() === '$1,000.00');\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=link[name=\"Emergency Fund Savings\"s]", "internal:role=main >> internal:text=\"$1,000.00\"i"], query: "whether the Emergency Fund entry in the Manual Accounts list has Savings type and a $1,000.00 balance", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The \"Manual Accounts\" list shows \"Emergency Fund\" of type \"Savings\" with a balance of $1,000.00." }));
  });
  
  await testmu.step("Checking the Wallet manual account balance", async () => {
    await page.waitForLoadState("domcontentloaded", { timeout: 5000 });
    setVar("wallet_account_check", await testmu.textualAnalyzer(page, { wrappedJs: "(els) => {\n  const __m = {45: 0, 48: 1};\n  const el = (i) => els[__m[i]];\n  const __v = (el(45).innerText.includes('Wallet') && el(48).innerText.trim() === '$0.00');\n  return (typeof __v === 'boolean' ? String(__v) : __v);\n}", locators: ["internal:role=link[name=\"Wallet Checking\"s]", "internal:role=main >> internal:text=\"$0.00\"i"], query: "whether the Wallet entry in the Manual Accounts list has a $0.00 balance", expectedValue: "true", needsUnitConversion: false, operator: "equals", transforms: [], condition: "The \"Manual Accounts\" list still shows \"Wallet\" with a balance of $0.00." }));
  });
  
  await testmu.step("Assertion check", { instructionId: "00185e0d-c491-4151-97d1-ed4622a9232c" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The \"Manual Accounts\" list shows \"Emergency Fund\" of type \"Savings\" with a balance of $1,000.00.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "true", "extracted_value": "{{emergency_fund_account_check}}", "store_key": "emergency_fund_account_check", "variable_refs": {"{{emergency_fund_account_check}}": "true"}}], "sub_checks": [{"description": "The \"Manual Accounts\" list shows \"Emergency Fund\" of type \"Savings\" with a balance of $1,000.00.", "store_key": "emergency_fund_account_check", "expected_value": "true", "extracted_value": "{{emergency_fund_account_check}}", "operator": "equals", "transforms": []}], "composite_operator": "and", "claim": "the \"Manual Accounts\" list shows \"Emergency Fund\" of type \"Savings\" with a balance of $1,000.00"});
  });
  
  await testmu.step("Assertion check", { instructionId: "4feeb37c-640f-427c-af35-402ee6b775a1" }, async () => {
    await testmu.verifyAssertion(page, "Assertion check", {"operator": ["equals"], "assertion_operands": [], "left_operand": null, "right_operand": null, "operands": [], "sub_results": [{"description": "The \"Manual Accounts\" list still shows \"Wallet\" with a balance of $0.00.", "passed": true, "operator": "equals", "transforms": [], "json_path": null, "expected": "true", "extracted_value": "{{wallet_account_check}}", "store_key": "wallet_account_check", "variable_refs": {"{{wallet_account_check}}": "true"}}], "sub_checks": [{"description": "The \"Manual Accounts\" list still shows \"Wallet\" with a balance of $0.00.", "store_key": "wallet_account_check", "expected_value": "true", "extracted_value": "{{wallet_account_check}}", "operator": "equals", "transforms": []}], "composite_operator": "and", "claim": "the \"Manual Accounts\" list still shows \"Wallet\" with a balance of $0.00"});
  });
});

testmu.run();