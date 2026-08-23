"""Explicitly gated smoke tests for a non-production Rahtal instance."""

from __future__ import annotations

import os
import unittest
from datetime import datetime

from rahtal_assistant.client import RahtalClient
from rahtal_assistant.config import RahtalSettings
from rahtal_assistant.services import AuthService, PerformanceService, TaskService


READ_ONLY_ENABLED = os.getenv("RAHTAL_SMOKE_READ_ONLY", "false").lower() == "true"
WRITE_ENABLED = os.getenv("RAHTAL_SMOKE_ALLOW_WRITE", "false").lower() == "true"
ENVIRONMENT = os.getenv("RAHTAL_SMOKE_ENVIRONMENT", "").lower()
NON_PRODUCTION = ENVIRONMENT in {"test", "development", "dev", "staging", "stage"}


@unittest.skipUnless(READ_ONLY_ENABLED, "Set RAHTAL_SMOKE_READ_ONLY=true to opt in.")
class RahtalSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = RahtalClient(RahtalSettings.from_env())
        cls.auth = AuthService(cls.client)
        cls.tasks = TaskService(cls.client)
        cls.performance = PerformanceService(cls.client, cls.auth, cls.tasks)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_whoami_and_current_tasks(self) -> None:
        self.assertIsInstance(self.auth.whoami()["id"], int)
        result = self.tasks.list_my_tasks(status="active", page_size=5)
        self.assertIn("items", result)

    @unittest.skipUnless(
        WRITE_ENABLED and NON_PRODUCTION,
        "Write smoke requires RAHTAL_SMOKE_ALLOW_WRITE=true and a non-production environment.",
    )
    def test_zz_explicit_non_production_write(self) -> None:
        required = {
            "RAHTAL_SMOKE_TASK_ID": os.getenv("RAHTAL_SMOKE_TASK_ID"),
            "RAHTAL_SMOKE_TITLE_ID": os.getenv("RAHTAL_SMOKE_TITLE_ID"),
            "RAHTAL_SMOKE_START_TIME": os.getenv("RAHTAL_SMOKE_START_TIME"),
            "RAHTAL_SMOKE_END_TIME": os.getenv("RAHTAL_SMOKE_END_TIME"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            self.fail("Missing explicit write-smoke settings: " + ", ".join(missing))

        result = self.performance.create_performance(
            task_id=int(required["RAHTAL_SMOKE_TASK_ID"]),
            title_id=int(required["RAHTAL_SMOKE_TITLE_ID"]),
            summary=f"Codex MCP smoke test {datetime.now().isoformat(timespec='seconds')}",
            date="today",
            start_time=required["RAHTAL_SMOKE_START_TIME"],
            end_time=required["RAHTAL_SMOKE_END_TIME"],
        )
        self.assertIsInstance(result["id"], int)


if __name__ == "__main__":
    unittest.main()
