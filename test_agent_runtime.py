import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import offer_compass_runtime


class AgentRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="offer-agent-test-")
        self.db_path = Path(self.folder.name) / "agent.sqlite3"
        offer_compass_runtime.initialize(self.db_path)
        self.runtime = offer_compass_runtime.AgentRuntime(self.db_path)
        self.body = {
            "jd": "岗位要求：\n1. 熟练 SQL 并能够进行数据分析\n2. 具备独立 A/B 测试经验",
            "goal": "判断是否值得投递",
            "lines": ["项目经历", "使用 SQL 分析用户留存，完成指标定义和结果复核。"],
            "levels": {"SQL": 2},
        }

    def tearDown(self):
        self.folder.cleanup()

    def test_agent_waits_for_one_answer_then_resumes(self):
        first = self.runtime.run(self.body)
        self.assertEqual(first["stage"], "clarification_required")
        self.assertEqual(first["status"], "waiting")
        self.assertEqual(first["next_action"], "wait_for_user")
        self.assertIn("A/B", first["requirement"])
        self.assertEqual([item["tool"] for item in first["tool_trace"]], [
            "get_candidate_profile", "parse_job_description", "retrieve_resume_evidence",
            "assess_job_fit", "create_clarifying_question"])

        second = self.runtime.run(dict(self.body, run_id=first["run_id"],
                                       answer="在课程项目中负责实验设计与结果分析，无线上工作经验。"))
        self.assertEqual(second["stage"], "fit_assessed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["next_action"], "review_fit_assessment")
        self.assertEqual(second["verdict"], "apply")
        self.assertEqual(len(second["tool_trace"]), 9)
        self.assertEqual(self.runtime.get(first["run_id"])["revision"], 2)

        with sqlite3.connect(str(self.db_path)) as db:
            run = db.execute("SELECT stage, status, revision FROM agent_runs").fetchone()
            calls = db.execute("SELECT tool_name, status, input_json, output_json FROM agent_tool_calls ORDER BY sequence").fetchall()
        self.assertEqual(run, ("fit_assessed", "completed", 2))
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(item[1] == "completed" for item in calls))
        json.loads(calls[0][2])
        json.loads(calls[0][3])

    def test_changed_input_cannot_reuse_run(self):
        first = self.runtime.run(self.body)
        with self.assertRaises(ValueError):
            self.runtime.run(dict(self.body, run_id=first["run_id"], jd="不同的 JD"))


if __name__ == "__main__":
    unittest.main()
