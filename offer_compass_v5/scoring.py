"""Two-stage job ranking: deterministic screening, then optional model scoring."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing

from offer_compass_tools import (assess_job_fit, get_candidate_profile,
                                 parse_job_description, retrieve_resume_evidence)

from .schemas import JobPreferences, ValidationError, clean_text, string_list
from .security import assess_page_text, redact
from .store import now, rows
from . import model_provider


PROMPT_VERSION = "job-score-v1"


def _profile(body):
    if not isinstance(body, dict):
        raise ValidationError("invalid_request")
    lines = body.get("lines")
    levels = body.get("levels") or {}
    if not (isinstance(lines, list) and 0 < len(lines) <= 1000
            and all(isinstance(line, str) and len(line) <= 4000 for line in lines)
            and sum(map(len, lines)) <= 60000
            and isinstance(levels, dict) and len(levels) <= 200
            and all(isinstance(key, str) and type(value) is int and 0 <= value <= 3
                    for key, value in levels.items())):
        raise ValidationError("invalid_profile")
    preferences = JobPreferences.from_dict(body.get("preferences"))
    clean_lines = [line.replace("\x00", "") for line in lines]
    clean_levels = {clean_text(key, 100, True): value for key, value in levels.items()}
    canonical = json.dumps({"lines": clean_lines, "levels": clean_levels,
                            "preferences": preferences.as_dict()},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return clean_lines, clean_levels, preferences, hashlib.sha256(canonical.encode()).hexdigest()


def _term_match(value, terms):
    normalized = str(value or "").casefold()
    return not terms or any(term.casefold().rstrip("市") in normalized for term in terms)


def _bounded_int(value, default, minimum, maximum):
    if value in (None, ""):
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError("invalid_number")
    return value


def _screen_one(job, profile, preferences):
    safety = assess_page_text(job["jd"])
    if not safety["safe"]:
        return {"score": 0, "disposition": "excluded", "matched": [], "gaps": [],
                "reasons": ["JD 包含疑似提示注入，已停止模型处理"]}
    parsed = parse_job_description(job["jd"])
    evidence = retrieve_resume_evidence(parsed["requirements"], profile)
    assessment = assess_job_fit(parsed["requirements"], evidence)
    requirements = assessment["requirements"]
    if requirements:
        points = {"experience": 1.0, "confirmed": 1.0, "mention": .45, "missing": 0.0}
        evidence_ratio = sum(points.get(item["status"], 0) for item in requirements) / len(requirements)
    else:
        evidence_ratio = 0
    role_match = _term_match(job["role"], preferences.roles)
    city_match = _term_match(job["city"], preferences.cities)
    industry_match = _term_match(job["industry"], preferences.industries)
    preference_score = (20 if role_match else 0) + (10 if city_match else 0) + (10 if industry_match else 0)
    if not preferences.roles:
        preference_score += 20
    if not preferences.cities:
        preference_score += 10
    if not preferences.industries:
        preference_score += 10
    jd_quality = 10 if len(job["jd"].strip()) >= 180 else 5 if len(job["jd"].strip()) >= 60 else 0
    score = max(0, min(100, round(evidence_ratio * 50 + preference_score + jd_quality)))
    hard_mismatch = ((preferences.roles and not role_match)
                     or (preferences.cities and not city_match)
                     or (preferences.industries and not industry_match))
    if hard_mismatch:
        disposition = "excluded"
    elif score >= 65:
        disposition = "shortlist"
    elif score >= 45:
        disposition = "review"
    else:
        disposition = "low_evidence"
    matched = [item for item in requirements if item["status"] in {"experience", "confirmed", "mention"}]
    gaps = [item["requirement"] for item in assessment.get("gaps", [])][:8]
    reasons = ["经历证据覆盖 {:.0%}".format(evidence_ratio)]
    if preferences.roles:
        reasons.append("岗位方向{}偏好".format("符合" if role_match else "不符合"))
    if preferences.cities:
        reasons.append("城市{}偏好".format("符合" if city_match else "不符合"))
    if preferences.industries:
        reasons.append("行业{}偏好".format("符合" if industry_match else "不符合"))
    if jd_quality < 10:
        reasons.append("JD 信息不够完整")
    return {"score": score, "disposition": disposition,
            "matched": [{"requirement": item["requirement"], "line": item.get("line"),
                         "status": item["status"]} for item in matched[:8]],
            "gaps": gaps, "reasons": reasons}


def local_screen(db_path, body):
    """Rank all active jobs without a model call and persist explainable results."""
    lines, levels, preferences, profile_hash = _profile(body)
    profile = get_candidate_profile(lines, levels)
    jobs = rows(db_path, "SELECT * FROM jobs WHERE status='active' ORDER BY last_seen_at DESC")
    stamp = now()
    results = []
    with closing(sqlite3.connect(str(db_path))) as db, db:
        for job in jobs:
            result = _screen_one(job, profile, preferences)
            db.execute("""INSERT INTO job_local_scores
                (job_id, profile_hash, local_score, disposition, matched_json, gaps_json,
                 reasons_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, profile_hash) DO UPDATE SET
                  local_score=excluded.local_score, disposition=excluded.disposition,
                  matched_json=excluded.matched_json, gaps_json=excluded.gaps_json,
                  reasons_json=excluded.reasons_json, updated_at=excluded.updated_at""", (
                job["job_id"], profile_hash, result["score"], result["disposition"],
                json.dumps(result["matched"], ensure_ascii=False),
                json.dumps(result["gaps"], ensure_ascii=False),
                json.dumps(result["reasons"], ensure_ascii=False), stamp, stamp))
            results.append({"job_id": job["job_id"], "company": job["company"],
                            "role": job["role"], **result})
    order = {"shortlist": 0, "review": 1, "low_evidence": 2, "excluded": 3}
    results.sort(key=lambda item: (order[item["disposition"]], -item["score"], item["job_id"]))
    return {"stage": "local_screen", "profile_hash": profile_hash,
            "total_jobs": len(results),
            "shortlisted": sum(item["disposition"] == "shortlist" for item in results),
            "review": sum(item["disposition"] == "review" for item in results),
            "model_calls": 0, "token_usage": 0, "results": results[:200]}


MODEL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommendation": {"type": "string", "enum": ["apply", "conditional", "hold"]},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "evidence_lines": {"type": "array", "items": {"type": "integer"}, "maxItems": 8},
    },
    "required": ["score", "recommendation", "summary", "strengths", "gaps", "evidence_lines"],
}


def _response_text(response):
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("model_output_missing")


def model_score(payload, model, max_output_tokens=800):
    request_body = {
        "model": model,
        "instructions": ("你是证据约束型岗位匹配评分器。只能根据输入中的 JD、简历证据行和本地初筛结果评分。"
                         "不得推断未提供的经历、能力、职责或成果。优势必须能指向 evidence_lines；证据不足就降低分数并列为缺口。"),
        "input": json.dumps(payload, ensure_ascii=False),
        "text": {"format": {"type": "json_schema", "name": "job_fit_score",
                            "strict": True, "schema": MODEL_SCHEMA}},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    result = model_provider.responses(request_body, timeout=30)
    return json.loads(_response_text(result)), result.get("usage") or {}


def _validated_model_result(result, allowed_lines):
    if not isinstance(result, dict):
        raise RuntimeError("invalid_model_score")
    score = result.get("score")
    recommendation = result.get("recommendation")
    if type(score) is not int or not 0 <= score <= 100 or recommendation not in {"apply", "conditional", "hold"}:
        raise RuntimeError("invalid_model_score")
    valid_lines = [line for line in result.get("evidence_lines", [])
                   if type(line) is int and line in allowed_lines][:8]
    strengths = string_list(result.get("strengths"), 5, 300) if valid_lines else []
    if recommendation == "apply" and not valid_lines:
        recommendation = "conditional"
    return {
        "score": score, "recommendation": recommendation,
        "summary": clean_text(result.get("summary"), 1000, True),
        "strengths": strengths,
        "gaps": string_list(result.get("gaps"), 5, 300),
        "evidence_lines": valid_lines,
    }


def deep_score(db_path, body, scorer=None):
    """Model-score only the local shortlist, with explicit consent, budget and cache."""
    if not isinstance(body, dict) or body.get("allow_model") is not True:
        raise ValidationError("model_consent_required")
    lines, levels, preferences, profile_hash = _profile(body)
    max_jobs = _bounded_int(body.get("max_jobs"), 5, 1, 20)
    max_total_tokens = _bounded_int(body.get("max_total_tokens"), 12000, 1500, 50000)
    provider_config = model_provider.settings()
    model = clean_text(body.get("model") or provider_config["model"], 120, True)
    model_identity = provider_config["provider"] + ":" + model
    local = local_screen(db_path, {"lines": lines, "levels": levels, "preferences": preferences.as_dict()})
    candidates = rows(db_path, """SELECT j.*, s.local_score, s.disposition, s.matched_json,
        s.gaps_json, s.reasons_json FROM jobs j JOIN job_local_scores s ON s.job_id=j.job_id
        WHERE s.profile_hash=? AND s.disposition IN ('shortlist','review')
        ORDER BY s.local_score DESC, j.last_seen_at DESC LIMIT ?""", (profile_hash, max_jobs))
    run_id, stamp = "score_" + uuid.uuid4().hex, now()
    called = cache_hits = used_input = used_output = budget_skipped = 0
    output = []
    remaining = max_total_tokens
    scorer = scorer or model_score
    for job in candidates:
        cache_key = hashlib.sha256((job["job_id"] + ":" + job["content_hash"] + ":" +
                                    profile_hash + ":" + PROMPT_VERSION + ":" + model_identity).encode()).hexdigest()
        cached = rows(db_path, "SELECT * FROM job_deep_scores WHERE cache_key=?", (cache_key,))
        if cached:
            item = cached[0]
            cache_hits += 1
            output.append({"job_id": job["job_id"], "company": job["company"], "role": job["role"],
                           "score": item["deep_score"], "recommendation": item["recommendation"],
                           "summary": item["summary"], "strengths": json.loads(item["strengths_json"]),
                           "gaps": json.loads(item["gaps_json"]), "evidence_lines": json.loads(item["evidence_lines_json"]),
                           "cached": True})
            continue
        matched = json.loads(job["matched_json"])
        evidence_lines = sorted({item["line"] for item in matched if type(item.get("line")) is int})
        evidence = [{"line": index, "text": redact(lines[index - 1])}
                    for index in evidence_lines if 0 < index <= len(lines)]
        payload = {"job": {"company": job["company"], "role": job["role"], "city": job["city"],
                           "industry": job["industry"], "jd": job["jd"][:16000]},
                   "local_screen": {"score": job["local_score"], "matched": matched,
                                    "gaps": json.loads(job["gaps_json"]),
                                    "reasons": json.loads(job["reasons_json"])},
                   "resume_evidence": evidence}
        # UTF-8 byte length plus a fixed protocol allowance intentionally
        # overestimates common model tokenization before a paid call.
        estimated_input = max(500, len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 1000)
        reserved_output = 800
        if estimated_input + reserved_output > remaining:
            budget_skipped = len(candidates) - len(output)
            break
        result, usage = scorer(payload, model, reserved_output)
        result = _validated_model_result(result, set(evidence_lines))
        input_tokens = int(usage.get("input_tokens") or estimated_input)
        output_tokens = int(usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        remaining = max(0, remaining - total_tokens)
        called += 1
        used_input += input_tokens
        used_output += output_tokens
        with closing(sqlite3.connect(str(db_path))) as db, db:
            db.execute("""INSERT INTO job_deep_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                cache_key, job["job_id"], profile_hash, job["content_hash"], PROMPT_VERSION, model_identity,
                result["score"], result["recommendation"], result["summary"],
                json.dumps(result["strengths"], ensure_ascii=False), json.dumps(result["gaps"], ensure_ascii=False),
                json.dumps(result["evidence_lines"], ensure_ascii=False), input_tokens, output_tokens, stamp))
        output.append({"job_id": job["job_id"], "company": job["company"], "role": job["role"],
                       **result, "cached": False})
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("""INSERT INTO job_scoring_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            run_id, profile_hash, model_identity, max_jobs, max_total_tokens, len(candidates), called,
            cache_hits, used_input, used_output, stamp))
    output.sort(key=lambda item: (-item["score"], item["job_id"]))
    return {"stage": "deep_score", "run_id": run_id, "provider": provider_config["provider"], "model": model,
            "candidate_count": len(candidates), "model_calls": called, "cache_hits": cache_hits,
            "budget_skipped": budget_skipped,
            "usage": {"input_tokens": used_input, "output_tokens": used_output,
                      "total_tokens": used_input + used_output, "budget": max_total_tokens},
            "local_summary": {key: local[key] for key in ("total_jobs", "shortlisted", "review")},
            "results": output}
