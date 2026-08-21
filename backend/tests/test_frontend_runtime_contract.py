from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read_frontend(path: str) -> str:
    return (ROOT / "frontend" / path).read_text(encoding="utf-8")


class FrontendRuntimeContractTests(unittest.TestCase):
    def test_call_script_studio_separates_dirty_form_from_server_versions(self):
        source = read_frontend("components/CallScriptStudio.tsx")

        for contract in [
            "basePublishedVersion",
            "serverDraftVersion",
            "formValues",
            "isDirty",
            "changedFields",
            "currentContentHash",
            "Unsaved changes — not tested or published.",
            "No unpublished changes.",
            "Testing required for these changes.",
        ]:
            self.assertIn(contract, source)
        self.assertIn("Internal-test opening</div>", source)
        self.assertIn("Consented-lead opening</div>", source)
        self.assertNotIn("setDraft({ ...draft", source)

    def test_call_script_studio_one_click_publish_and_visible_reasons(self):
        source = read_frontend("components/CallScriptStudio.tsx")

        self.assertIn("/calling/allstate/script-versions/publish-changes", source)
        self.assertIn("Publish changes", source)
        self.assertIn("Make a script change before publishing.", source)
        self.assertIn("aria-describedby=\"publish-changes-reason\"", source)
        self.assertIn("title={publishReason}", source)
        self.assertIn("Advanced actions", source)

    def test_call_script_studio_supports_two_step_opening_and_detailed_playground_checks(self):
        source = read_frontend("components/CallScriptStudio.tsx")

        for contract in [
            "Opening style",
            "Full introduction",
            "Confirm person first",
            "Confirmed-person internal introduction",
            "Confirmed-person prospect introduction",
            "Wrong-person response",
            "Retell playground checks",
            "playgroundValidation.checks.map",
            "item.label",
            "Advanced playground transcript",
            "data-playground-validation",
        ]:
            self.assertIn(contract, source)

    def test_call_script_studio_has_field_errors_and_unsaved_change_protection(self):
        source = read_frontend("components/CallScriptStudio.tsx")

        self.assertIn("Customer-name variable is malformed. Use {{customer_name}}, not {customer_name}.", source)
        self.assertIn("aria-invalid={errors.length ? 'true' : 'false'}", source)
        self.assertIn("document.getElementById(`script-${field.replaceAll('.', '-')}`)?.focus()", source)
        self.assertIn("beforeunload", source)
        self.assertIn("You have unpublished script changes.", source)
        self.assertIn("Stay and continue editing", source)
        self.assertIn("Discard changes", source)
        self.assertIn("Save draft", source)

    def test_calling_panel_renders_backend_readiness_checks_and_blockers(self):
        source = read_frontend("components/AllstateCallingPanel.tsx")

        self.assertIn("/calling/allstate/contacts/upload", source)
        self.assertIn("/calling/allstate/dry-run", source)
        self.assertIn("/calling/allstate/campaign/start", source)
        self.assertIn("/calling/allstate/campaign/${action}", source)
        self.assertIn("readiness.checks", source)
        self.assertIn("READY TO CALL", source)
        self.assertIn("DRY RUN MY CONTACTS", source)
        self.assertIn("START APPROVED CALLING CAMPAIGN", source)
        self.assertIn("data-voryx-internal-test-tool", source)
        self.assertIn("/calling/allstate/internal-test-readiness", source)
        self.assertIn("/calling/allstate/internal-test-call", source)
        self.assertIn("Call this test number once", source)
        self.assertIn("Scheduled for", source)
        self.assertIn("Advanced technical details", source)

    def test_calling_consent_source_uses_presets_with_conditional_other_fields(self):
        source = read_frontend("components/AllstateCallingPanel.tsx")

        for contract in [
            "data-voryx-consent-source-setup",
            "data-voryx-source-preset",
            "data-voryx-organization-preset",
            "data-voryx-consent-wording-preset",
            "data-voryx-proof-preset",
            "data-voryx-evidence-preset",
            "sourcePreset === 'other'",
            "organizationPreset === 'other'",
            "consentWordingPreset === 'other'",
            "proofPreset === 'other'",
            "evidencePreset === 'other'",
            "Automated/synthesized calls are permitted",
        ]:
            self.assertIn(contract, source)
        self.assertNotIn('>Effective date<input', source)

    def test_publish_does_not_accept_empty_or_unverified_success(self):
        source = read_frontend("components/CallScriptStudio.tsx")

        self.assertIn("PUBLISH_VERIFICATION_MISSING", source)
        self.assertIn("EMPTY_OR_UNVERIFIED_RESPONSE", source)
        self.assertIn("The publish request returned no verified live result.", source)

    def test_api_client_preserves_structured_errors_and_empty_successes(self):
        source = read_frontend("lib/api.ts")

        for contract in [
            "class ApiError",
            "fieldErrors",
            "blockers",
            "recommendedAction",
            "requestId",
            "res.status === 204",
            "if (!body.trim())",
        ]:
            self.assertIn(contract, source)

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

    def test_outreach_controls_show_simple_email_marketing_workflow(self):
        source = read_frontend("components/OutreachControlsPanel.tsx")

        self.assertIn("data-voryx-email-marketing-employee", source)
        self.assertIn("data-voryx-next-recommended-action", source)
        self.assertIn("data-voryx-simple-email-actions", source)
        self.assertIn("data-voryx-outreach-mode", source)
        self.assertIn("showLeadWorkflow", source)
        self.assertIn("showEmailWorkflow", source)
        self.assertIn("leadSourceCampaignId", source)
        self.assertIn("source_campaign_id", source)
        self.assertIn("reviewCampaignId", source)
        self.assertIn("target_campaign_id: campaignId", source)
        self.assertIn("activeDrafts", source)
        self.assertIn("leadsFoundForDisplay", source)
        self.assertIn("approvedSourceLeadKeys", source)
        self.assertIn("allowedLeadKeys.has(draft.lead_key)", source)
        self.assertIn("showAllLeads", source)
        self.assertIn("Show all {allReviewItems.length} leads", source)
        self.assertIn("Approve all eligible leads", source)
        self.assertIn("approveEligibleLeads", source)
        self.assertIn("showEmailWorkflow && leadSourceCampaignId ? sourceApprovedLeads : approvedLeads", source)
        campaigns = read_frontend("app/campaigns/page.tsx")
        self.assertIn("leadSourceCampaignFor", campaigns)
        self.assertIn("targetCampaignId", campaigns)
        self.assertIn("lead[-_ ]research|lead[-_ ]generation", campaigns)
        self.assertIn("leadSourceCampaignId={leadSourceCampaign?.id}", campaigns)
        self.assertIn("Use approved Lead Research leads or connect this workflow to a lead source", source)
        self.assertIn("data-voryx-research-status", source)
        for label in ["Email-ready target", "Email-ready currently available", "Remaining needed", "Existing leads enriched", "Unchanged duplicates", "Enrichment exhausted", "Stop reason", "Last enrichment", "Pages checked"]:
            self.assertIn(label, source)
        self.assertIn("research_status", source)
        self.assertIn("remaining_to_target", source)
        self.assertIn("existing_enriched", source)
        self.assertIn("data-voryx-email-advanced", source)
        self.assertIn("data-voryx-sender-verification", source)
        self.assertIn("data-voryx-approved-sending-window", source)
        for label in ["Generate leads", "Approve all eligible leads", "Generate email draft", "Approve all drafts", "Send test", "Send approved emails", "Report"]:
            self.assertIn(label, source)
        for label in ["Email ready", "Phone ready", "Enrichment needed", "Missing all contact data", "DNC", "Duplicates", "Previously contacted"]:
            self.assertIn(label, source)
        self.assertIn("lead_category", source)
        self.assertIn("identity_needs_review", source)
        for label in ["Approved sending window", "Start time", "End time", "Start date", "End date", "Timezone", "Hourly limit"]:
            self.assertIn(label, source)
        self.assertIn("allowed_sending_days", source)
        self.assertIn("allowed_sending_hours", source)
        self.assertIn("hourly_send_limit", source)
        self.assertIn("allowed_sending_start_date", source)
        self.assertIn("allowed_sending_end_date", source)
        self.assertIn("Save draft changes", source)
        self.assertIn("Approve this draft", source)
        self.assertIn("Saving or approving will add it automatically", source)
        self.assertNotIn("Use draft as-is", source)
        self.assertNotIn("Use this draft", source)
        self.assertIn("SEND CONTROLLED BATCH", source)
        self.assertIn("Cold calling, text marketing and social outreach are separate employees", source)
        self.assertIn("Assumed emails without source evidence stay blocked", source)
        self.assertNotIn("Dry-run prepare", source)
        self.assertNotIn("Send 1 real email", source)

    def test_campaign_detail_has_operational_sections(self):
        source = read_frontend("app/campaigns/page.tsx")
        layout = read_frontend("app/layout.tsx")

        self.assertIn("data-voryx-campaign-detail-sections", source)
        self.assertIn("displayCampaigns", source)
        self.assertIn("Email Marketing Campaign", source)
        self.assertIn("Lead generation + email drafting + reporting", source)
        self.assertIn("Historical review decisions are separate from the current lead pool", source)
        self.assertIn("Sales Workspaces", layout)
        self.assertIn("['Leads', '/leads']", layout)
        self.assertNotIn("['Leads', '/campaigns']", layout)
        self.assertNotIn("['Employees', '/employees']", layout)
        self.assertNotIn("['Schedules', '/scheduler']", layout)
        self.assertIn("DailyReportPanel", source)
        self.assertIn("isLeadResearchEmployee", source)
        self.assertIn("isEmailOutreachEmployee", source)
        self.assertIn("isReportingEmployee", source)
        self.assertIn("leadSourceCampaignFor", source)
        self.assertIn("leadSourceCampaignId={leadSourceCampaign?.id}", source)
        self.assertIn("Sales Workspace Control Center", source)
        self.assertIn("Company &gt; Sales Workspace &gt; AI Sales Employee OS", source)
        self.assertIn("Current blocker:", source)
        self.assertIn("<details", source)
        self.assertIn("Advanced", source)
        for label in ["Goal", "Leads", "Lead Source", "Email Sending Workflow", "Replies and Meetings", "Calling", "Daily Report", "Hermes Sync and Job IDs", "Raw Schedules", "Raw Employees", "Model Policy"]:
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

        self.assertIn("function errorPayload", source)
        self.assertIn("parsed?.detail?.error", source)
        self.assertIn("console.error('API request failed'", source)
        self.assertIn("throw new ApiError(", source)

    def test_daily_report_route_uses_jobs_json_executor_with_receipt_evidence(self):
        source = (ROOT / "backend" / "app" / "api" / "routes.py").read_text(encoding="utf-8")

        self.assertIn("execute_scheduled_jobs_json_task", source)
        self.assertIn("'hermes_job_id': '5881b72113ce'", source)
        self.assertIn("'provider_message_id': delivery_job.provider_message_id", source)
        self.assertIn("'Daily report delivered with provider receipt evidence.'", source)


if __name__ == "__main__":
    unittest.main()


class LeadsPageContractTests(unittest.TestCase):
    def test_leads_route_exists_and_explains_current_unique_pool(self):
        source = (ROOT / "frontend" / "app" / "leads" / "page.tsx").read_text()
        self.assertIn("Lead Workspace", source)
        self.assertIn("Current unique lead pools", source)
        self.assertIn("Assumed addresses stay visible", source)

    def test_outreach_panel_displays_quality_gate(self):
        source = (ROOT / "frontend" / "components" / "OutreachControlsPanel.tsx").read_text()
        self.assertIn("email_confidence", source)
        self.assertIn("approval_eligible", source)
        self.assertIn("Needs public or verified email evidence before approval", source)


class SalesCampaignWizardContractTests(unittest.TestCase):
    def test_sales_campaign_wizard_is_primary_flow(self):
        source = read_frontend("components/SalesCampaignWizard.tsx")
        campaigns = read_frontend("app/campaigns/page.tsx")
        self.assertIn("data-voryx-sales-campaign-wizard", source)
        self.assertIn("B2B sales workspace setup", source)
        self.assertIn("Calling: not connected", source)
        self.assertIn("SMS/Text: not connected", source)
        self.assertIn("Social outreach: not connected", source)
        self.assertIn("WhatsApp: not connected", source)
        self.assertIn("Advanced: raw campaign records", campaigns)
        self.assertIn("SalesCampaignWizard", campaigns)


class AISalesOSMilestoneContractTests(unittest.TestCase):
    def test_business_navigation_moves_technical_pages_to_advanced(self):
        layout = read_frontend("app/layout.tsx")
        for label in ["Sales Workspaces", "Leads", "Outreach", "Replies", "Meetings", "Reports", "Settings", "Advanced"]:
            self.assertIn(label, layout)
        self.assertNotIn("['Jobs', '/jobs']", layout)
        self.assertNotIn("['Logs', '/reports']", layout)
        self.assertNotIn("['Health', '/system']", layout)
        advanced = read_frontend("app/advanced/page.tsx")
        for label in ["Raw Campaign Records", "Raw Employees", "Schedules", "Jobs", "Logs", "Health", "Model Policy", "Data Files", "Hermes Sync"]:
            self.assertIn(label, advanced)

    def test_bibs_source_config_panel_and_api_contract_exist(self):
        panel = read_frontend("components/BibsLeadSourcePanel.tsx")
        campaigns = read_frontend("app/campaigns/page.tsx")
        wizard = read_frontend("components/SalesCampaignWizard.tsx")
        routes = (ROOT / "backend" / "app" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn("data-voryx-bibs-source-config", panel)
        self.assertIn("Save source config", panel)
        self.assertIn("Test source config", panel)
        self.assertIn("Generate leads from internet", panel)
        self.assertIn("AI Internet Research - generate leads from internet", panel)
        self.assertIn("Optional reference URLs", panel)
        self.assertIn("source_type: 'ai_internet_research'", panel)
        self.assertIn("lead_source_type: 'ai_internet_research'", wizard)
        self.assertIn("Optional upload CSV path", wizard)
        self.assertIn("internet_research_provider_not_configured", wizard)
        self.assertNotIn("Generate new leads from configured internet/source-backed research", wizard)
        self.assertIn("BibsLeadSourcePanel", campaigns)
        self.assertIn("bibs_real_lead_source_config.json", routes)
        self.assertIn("/companies/{company_id}/bibs-lead-source-config", routes)
        self.assertIn("generate_ai_internet_source_plan", routes)
        self.assertIn("internet_research_provider_not_configured", routes)
        self.assertIn("hermes_native_browser_status", routes)
        self.assertIn("AI Internet Research provider:", panel)
        self.assertIn("Hermes Native Browser", panel)
        self.assertIn("Browser launch:", panel)
        self.assertIn("Internet access:", panel)
        self.assertNotIn("real_source_not_configured", panel)

    def test_count_diagnostics_and_future_channels_are_visible(self):
        outreach = read_frontend("components/OutreachControlsPanel.tsx")
        campaigns = read_frontend("app/campaigns/page.tsx")
        self.assertIn("data-voryx-count-diagnostics", outreach)
        self.assertIn("Count source: Canonical Lead Pool", outreach)
        for label in ["SMS/Text", "Social Outreach", "WhatsApp"]:
            self.assertIn(label, campaigns)
