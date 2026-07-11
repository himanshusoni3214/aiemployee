from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read_frontend(path: str) -> str:
    return (ROOT / "frontend" / path).read_text(encoding="utf-8")


class FrontendRuntimeContractTests(unittest.TestCase):
    def test_crud_page_exposes_runtime_fallback_contract(self):
        source = read_frontend("components/CrudPage.tsx")

        self.assertIn("data-voryx-crud-page", source)
        self.assertIn("data-voryx-crud-path={path}", source)
        self.assertIn("data-voryx-crud-defaults={JSON.stringify(defaults)}", source)
        self.assertIn("data-voryx-crud-save", source)
        self.assertIn("data-voryx-crud-edit", source)
        self.assertIn("data-voryx-crud-archive", source)
        self.assertIn("type=\"button\"", source)
        self.assertIn("data-voryx-crud-save", source)
        self.assertIn("data-voryx-action-path={`${path}/${item.id}/dry-run`}", source)
        self.assertIn("data-voryx-action-path={`${path}/${item.id}/test-run`}", source)

    def test_action_runtime_does_not_intercept_crud_controls(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertNotIn("data-voryx-crud-save], [data-voryx-crud-edit]", source)
        self.assertNotIn("handleCrudClick", source)
        self.assertNotIn("readCrudForm", source)
        self.assertNotIn("setCrudField", source)
        self.assertNotIn("apiPost", source)
        self.assertIn("button[data-voryx-action-path]", source)
        self.assertIn("select[data-voryx-company-selector]", source)

    def test_action_runtime_surfaces_backend_job_state(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("fetch(`/api/jobs/${jobId}`", source)
        self.assertIn("const terminalStates = new Set(['completed', 'failed', 'blocked', 'cancelled', 'skipped'])", source)
        self.assertIn("const problemStates = new Set(['failed', 'blocked', 'cancelled', 'skipped'])", source)
        self.assertIn("result?.message || `${capitalize(label)} request accepted`", source)

    def test_company_selector_does_not_keep_stale_dependent_filters(self):
        company_selector = read_frontend("components/CompanySelector.tsx")
        query_selector = read_frontend("components/QuerySelector.tsx")
        company_selection = read_frontend("lib/companySelection.ts")
        system_page = read_frontend("app/system/page.tsx")

        self.assertIn("params.delete('campaign_id')", company_selector)
        self.assertIn("params.delete('employee_id')", company_selector)
        self.assertIn("resetParams.forEach((resetParam) => params.delete(resetParam))", query_selector)
        self.assertIn("defaultToSingleActive !== true", company_selection)
        self.assertIn("CompanySelector companies={companies} selectedCompanyId={companyId} allowAll label=\"System scope\"", system_page)

    def test_company_selector_exposes_non_react_fallback_contract(self):
        source = read_frontend("components/CompanySelector.tsx")

        self.assertIn("htmlFor={selectId}", source)
        self.assertIn("id={selectId}", source)
        self.assertIn("data-voryx-company-selector=\"true\"", source)
        self.assertIn("data-voryx-company-param=\"company_id\"", source)
        self.assertIn("data-voryx-allow-all={allowAll ? 'true' : 'false'}", source)

    def test_company_selector_marks_react_navigation_for_fallback_guard(self):
        source = read_frontend("components/CompanySelector.tsx")

        self.assertIn("select.dataset.voryxReactNavigationHref", source)
        self.assertIn("const targetHref = new URL(", source)
        self.assertIn("window.location.href", source)
        self.assertIn("onChange={(event) => changeCompany(event.target.value, event.currentTarget)}", source)
        self.assertIn("window.location.assign(targetHref)", source)

    def test_action_runtime_has_company_selector_change_listener(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("document.addEventListener('change'", source)
        self.assertIn("select[data-voryx-company-selector]", source)
        self.assertIn("handleCompanySelectorChange(event, select)", source)

    def test_action_runtime_company_selector_sets_company_id(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("const param = select?.dataset.voryxCompanyParam || 'company_id'", source)
        self.assertIn("const url = new URL(window.location.href)", source)
        self.assertIn("url.searchParams.set(param, selectedCompanyId)", source)

    def test_action_runtime_company_selector_removes_dependent_filters(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("const companySelectorResetParams = ['campaign_id', 'employee_id']", source)
        self.assertIn("companySelectorResetParams.forEach((resetParam) => url.searchParams.delete(resetParam))", source)

    def test_action_runtime_company_selector_handles_all_companies(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("if (value === '__all') return 'all'", source)
        self.assertIn("if (selectedCompanyId === 'all')", source)
        self.assertIn("url.searchParams.set(param, 'all')", source)

    def test_action_runtime_company_selector_handles_empty_selection(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("} else {\n      url.searchParams.delete(param);", source)
        self.assertIn("localStorage.removeItem(selectedCompanyStorageKey)", source)

    def test_action_runtime_company_selector_updates_local_storage(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("const selectedCompanyStorageKey = 'voryx:selectedCompanyId'", source)
        self.assertIn("localStorage.setItem(selectedCompanyStorageKey, selectedCompanyId)", source)
        self.assertIn("localStorage.removeItem(selectedCompanyStorageKey)", source)

    def test_action_runtime_company_selector_performs_real_navigation(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("window.location.assign(url.toString())", source)
        self.assertIn("if (urlsEquivalent(window.location.href, targetHref))", source)

    def test_action_runtime_company_selector_prevents_double_navigation(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("select.dataset.voryxCompanyFallbackHref", source)
        self.assertIn("select.dataset.voryxReactNavigationHref", source)
        self.assertIn("delete select.dataset.voryxCompanyFallbackHref", source)
        self.assertIn("urlsEquivalent(select.dataset.voryxReactNavigationHref, targetHref)", source)

    def test_crud_controls_have_explicit_labels(self):
        source = read_frontend("components/CrudPage.tsx")

        self.assertIn("htmlFor={fieldId}", source)
        self.assertIn("id={fieldId}", source)
        self.assertIn("<fieldset", source)
        self.assertIn("htmlFor={dayId}", source)
        self.assertIn("htmlFor={`${fieldId}-start`}", source)

    def test_employee_actions_support_scheduled_and_safety_locked_states(self):
        actions = read_frontend("components/ActionButtons.tsx")
        employees = read_frontend("app/employees/page.tsx")
        crud = read_frontend("components/CrudPage.tsx")
        safety = read_frontend("lib/hermesSafety.ts")

        self.assertIn("status === 'Scheduled'", actions)
        self.assertIn("supports_manual_run", actions)
        self.assertIn("Manual run unavailable in jobs_json mode", actions)
        self.assertIn(">Locked<", actions)
        self.assertIn("Safety blocked: this worker can send real Gmail prospect outreach.", actions)
        self.assertIn("isSafetyLockedHermesJob", actions)
        self.assertIn("b03a2d0f1149", safety)
        self.assertIn("Scheduled", employees)
        self.assertIn("isSafetyLockedHermesJob", employees)
        self.assertIn(">Locked<", crud)
        self.assertIn("Safety blocked: this worker can send real Gmail prospect outreach.", crud)

    def test_connector_capabilities_hide_unsupported_actions(self):
        actions = read_frontend("components/ActionButtons.tsx")
        crud = read_frontend("components/CrudPage.tsx")
        employees = read_frontend("app/employees/page.tsx")
        scheduler = read_frontend("app/scheduler/page.tsx")
        campaigns = read_frontend("app/campaigns/page.tsx")

        self.assertIn("supports_manual_run", actions)
        self.assertIn("supports_dry_run", actions)
        self.assertIn("data-voryx-manual-run-unavailable", actions)
        self.assertIn("canShowManualRun(item)", crud)
        self.assertIn("canShowDryRun(item)", crud)
        self.assertIn("/connectors/capabilities", employees)
        self.assertIn("/connectors/capabilities", scheduler)
        self.assertIn("/connectors/capabilities", campaigns)
        self.assertIn("capabilities={capabilities}", employees)
        self.assertIn("capabilities={capabilities}", scheduler)
        self.assertIn("capabilities={capabilities}", campaigns)

    def test_action_runtime_localizes_server_rendered_times(self):
        runtime = read_frontend("public/voryx-action-runtime.js")
        sync_status = read_frontend("components/SyncStatus.tsx")

        self.assertIn("data-voryx-sync-last", sync_status)
        self.assertIn("const localizeStaticTimes", runtime)
        self.assertIn("time[datetime]", runtime)
        self.assertIn("[data-voryx-sync-last]", runtime)


    def test_employees_page_keeps_model_policy_out_of_worker_cards(self):
        source = read_frontend("app/employees/page.tsx")

        self.assertIn("data-voryx-employee-schedule-cards", source)
        self.assertIn("data-voryx-disabled-worker-types", source)
        self.assertIn("isOperationalWorker", source)
        self.assertNotIn("<ModelPolicyPanel key={employee.id}", source)

    def test_outreach_controls_show_readiness_and_prospect_toggle(self):
        source = read_frontend("components/OutreachControlsPanel.tsx")

        self.assertIn("data-voryx-outreach-readiness", source)
        self.assertIn("data-voryx-ai-sales-control-center", source)
        self.assertIn("data-voryx-next-recommended-action", source)
        self.assertIn("data-voryx-sales-employee-stats", source)
        self.assertIn("data-voryx-primary-sales-actions", source)
        self.assertIn("data-voryx-sender-verification", source)
        self.assertIn("Lead-to-Email Workflow", source)
        self.assertIn("Approve all visible leads", source)
        self.assertIn("Generate drafts for", source)
        self.assertIn("Preview email batch", source)
        self.assertIn("Dry-run prepare", source)
        self.assertIn("Send 1 real email", source)
        self.assertIn("Schedule batch for next sending window", source)
        self.assertIn("Connect Gmail / Verify thread tracking", source)
        self.assertIn("Calling: not connected", source)
        self.assertIn("SEND 1 REAL EMAIL", source)
        self.assertIn("SEND CONTROLLED BATCH", source)
        self.assertIn("Approve all generated drafts", source)
        self.assertIn("Approve selected drafts", source)
        self.assertIn("Batch Preview", source)

    def test_campaign_detail_has_operational_sections(self):
        source = read_frontend("app/campaigns/page.tsx")

        self.assertIn("data-voryx-campaign-detail-sections", source)
        self.assertIn("AI Sales Employee Control Center", source)
        self.assertIn("Company &gt; Campaign &gt; AI Sales Employee", source)
        self.assertIn("Current blocker:", source)
        self.assertIn("<details", source)
        self.assertIn("Advanced", source)
        for label in ["Goal", "Leads", "Lead Files", "Email Sending Workflow", "Replies and Meetings", "Calling", "Daily Report", "Hermes Sync and Job IDs", "Raw Schedules", "Raw Employees", "Model Policy"]:
            self.assertIn(label, source)

    def test_jobs_page_displays_delivery_evidence_fields(self):
        source = read_frontend("app/jobs/page.tsx")

        self.assertIn("delivery_status", source)
        self.assertIn("recipient_email", source)
        self.assertIn("provider_message_id", source)
        self.assertIn("evidence_type", source)
        self.assertIn("verification_reason", source)
        self.assertIn("'Imported'", source)
        self.assertIn("'Synced'", source)

    def test_api_errors_show_concise_detail_message(self):
        source = read_frontend("lib/api.ts")

        self.assertIn("function errorMessage", source)
        self.assertIn("detail?.detail?.message", source)
        self.assertIn("console.error('API request failed'", source)
        self.assertIn("throw new Error(errorMessage", source)


if __name__ == "__main__":
    unittest.main()
