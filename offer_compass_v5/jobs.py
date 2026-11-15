"""Trusted-source job discovery and persistence."""

import hashlib
import html
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schemas import DiscoveryRequest, ValidationError
from .store import now, rows


class _TextParser(HTMLParser):
    BREAKS = {"br", "p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def html_text(value):
    parser = _TextParser()
    parser.feed(html.unescape(str(value or ""))[:1000000])
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()[:50000]


def greenhouse_token(value):
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        return value
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        token = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", token):
            return token
    raise ValidationError("invalid_greenhouse_source")


def detect_source(value):
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        return "greenhouse"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ValidationError("source_must_use_https")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "greenhouse"
    if host == "jobs.bytedance.com" and (parsed.path == "/campus" or parsed.path.startswith("/campus/")):
        return "bytedance"
    raise ValidationError("unsupported_source_url")


def bytedance_source(value):
    if detect_source(value) != "bytedance":
        raise ValidationError("invalid_bytedance_source")
    return "bytedance-campus"


def _matches(value, terms):
    value = value.casefold()
    return not terms or any(term.casefold().rstrip("市") in value for term in terms)


def parse_greenhouse_jobs(payload, request):
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValidationError("invalid_source_response")
    result = []
    for item in payload["jobs"][:5000]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("title") or "").strip()[:300]
        url = str(item.get("absolute_url") or "").strip()[:1200]
        city = str((item.get("location") or {}).get("name") or "").strip()[:200]
        if not role or urlparse(url).scheme != "https":
            continue
        if not (_matches(role, request.preferences.roles)
                and _matches(request.industry, request.preferences.industries)
                and _matches(city, request.preferences.cities)):
            continue
        result.append({
            "external_id": str(item.get("id") or url)[:300], "company": request.company or greenhouse_token(request.source),
            "role": role, "industry": request.industry, "city": city,
            "jd": html_text(item.get("content")), "official_url": url,
        })
    return result


def discover_greenhouse(request, timeout=12):
    token = greenhouse_token(request.source)
    url = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true".format(token)
    http_request = Request(url, headers={"User-Agent": "Offer-Compass/5.5", "Accept": "application/json"})
    with urlopen(http_request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("source_http_{}".format(response.status))
        payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    return token, parse_greenhouse_jobs(payload, request)


def _byte_city(item):
    values = []
    for candidate in ((item.get("city_info") or {}), *((item.get("city_list") or [])),
                      *((item.get("city_info_list") or []))):
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("city_info") if isinstance(candidate.get("city_info"), dict) else {}
        name = candidate.get("name") or candidate.get("cn_name") or nested.get("name") or nested.get("cn_name")
        if name and str(name).strip() not in values:
            values.append(str(name).strip())
    return " / ".join(values)[:200]


def parse_bytedance_jobs(payload, request):
    data = payload.get("data") if isinstance(payload, dict) else None
    posts = data.get("job_post_list") if isinstance(data, dict) else None
    if payload.get("code") != 0 or not isinstance(posts, list):
        raise ValidationError("invalid_bytedance_response")
    result = []
    for item in posts:
        if not isinstance(item, dict):
            continue
        external_id = str(item.get("id") or "").strip()
        role = str(item.get("title") or "").strip()[:300]
        city = _byte_city(item)
        if not external_id or not role:
            continue
        if not (_matches(role, request.preferences.roles)
                and _matches(request.industry, request.preferences.industries)
                and _matches(city, request.preferences.cities)):
            continue
        description = html_text(item.get("description"))
        requirement = html_text(item.get("requirement"))
        jd = "\n".join(part for part in
                       (("岗位职责\n" + description) if description else "",
                        ("岗位要求\n" + requirement) if requirement else "") if part)[:50000]
        result.append({"external_id": external_id, "company": request.company or "字节跳动",
                       "role": role, "industry": request.industry or "互联网", "city": city,
                       "jd": jd, "official_url":
                       "https://jobs.bytedance.com/campus/position/{}/detail".format(external_id)})
    return result


def discover_bytedance(request, timeout=15):
    bytedance_source(request.source)
    url = "https://jobs.bytedance.com/api/v1/search/job/posts"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
               "Content-Type": "application/json", "Origin": "https://jobs.bytedance.com",
               "Referer": "https://jobs.bytedance.com/campus/position", "portal-channel": "campus",
               "portal-platform": "pc", "website-path": "campus"}
    keywords = request.preferences.roles[:5] or [""]
    found = {}
    for keyword in keywords:
        for page in range(5):
            body = {"keyword": keyword, "limit": 50, "offset": page * 50,
                    "portal_type": 3, "portal_entrance": 1, "language": "zh",
                    "recruitment_id_list": ["201"], "job_category_id_list": [],
                    "location_code_list": [], "subject_id_list": [], "tag_id_list": [],
                    "storefront_id_list": [], "job_function_id_list": []}
            http_request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                   headers=headers, method="POST")
            with urlopen(http_request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError("source_http_{}".format(response.status))
                payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
            parsed = parse_bytedance_jobs(payload, request)
            for job in parsed:
                found[job["external_id"]] = job
            raw_posts = ((payload.get("data") or {}).get("job_post_list") or [])
            if len(raw_posts) < 50:
                break
            if len(found) >= 1000:
                break
    return "bytedance-campus", list(found.values())


def persist_discovery(db_path, request, token, discovered):
    source_id = "source_" + hashlib.sha256((request.source_type + ":" + token).encode()).hexdigest()[:20]
    stamp = now()
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("""INSERT INTO job_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET company=excluded.company, industry=excluded.industry,
              last_status=excluded.last_status, last_error=NULL, checked_at=excluded.checked_at""",
            (source_id, request.source_type, token, request.company or token, request.industry,
             "completed", None, stamp))
        ids = []
        for job in discovered:
            job_id = "job_" + hashlib.sha256((source_id + ":" + job["external_id"]).encode()).hexdigest()[:24]
            digest = hashlib.sha256(job["jd"].encode("utf-8")).hexdigest()
            db.execute("""INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET company=excluded.company, role=excluded.role,
                  industry=excluded.industry, city=excluded.city, jd=excluded.jd,
                  official_url=excluded.official_url, status='active', content_hash=excluded.content_hash,
                  last_seen_at=excluded.last_seen_at""", (
                job_id, source_id, job["external_id"], job["company"], job["role"], job["industry"],
                job["city"], job["jd"], job["official_url"], "active", digest, stamp, stamp))
            ids.append(job_id)
    return {"source_id": source_id, "discovered_count": len(ids), "job_ids": ids[:100],
            "job_ids_limited": len(ids) > 100, "checked_at": stamp}


def discover(db_path, body, fetcher=None):
    request = DiscoveryRequest.from_dict(body)
    resolved = request.source_type if request.source_type != "auto" else detect_source(request.source)
    request = replace(request, source_type=resolved)
    discoverer = fetcher or {"greenhouse": discover_greenhouse, "bytedance": discover_bytedance}[resolved]
    token, jobs = discoverer(request)
    result = persist_discovery(db_path, request, token, jobs)
    result["source_type"] = resolved
    return result


def list_jobs(db_path, limit=50):
    limit = max(1, min(int(limit), 200))
    items = rows(db_path, """SELECT j.job_id, j.company, j.role, j.industry, j.city, j.jd,
        j.official_url, j.status, j.first_seen_at, j.last_seen_at,
        ls.local_score, ls.disposition, ls.reasons_json, ls.gaps_json,
        ds.deep_score, ds.recommendation, ds.summary AS deep_summary, ds.model AS score_model,
        ds.input_tokens, ds.output_tokens, ds.created_at AS scored_at
        FROM jobs j
        LEFT JOIN job_local_scores ls ON ls.rowid=(SELECT rowid FROM job_local_scores x
            WHERE x.job_id=j.job_id ORDER BY x.updated_at DESC LIMIT 1)
        LEFT JOIN job_deep_scores ds ON ds.rowid=(SELECT rowid FROM job_deep_scores y
            WHERE y.job_id=j.job_id AND y.profile_hash=ls.profile_hash ORDER BY y.created_at DESC LIMIT 1)
        ORDER BY COALESCE(ds.deep_score, ls.local_score, -1) DESC, j.last_seen_at DESC LIMIT ?""", (limit,))
    for item in items:
        for source, target in (("reasons_json", "screen_reasons"), ("gaps_json", "screen_gaps")):
            try:
                item[target] = json.loads(item.pop(source) or "[]")
            except (TypeError, ValueError):
                item[target] = []
    return {"jobs": items}


def get_job(db_path, job_id):
    found = rows(db_path, "SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if not found:
        raise KeyError(job_id)
    return found[0]
