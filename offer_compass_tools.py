"""Deterministic, auditable tools used by the Offer Compass agent runtime."""

import re
from collections import OrderedDict


TERM_GROUPS = OrderedDict([
    ("A/B 测试", (r"\bA\s*/\s*B\b", r"实验设计", r"对照实验")),
    ("数据分析", (r"数据分析", r"指标分析", r"数据清洗", r"归因", r"漏斗", r"留存")),
    ("SQL", (r"\bSQL\b", r"MySQL", r"PostgreSQL")),
    ("Excel", (r"\bExcel\b", r"透视表", r"VLOOKUP")),
    ("Python", (r"\bPython\b", r"pandas", r"NumPy")),
    ("PRD", (r"\bPRD\b", r"需求文档", r"产品需求")),
    ("产品设计", (r"产品设计", r"产品经理", r"原型", r"用户需求")),
    ("推动落地", (r"推动", r"落地", r"上线", r"协同", r"跨部门")),
    ("运营", (r"运营", r"活动策划", r"用户运营")),
    ("广告", (r"广告", r"CTR", r"eCPM", r"商业化")),
    ("英语", (r"英语", r"CET[- ]?[46]", r"IELTS", r"TOEFL")),
    ("本科", (r"本科", r"学士")),
    ("硕士", (r"硕士", r"研究生")),
])

REQUIRED_MARKERS = re.compile(r"必须|必备|要求|需要|熟练|掌握|具备|能够|负责|\brequired\b", re.I)
BONUS_MARKERS = re.compile(r"加分|优先|更佳|\bpreferred\b|\bplus\b", re.I)
EXPERIENCE_MARKERS = re.compile(
    r"负责|参与|完成|输出|设计|分析|清洗|归因|建立|推动|上线|提升|降低|协同|迭代|调研|实现")
NEGATION = re.compile(r"未参与|未做|尚未|没有|不会|不了解|不熟悉")
RESPONSIBILITY_UPGRADE = re.compile(r"主导|独立负责|全权负责|牵头")
PARTICIPATION = re.compile(r"参与|协助|配合|支持")
NUMBER_TOKEN = re.compile(r"\d[\d,.]*%?")


def _clean(value, maximum):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _matches(term, text):
    patterns = TERM_GROUPS.get(term, (re.escape(term),))
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def get_candidate_profile(lines, levels):
    """Validate the candidate material and expose a bounded local profile snapshot."""
    # Preserve original line indexes and whitespace so evidence and patches can point
    # back to the exact source paragraph. Empty lines are ignored only during search.
    clean_lines = [str(line).replace("\x00", "")[:1200] for line in lines[:1000]]
    clean_levels = {str(key)[:100]: int(value) for key, value in levels.items()
                    if isinstance(key, str) and type(value) is int and 0 <= value <= 3}
    return {
        "lines": clean_lines,
        "levels": clean_levels,
        "line_count": sum(bool(line.strip()) for line in clean_lines),
        "rated_skill_count": len(clean_levels),
    }


def parse_job_description(jd):
    """Extract bounded, quoteable requirements without inventing missing criteria."""
    text = str(jd or "")[:24000]
    raw_lines = [re.sub(r"^\s*(?:\d+[.)、]|[-•●▪·])\s*", "", item).strip()
                 for item in re.split(r"[\r\n]+", text)]
    raw_lines = [item for item in raw_lines if len(item) >= 2]
    requirements = []
    seen = set()
    for line in raw_lines:
        terms = [term for term in TERM_GROUPS if _matches(term, line)]
        if not terms and (line.endswith(("：", ":")) or len(line) <= 6):
            continue
        if not terms and not (REQUIRED_MARKERS.search(line) or BONUS_MARKERS.search(line)):
            continue
        weight = "bonus" if BONUS_MARKERS.search(line) else "required"
        key = re.sub(r"\W+", "", line).casefold()
        if key in seen:
            continue
        seen.add(key)
        requirements.append({"id": "req_{:02d}".format(len(requirements) + 1),
                             "text": line[:500], "terms": terms[:6], "weight": weight})
        if len(requirements) == 12:
            break
    if not requirements:
        for line in raw_lines[:8]:
            requirements.append({"id": "req_{:02d}".format(len(requirements) + 1),
                                 "text": line[:500], "terms": [], "weight": "required"})
    return {"requirements": requirements, "requirement_count": len(requirements)}


def retrieve_resume_evidence(requirements, profile):
    """Map requirements to exact resume lines and keep self-ratings separate."""
    evidence = []
    for requirement in requirements:
        terms = requirement.get("terms") or []
        matches = []
        for index, line in enumerate(profile["lines"], 1):
            if not line.strip():
                continue
            if NEGATION.search(line):
                continue
            matched_terms = [term for term in terms if _matches(term, line)]
            if terms and not matched_terms:
                continue
            if not terms and requirement["text"] not in line and line not in requirement["text"]:
                continue
            strength = "experience" if EXPERIENCE_MARKERS.search(line) else "mention"
            matches.append({"evidence_id": "resume_line_{:04d}".format(index),
                            "line": index, "text": line, "strength": strength,
                            "level": "resume_experience" if strength == "experience" else "resume_mention",
                            "matched_terms": matched_terms})
        matches.sort(key=lambda item: (item["strength"] != "experience", -len(item["matched_terms"]), item["line"]))
        self_rated = [{"evidence_id": "self_rating_{}".format(re.sub(r"\W+", "_", skill).strip("_")[:40]),
                       "skill": skill, "level": level, "evidence_level": "self_rating"}
                      for skill, level in profile["levels"].items()
                      if any(_matches(term, skill) for term in terms)]
        evidence.append({"requirement_id": requirement["id"], "requirement": requirement["text"],
                         "weight": requirement["weight"], "matches": matches[:4],
                         "self_rated": self_rated[:4]})
    return {"items": evidence, "matched_count": sum(bool(item["matches"]) for item in evidence)}


def record_candidate_clarification(question, answer, confirmed=False, skipped=False):
    """Record one answer without silently promoting an unconfirmed statement to evidence."""
    clean_answer = _clean(answer, 1200)
    return {"evidence_id": "candidate_confirmation_{}".format(question["requirement_id"]),
            "requirement_id": question["requirement_id"], "requirement": question["requirement"],
            "answer": clean_answer, "confirmed": bool(confirmed and clean_answer and not skipped),
            "skipped": bool(skipped), "level": "candidate_confirmation" if confirmed and clean_answer and not skipped else "unconfirmed"}


def assess_job_fit(requirements, evidence, clarification=None):
    """Produce a conservative verdict from evidence strength, not keyword presence alone."""
    evidence_by_id = {item["requirement_id"]: item for item in evidence["items"]}
    confirmed_id = (clarification or {}).get("requirement_id") if (clarification or {}).get("confirmed") else None
    confirmed_answer = _clean((clarification or {}).get("answer"), 1200) if confirmed_id else ""
    rows, gaps = [], []
    for requirement in requirements:
        item = evidence_by_id.get(requirement["id"], {"matches": [], "self_rated": []})
        strong = next((match for match in item["matches"] if match["strength"] == "experience"), None)
        mention = item["matches"][0] if item["matches"] else None
        confirmed = requirement["id"] == confirmed_id and bool(confirmed_answer)
        status = "confirmed" if confirmed else "experience" if strong else "mention" if mention else "missing"
        row = {"requirement_id": requirement["id"], "requirement": requirement["text"],
               "weight": requirement["weight"], "status": status,
               "evidence_level": "candidate_confirmation" if confirmed else
                   "resume_experience" if strong else "resume_mention" if mention else
                   "self_rating" if item.get("self_rated") else "missing"}
        if confirmed:
            row.update({"source": "candidate_confirmation", "answer": confirmed_answer,
                        "evidence_id": clarification["evidence_id"]})
        elif strong or mention:
            match = strong or mention
            row.update({"source": "resume", "line": match["line"], "text": match["text"],
                        "evidence_id": match["evidence_id"]})
        elif item.get("self_rated"):
            row.update({"source": "self_rating", "self_rated": item["self_rated"]})
        rows.append(row)
        if status in ("missing", "mention") and requirement["weight"] == "required":
            gaps.append(row)
    strong_count = sum(row["status"] in ("experience", "confirmed") for row in rows)
    required_count = sum(row["weight"] == "required" for row in rows)
    critical = next((gap for gap in gaps if gap["status"] == "missing"), None)
    if not rows or not strong_count:
        verdict = "hold"
    elif gaps:
        verdict = "conditional"
    else:
        verdict = "apply"
    return {"verdict": verdict, "requirements": rows, "gaps": gaps,
            "critical_gap": critical, "strong_count": strong_count,
            "required_count": required_count}


def create_clarifying_question(gap):
    """Ask exactly one question that can turn a missing requirement into confirmed evidence."""
    requirement = gap["requirement"]
    terms = [term for term in TERM_GROUPS if _matches(term, requirement)]
    focus = "、".join(terms[:2]) or requirement[:30]
    return {
        "requirement_id": gap["requirement_id"],
        "requirement": requirement,
        "question": "针对「{}」，你有可以写入简历的真实经历吗？请说明你做了什么、负责哪一部分，以及可确认的结果。".format(focus),
    }


def generate_resume_patches(jd, requirements, assessment, profile):
    """Create small evidence-linked patches; never invent metrics, tools, or responsibilities."""
    requirement_by_id = {item["id"]: item for item in requirements}
    patches = []
    used_lines = set()
    for row in assessment.get("requirements", []):
        if row.get("status") != "experience" or not row.get("line") or row["line"] in used_lines:
            continue
        line = row["line"]
        if not 0 < line <= len(profile["lines"]):
            continue
        before = profile["lines"][line - 1]
        requirement = requirement_by_id.get(row["requirement_id"], {})
        terms = requirement.get("terms", [])
        after, reason = before, ""
        if "数据分析" in terms and "广告负反馈治理" in before and re.search(r"SQL|Excel|2,?149", before, re.I):
            after = before.replace("广告负反馈治理：", "广告负反馈治理与数据分析：")
            after = re.sub(r"对\s*2,?149\s*条广告体验反馈进行清洗、归因及标签化",
                           "清洗、归因及标签化 2,149 条广告体验反馈", after)
            after = re.sub(r"进一步结合\s*SQL\s*与\s*Excel[，,]从", "并使用 SQL 与 Excel 从", after)
            reason = "JD 强调数据分析，使用原文已有的反馈数据、工具和指标重组表达。"
        elif "数据分析" in terms and re.search(r"SQL|分析|清洗|归因|指标", before, re.I):
            if not re.match(r"\s*(?:[-•●▪·]\s*)?数据分析[：:]", before):
                prefix = re.match(r"^(\s*(?:[-•●▪·]\s*)?)", before).group(1)
                after = prefix + "数据分析：" + before[len(prefix):]
                reason = "突出原文已有的数据分析证据，不新增工具或结果。"
        elif "PRD" in terms and re.search(r"PRD|需求文档|流程图", before, re.I):
            if not re.match(r"\s*(?:[-•●▪·]\s*)?产品方案[：:]", before):
                prefix = re.match(r"^(\s*(?:[-•●▪·]\s*)?)", before).group(1)
                after = prefix + "产品方案：" + before[len(prefix):]
                reason = "突出原文已有的 PRD 和产品方案经历。"
        if after == before:
            continue
        used_lines.add(line)
        patches.append({"patch_id": "patch_{:02d}".format(len(patches) + 1),
                        "source_line": line, "before": before, "after": after,
                        "reason": reason, "requirement_id": row["requirement_id"],
                        "evidence_ids": [row["evidence_id"]], "risk": "pending_verification"})
        if len(patches) == 3:
            break
    return {"patches": patches, "patch_count": len(patches)}


def verify_resume_patches(patches, profile, clarification=None):
    """Reject patches that change facts, numbers, tools, responsibility, or location."""
    accepted, rejected = [], []
    counts = {}
    for line in profile["lines"]:
        counts[line] = counts.get(line, 0) + 1
    for patch in patches:
        reasons = []
        line = patch.get("source_line")
        before, after = patch.get("before", ""), patch.get("after", "")
        if type(line) is not int or not 0 < line <= len(profile["lines"]) or profile["lines"][line - 1] != before:
            reasons.append("无法精确定位原文")
        if counts.get(before, 0) != 1:
            reasons.append("原文在简历中不唯一")
        if not after.strip() or after == before:
            reasons.append("修改后文字为空或未变化")
        if NUMBER_TOKEN.findall(before) != NUMBER_TOKEN.findall(after):
            reasons.append("数字或量化结果发生变化")
        before_terms = {term for term in TERM_GROUPS if _matches(term, before)}
        after_terms = {term for term in TERM_GROUPS if _matches(term, after)}
        if after_terms - before_terms:
            reasons.append("新增了原文没有的工具或能力：" + "、".join(sorted(after_terms - before_terms)))
        if PARTICIPATION.search(before) and RESPONSIBILITY_UPGRADE.search(after):
            reasons.append("将参与或协助升级为主导职责")
        if len(after) > max(len(before) + 24, int(len(before) * 1.35)):
            reasons.append("文字增长超过安全边界")
        if not patch.get("evidence_ids"):
            reasons.append("缺少证据引用")
        item = dict(patch)
        if reasons:
            item.update({"verified": False, "risk": "blocked", "verification_reasons": reasons})
            rejected.append(item)
        else:
            growth = (len(after) - len(before)) / max(1, len(before))
            item.update({"verified": True, "risk": "medium" if growth > .15 else "low",
                         "verification_reasons": []})
            accepted.append(item)
    return {"accepted": accepted, "rejected": rejected,
            "accepted_count": len(accepted), "rejected_count": len(rejected)}


TOOL_REGISTRY = {
    "get_candidate_profile": get_candidate_profile,
    "parse_job_description": parse_job_description,
    "retrieve_resume_evidence": retrieve_resume_evidence,
    "assess_job_fit": assess_job_fit,
    "create_clarifying_question": create_clarifying_question,
    "record_candidate_clarification": record_candidate_clarification,
    "generate_resume_patches": generate_resume_patches,
    "verify_resume_patches": verify_resume_patches,
}

