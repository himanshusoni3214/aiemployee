import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'ops' / 'retell_successor_cutover.py'
SPEC = importlib.util.spec_from_file_location('retell_successor_cutover', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class RetellSuccessorCutoverTests(unittest.TestCase):
    def test_update_env_preserves_unrelated_values_and_replaces_retell_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env.production'
            path.write_text(
                'DATABASE_URL=postgresql://example\n'
                'RETELL_AGENT_ID=legacy\n'
                'RETELL_PERMANENT_AGENT_ID=legacy\n'
                'RETELL_AGENT_VERSION=3\n'
            )
            MODULE.update_env(path, {
                'RETELL_LEGACY_AGENT_ID': 'legacy',
                'RETELL_AGENT_ID': 'successor',
                'RETELL_PERMANENT_AGENT_ID': 'successor',
                'RETELL_AGENT_VERSION': '0',
            })
            _, values = MODULE.parse_env(path)
            self.assertEqual(values['DATABASE_URL'], 'postgresql://example')
            self.assertEqual(values['RETELL_LEGACY_AGENT_ID'], 'legacy')
            self.assertEqual(values['RETELL_AGENT_ID'], 'successor')
            self.assertEqual(values['RETELL_PERMANENT_AGENT_ID'], 'successor')
            self.assertEqual(values['RETELL_AGENT_VERSION'], '0')

    def test_exact_assignment_rejects_both_agents_or_wrong_version(self):
        legacy = {'agent_id': 'legacy', 'agent_version': 3, 'weight': 1}
        successor = {'agent_id': 'successor', 'agent_version': 0, 'weight': 1}
        self.assertTrue(MODULE.exact_assignment({'outbound_agents': [successor]}, 'successor', 0))
        self.assertFalse(MODULE.exact_assignment({'outbound_agents': [legacy, successor]}, 'successor', 0))
        self.assertFalse(MODULE.exact_assignment({'outbound_agents': [successor]}, 'successor', 1))

    def test_env_rollback_removes_new_legacy_key_when_old_value_was_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env.production'
            path.write_text(
                'RETELL_AGENT_ID=successor\n'
                'RETELL_PERMANENT_AGENT_ID=successor\n'
                'RETELL_LEGACY_AGENT_ID=legacy\n'
                'RETELL_AGENT_VERSION=0\n'
            )
            MODULE.update_env(path, {
                'RETELL_AGENT_ID': 'legacy',
                'RETELL_PERMANENT_AGENT_ID': 'legacy',
                'RETELL_LEGACY_AGENT_ID': '',
                'RETELL_AGENT_VERSION': '3',
            })
            _, values = MODULE.parse_env(path)
            self.assertNotIn('RETELL_LEGACY_AGENT_ID', values)
            self.assertEqual(values['RETELL_AGENT_ID'], 'legacy')
            self.assertEqual(values['RETELL_AGENT_VERSION'], '3')


if __name__ == '__main__':
    unittest.main()
