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
        self.assertIn("type=\"button\" data-voryx-crud-save", source)
        self.assertIn("data-voryx-action-path={`${path}/${item.id}/dry-run`}", source)
        self.assertIn("data-voryx-action-path={`${path}/${item.id}/test-run`}", source)

    def test_action_runtime_prevents_navigation_and_calls_backend_for_crud(self):
        source = read_frontend("public/voryx-action-runtime.js")

        self.assertIn("event.preventDefault();", source)
        self.assertIn("event.stopImmediatePropagation?.();", source)
        self.assertIn("button.closest('[data-voryx-crud-save]')", source)
        self.assertIn("await apiPost(editingId ? `${path}/${editingId}` : path", source)
        self.assertIn("method: editingId ? 'PUT' : 'POST'", source)
        self.assertIn("await apiPost(`${path}/${item.id}`, { method: 'DELETE' })", source)
        self.assertIn("console.error(`Dashboard ${label} failed`", source)

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

    def test_crud_controls_have_explicit_labels(self):
        source = read_frontend("components/CrudPage.tsx")

        self.assertIn("htmlFor={fieldId}", source)
        self.assertIn("id={fieldId}", source)
        self.assertIn("<fieldset", source)
        self.assertIn("htmlFor={dayId}", source)
        self.assertIn("htmlFor={`${fieldId}-start`}", source)

    def test_action_runtime_localizes_server_rendered_times(self):
        runtime = read_frontend("public/voryx-action-runtime.js")
        sync_status = read_frontend("components/SyncStatus.tsx")

        self.assertIn("data-voryx-sync-last", sync_status)
        self.assertIn("const localizeStaticTimes", runtime)
        self.assertIn("time[datetime]", runtime)
        self.assertIn("[data-voryx-sync-last]", runtime)


if __name__ == "__main__":
    unittest.main()
