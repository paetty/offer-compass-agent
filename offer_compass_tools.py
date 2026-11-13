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


def _clean(value, maximum):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _matches(term, text):
    patterns = TERM_GROUPS.get(term, (re.escape(term),))
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def get_candidate_profile(lines, levels):
    """Validate the candidate material and expose a bounded local profile snapshot."""
    clean_lines = [_clean(line, 1200) for line in lines if _clean(line, 1200)][:1000]
    clean_levels = {str(key)[:100]: int(value) for key, value in levels.items()
                    if isinstance(key, str) and type(value) is int and 0 <= value <= 3}
    return {
        "lines": clean_lines,
        "levels": clean_levels,
        "line_count": len(clean_lines),
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
            if NEGATION.search(line):
                continue
            matched_terms = [term for term in terms if _matches(term, line)]
            if terms and not matched_terms:
                continue
            if not terms and requirement["text"] not in line and line not in requirement["text"]:
                continue
            strength = "experience" if EXPERIENCE_MARKERS.search(line) else "mention"
            matches.append({"line": index, "text": line, "strength": strength,
                            "matched_terms": matched_terms})
        matches.sort(key=lambda item: (item["strength"] != "experience", -len(item["matched_terms"]), item["line"]))
        self_rated = [{"skill": skill, "level": level} for skill, level in profile["levels"].items()
                      if any(_matches(term, skill) for term in terms)]
        evidence.append({"requirement_id": requirement["id"], "requirement": requirement["text"],
                         "weight": requirement["weight"], "matches": matches[:4],
                         "self_rated": self_rated[:4]})
    return {"items": evidence, "matched_count": sum(bool(item["matches"]) for item in evidence)}


def assess_job_fit(requirements, evidence, clarification=None):
    """Produce a conservative verdict from evidence strength, not keyword presence alone."""
    evidence_by_id = {item["requirement_id"]: item for item in evidence["items"]}
    confirmed_id = (clarification or {}).get("requirement_id")
    confirmed_answer = _clean((clarification or {}).get("answer"), 1200)
    rows, gaps = [], []
    for requirement in requirements:
        item = evidence_by_id.get(requirement["id"], {"matches": [], "self_rated": []})
        strong = next((match for match in item["matches"] if match["strength"] == "experience"), None)
        mention = item["matches"][0] if item["matches"] else None
        confirmed = requirement["id"] == confirmed_id and bool(confirmed_answer)
        status = "confirmed" if confirmed else "experience" if strong else "mention" if mention else "missing"
        row = {"requirement_id": requirement["id"], "requirement": requirement["text"],
               "weight": requirement["weight"], "status": status}
        if confirmed:
            row.update({"source": "candidate_confirmation", "answer": confirmed_answer})
        elif strong or mention:
            match = strong or mention
            row.update({"source": "resume", "line": match["line"], "text": match["text"]})
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


TOOL_REGISTRY = {
    "get_candidate_profile": get_candidate_profile,
    "parse_job_description": parse_job_description,
    "retrieve_resume_evidence": retrieve_resume_evidence,
    "assess_job_fit": assess_job_fit,
    "create_clarifying_question": create_clarifying_question,
}

