import csv
import tempfile
import unittest
from pathlib import Path

from app.services.daily_report import day_window, generate_daily_report


class DailyReportTests(unittest.TestCase):
    def make_root(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "home" / "leads").mkdir(parents=True)
        self.addCleanup(temp.cleanup)
        return root

    def write_csv(self, path: Path, rows: list[dict[str, str]]):
        path.parent.mkdir(parents=True, exist_ok=True)
        headers = sorted({key for row in rows for key in row})
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def test_toronto_daylight_saving_boundaries(self):
        winter = day_window("2026-01-15")
        summer = day_window("2026-06-26")
        self.assertEqual(winter.utc_start.hour, 5)
        self.assertEqual(summer.utc_start.hour, 4)

    def test_missing_lead_timestamps_are_unavailable_not_today_count(self):
        root = self.make_root()
        self.write_csv(root / "home" / "leads" / "leads_verified.csv", [{"Public Email": "buyer@example.com"}])
        report = generate_daily_report("2026-06-26", str(root))
        metric = report["metrics"]["leads_created_today"]
        self.assertFalse(metric["verified"])
        self.assertIn("Unavailable", metric["value"])

    def test_legacy_sent_without_message_id_is_not_confirmed_sent(self):
        root = self.make_root()
        self.write_csv(root / "home" / "leads" / "leads_verified.csv", [{"Public Email": "buyer@example.com"}])
        self.write_csv(root / "outreach_log.csv", [{"recipient": "buyer@example.com", "status": "sent", "timestamp": "2026-06-26 10:15:00"}])
        report = generate_daily_report("2026-06-26", str(root))
        self.assertEqual(report["metrics"]["emails_confirmed_sent_today"]["value"], 0)
        self.assertIn("lack durable message_id", "\n".join(report["errors_and_blockers"]))

    def test_structured_sent_with_message_id_counts_once(self):
        root = self.make_root()
        self.write_csv(root / "home" / "leads" / "leads_verified.csv", [{"Public Email": "buyer@example.com"}])
        (root / "outreach_events.jsonl").write_text(
            '{"event_id":"evt-1","campaign_id":"campaign-brew-it-by-sash-outreach","recipient":"buyer@example.com","status":"sent","sent_at":"2026-06-26T15:00:00Z","message_id":"msg-1","dry_run":false}\n'
            '{"event_id":"evt-1","campaign_id":"campaign-brew-it-by-sash-outreach","recipient":"buyer@example.com","status":"sent","sent_at":"2026-06-26T15:00:00Z","message_id":"msg-1","dry_run":false}\n',
            encoding="utf-8",
        )
        report = generate_daily_report("2026-06-26", str(root))
        self.assertEqual(report["metrics"]["emails_confirmed_sent_today"]["value"], 1)


if __name__ == "__main__":
    unittest.main()
