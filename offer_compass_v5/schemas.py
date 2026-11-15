"""Small dependency-free schemas for V5 API boundaries."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


class ValidationError(ValueError):
    """Raised when an external request crosses a V5 boundary."""


def clean_text(value: Any, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise ValidationError("text_required")
        return ""
    value = value.replace("\x00", "").strip()
    if required and not value:
        raise ValidationError("text_required")
    if len(value) > maximum:
        raise ValidationError("text_too_long")
    return value


def string_list(value: Any, limit: int = 20, item_limit: int = 120) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValidationError("invalid_list")
    return [clean_text(item, item_limit, required=True) for item in value]


@dataclass(frozen=True)
class JobPreferences:
    roles: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    cities: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Any) -> "JobPreferences":
        value = value or {}
        if not isinstance(value, dict):
            raise ValidationError("invalid_preferences")
        return cls(
            roles=string_list(value.get("roles")),
            industries=string_list(value.get("industries")),
            cities=string_list(value.get("cities")),
        )

    def as_dict(self) -> Dict[str, List[str]]:
        return {"roles": self.roles, "industries": self.industries, "cities": self.cities}


@dataclass(frozen=True)
class DiscoveryRequest:
    source_type: str
    source: str
    company: str
    industry: str
    preferences: JobPreferences

    @classmethod
    def from_dict(cls, value: Any) -> "DiscoveryRequest":
        if not isinstance(value, dict):
            raise ValidationError("invalid_request")
        source_type = clean_text(value.get("source_type", "auto"), 30, True).lower()
        if source_type not in {"auto", "greenhouse", "bytedance"}:
            raise ValidationError("unsupported_source")
        return cls(
            source_type=source_type,
            source=clean_text(value.get("source"), 500, True),
            company=clean_text(value.get("company"), 160),
            industry=clean_text(value.get("industry"), 120),
            preferences=JobPreferences.from_dict(value.get("preferences")),
        )


@dataclass(frozen=True)
class BrowserField:
    field_id: str
    label: str
    name: str
    field_type: str
    required: bool

    @classmethod
    def from_dict(cls, value: Any, index: int) -> "BrowserField":
        if not isinstance(value, dict):
            raise ValidationError("invalid_field")
        return cls(
            field_id=clean_text(value.get("id") or "field_{}".format(index + 1), 120, True),
            label=clean_text(value.get("label"), 240),
            name=clean_text(value.get("name"), 160),
            field_type=clean_text(value.get("type", "text"), 40, True).lower(),
            required=bool(value.get("required")),
        )


def bounded_dict(value: Any, limit: int = 80, item_limit: int = 2000) -> Dict[str, str]:
    if not isinstance(value, dict) or len(value) > limit:
        raise ValidationError("invalid_object")
    result = {}
    for key, item in value.items():
        result[clean_text(key, 120, True)] = clean_text(item, item_limit)
    return result
