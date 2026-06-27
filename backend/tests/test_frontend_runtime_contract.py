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

    def test_action_runtime_localizes_server_rendered_times(self):
        runtime = read_frontend("public/voryx-action-runtime.js")
        sync_status = read_frontend("components/SyncStatus.tsx")

        self.assertIn("data-voryx-sync-last", sync_status)
        self.assertIn("const localizeStaticTimes", runtime)
        self.assertIn("time[datetime]", runtime)
        self.assertIn("[data-voryx-sync-last]", runtime)


if __name__ == "__main__":
    unittest.main()
