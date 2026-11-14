"""Run the deterministic Offer Compass agent evaluation suite and write a baseline report."""

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import offer_compass_runtime
import offer_compass_tools


ROOT = Path(__file__).resolve().parent


def check_expected(result, expected):
    errors = []
    for field in ("stage", "verdict", "accepted_count"):
        if field in expected and result.get(field) != expected[field]:
            errors.append("{} expected {!r}, got {!r}".format(field, expected[field], result.get(field)))
    if len(result.get("patches", [])) < expected.get("min_patches", 0):
        errors.append("not enough verified patches")
    if len(result.get("patches", [])) > expected.get("max_patches", 999):
        errors.append("too many verified patches")
    if expected.get("rejected_count") is not None:
        rejected_count = result.get("rejected_count")
        if rejected_count is None:
            rejected_count = len(result.get("rejected_patches", []))
        if rejected_count != expected["rejected_count"]:
            errors.append("rejected_count expected {!r}, got {!r}".format(expected["rejected_count"], rejected_count))
    if expected.get("requirement_contains") not in (None, "") and expected["requirement_contains"] not in (result.get("requirement") or ""):
        errors.append("clarification targeted the wrong requirement")
    if expected.get("gap_contains") and not any(expected["gap_contains"] in gap for gap in result.get("gaps", [])):
        errors.append("expected gap was not retained")
    if expected.get("preserve_number"):
        token = expected["preserve_number"]
        if not result.get("patches") or any(token not in patch["before"] or token not in patch["after"] for patch in result["patches"]):
            errors.append("numeric evidence was not preserved")
    return errors


def run_case(case):
    if case["type"] == "verifier":
        profile = offer_compass_tools.get_candidate_profile(case["lines"], {})
        patch = dict(case["patch"], patch_id="patch_eval", reason="evaluation",
                     requirement_id="req_eval", evidence_ids=["resume_line_0001"])
        result = offer_compass_tools.verify_resume_patches([patch], profile)
        return result, check_expected(result, case["expected"]), True, []
    with tempfile.TemporaryDirectory(prefix="offer-eval-") as folder:
        db_path = Path(folder) / "eval.sqlite3"
        offer_compass_runtime.initialize(db_path)
        runtime = offer_compass_runtime.AgentRuntime(db_path)
        body = {"jd": case["jd"], "goal": "判断是否值得投递",
                "lines": case["lines"], "levels": case.get("levels", {})}
        result = runtime.run(body)
        if case.get("followup"):
            result = runtime.run(dict(body, run_id=result["run_id"], **case["followup"]))
        citation_valid = all(not item.get("line") or
                             (0 < item["line"] <= len(case["lines"]) and item.get("text") == case["lines"][item["line"] - 1])
                             for item in result.get("evidence", []))
        tool_statuses = [item["status"] for item in result.get("tool_trace", [])]
        errors = check_expected(result, case["expected"])
        if not citation_valid:
            errors.append("evidence citation does not match the source line")
        return result, errors, citation_valid, tool_statuses


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    rows, citation_checks, tool_statuses = [], [], []
    for case in cases:
        result, errors, citation_valid, statuses = run_case(case)
        citation_checks.append(citation_valid)
        tool_statuses.extend(statuses)
        rows.append({"id": case["id"], "type": case["type"], "passed": not errors,
                     "stage": result.get("stage"), "verdict": result.get("verdict"), "errors": errors})
    total = len(rows)
    passed = sum(row["passed"] for row in rows)
    expected_verdicts = [(row, case["expected"]["verdict"]) for row, case in zip(rows, cases)
                         if "verdict" in case["expected"]]
    metrics = {
        "case_pass_rate": passed / total if total else 0,
        "citation_validity": sum(citation_checks) / len(citation_checks) if citation_checks else 1,
        "verdict_accuracy": sum(row["verdict"] == expected for row, expected in expected_verdicts) /
                            len(expected_verdicts) if expected_verdicts else 1,
        "tool_success_rate": sum(status == "completed" for status in tool_statuses) /
                             len(tool_statuses) if tool_statuses else 1,
    }
    report = {"agent_version": "4.5", "generated_at": datetime.now(timezone.utc).isoformat(),
              "total_cases": total, "passed_cases": passed, "metrics": metrics, "cases": rows}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "v4.5-baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Agent V4.5 评测基线", "", "- 用例：{}".format(total), "- 通过：{}".format(passed),
             "- 用例通过率：{:.1%}".format(metrics["case_pass_rate"]),
             "- 证据引用有效率：{:.1%}".format(metrics["citation_validity"]),
             "- 判断准确率：{:.1%}".format(metrics["verdict_accuracy"]),
             "- 工具成功率：{:.1%}".format(metrics["tool_success_rate"]), "", "## 用例", ""]
    lines.extend("- {} `{}`{}".format("✓" if row["passed"] else "✗", row["id"],
                                     "" if row["passed"] else "：" + "；".join(row["errors"])) for row in rows)
    (args.output / "v4.5-baseline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "total": total, "metrics": metrics}, ensure_ascii=False))
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

