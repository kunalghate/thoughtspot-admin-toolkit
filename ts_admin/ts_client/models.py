"""
Pydantic response models for ThoughtSpot REST API v2.

These models define the shape of data returned by the TS API.
All API responses are parsed into these models — raw dicts never
leave the ts_client layer.

Only fields we actively use are declared. Extra fields are ignored (model_config).
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TSBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_explicit_nulls(cls, data: Any) -> Any:
        """
        Treat an explicit ``null`` as "field absent" so the declared default applies.

        ThoughtSpot sends `null` (not an omitted key) for unset optional values —
        e.g. a group with no description comes back as ``"description": null``.
        Pydantic only falls back to a field's default when the key is *missing*,
        so those nulls used to blow up parsing (`groups/search` on the primary
        org, where the stock system groups have no description).

        Only nulls for non-nullable fields that have a default are dropped. A
        null on a required field still raises, so a genuinely broken response
        is not silently swallowed.
        """
        if not isinstance(data, dict):
            return data

        droppable = {
            key
            for name, field in cls.model_fields.items()
            for key in (name, field.alias)
            if key and not field.is_required() and not _allows_none(field.annotation)
        }
        nulls = droppable & {k for k, v in data.items() if v is None}
        return {k: v for k, v in data.items() if k not in nulls} if nulls else data


def _allows_none(annotation: Any) -> bool:
    """True if the annotation accepts None (Optional[X], X | None, Any, bare None)."""
    return annotation is None or annotation is Any or type(None) in get_args(annotation)


def _map_epoch_ms_times(data: dict) -> dict:
    """
    Map TS v2 epoch-ms time fields onto created/modified.

    users/search and groups/search return `creation_time_in_millis` /
    `modification_time_in_millis` (epoch ms) rather than `created`/`modified`.
    """
    out = dict(data)
    for target, source in (
        ("created", "creation_time_in_millis"),
        ("modified", "modification_time_in_millis"),
    ):
        ms = out.get(source)
        if out.get(target) is None and ms:
            out[target] = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return out


# ── Enums ──────────────────────────────────────────────────────────────────────


class MetadataType(StrEnum):
    LIVEBOARD = "LIVEBOARD"
    ANSWER = "ANSWER"
    LOGICAL_TABLE = "LOGICAL_TABLE"  # generic (used in TS API type field)
    WORKSHEET = "WORKSHEET"  # subtype: standard worksheet
    ONE_TO_ONE_LOGICAL = "ONE_TO_ONE_LOGICAL"  # subtype: physical table
    AGGR_WORKSHEET = "AGGR_WORKSHEET"  # subtype: materialized view / agg worksheet
    SQL_VIEW = "SQL_VIEW"  # subtype: SQL view
    USER_DEFINED = "USER_DEFINED"  # subtype: user-defined data source (CSV/Excel upload)
    # Not a ThoughtSpot API type. An Analyst Studio dataset is an ONE_TO_ONE_LOGICAL
    # table whose connection reports dataSourceTypeEnum == RDBMS_MODE (Analyst Studio
    # is built on Mode). The API exposes no subtype for it, so search_metadata()
    # derives this effective type and we store it like any other.
    DATASET = "DATASET"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuthType(StrEnum):
    BASIC = "basic"
    TRUSTED = "trusted"
    BEARER = "bearer"


# ── Users ──────────────────────────────────────────────────────────────────────


class TSUser(TSBaseModel):
    id: str = Field(alias="id")
    name: str
    display_name: str = Field(alias="display_name", default="")
    email: str = Field(default="")
    status: UserStatus = UserStatus.ACTIVE
    created: datetime | None = None
    modified: datetime | None = None
    group_identifiers: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _epoch_times(cls, data: Any) -> Any:
        return _map_epoch_ms_times(data) if isinstance(data, dict) else data

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE


class TSUsersResponse(TSBaseModel):
    users: list[TSUser] = Field(default_factory=list)


# ── Groups ─────────────────────────────────────────────────────────────────────


class TSGroup(TSBaseModel):
    id: str
    name: str
    display_name: str = Field(default="")
    description: str = Field(default="")
    privileges: list[str] = Field(default_factory=list)
    member_users: list[str] = Field(default_factory=list)
    sub_groups: list[str] = Field(default_factory=list)
    # GUID of the user who created the group. groups/search returns it as
    # `author_id`; the ThoughtSpot UI does not surface it anywhere.
    author_id: str = Field(default="")
    created: datetime | None = None
    modified: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _epoch_times(cls, data: Any) -> Any:
        return _map_epoch_ms_times(data) if isinstance(data, dict) else data

    @model_validator(mode="before")
    @classmethod
    def _normalize_principal_lists(cls, data: Any) -> Any:
        # The v2 groups/search response returns members under "users" and
        # sub-groups under "sub_groups" as {"id": ..., "name": ...} objects;
        # normalize both to GUID lists.
        if not isinstance(data, dict):
            return data
        for field, source in (("member_users", "users"), ("sub_groups", "sub_groups")):
            raw = data.get(field) or data.get(source) or []
            guids = [(p.get("id") if isinstance(p, dict) else p) for p in raw]
            data[field] = [g for g in guids if g]
        return data


class TSGroupsResponse(TSBaseModel):
    user_groups: list[TSGroup] = Field(default_factory=list)


# ── Metadata (content) ─────────────────────────────────────────────────────────


class TSTag(TSBaseModel):
    id: str
    name: str
    color: str | None = Field(default=None)

    @property
    def color_str(self) -> str:
        return self.color or ""


class TSMetadataObject(TSBaseModel):
    """
    Parsed from /api/rest/2.0/metadata/search response.

    Top-level keys: metadata_id, metadata_name, metadata_type
    Nested under metadata_header: id, name, author, authorDisplayName,
                                   created (epoch ms), modified (epoch ms), tags
    """

    id: str = Field(alias="metadata_id")
    name: str = Field(alias="metadata_name")
    type: MetadataType = Field(alias="metadata_type")
    owner_id: str = ""
    author_name: str = ""  # authorDisplayName from metadata_header
    created: datetime | None = None
    modified: datetime | None = None
    tags: list[TSTag] = Field(default_factory=list)
    last_accessed: datetime | None = None
    view_count: int = 0

    @classmethod
    def model_validate(cls, obj: Any, **kwargs) -> "TSMetadataObject":
        """Extract nested metadata_header fields before standard validation."""
        if isinstance(obj, dict) and "metadata_header" in obj:
            header = obj.get("metadata_header") or {}
            flat = dict(obj)
            flat["owner_id"] = header.get("author", "")
            flat["author_name"] = header.get("authorDisplayName", "")
            created_ms = header.get("created")
            modified_ms = header.get("modified")
            if created_ms:
                from datetime import timezone

                flat["created"] = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if modified_ms:
                from datetime import timezone

                flat["modified"] = datetime.fromtimestamp(modified_ms / 1000, tz=timezone.utc)
            raw_tags = header.get("tags") or []
            flat["tags"] = [
                {"id": t.get("id", ""), "name": t.get("name", ""), "color": t.get("color", "")}
                for t in raw_tags
                if isinstance(t, dict)
            ]
            # stats object: {"views": int, "last_accessed": epoch_ms | None, ...}
            stats = obj.get("stats") or {}
            last_accessed_ms = stats.get("last_accessed")
            if last_accessed_ms:
                from datetime import timezone

                flat["last_accessed"] = datetime.fromtimestamp(last_accessed_ms / 1000, tz=timezone.utc)
            flat["view_count"] = stats.get("views", 0) or 0
            obj = flat
        return super().model_validate(obj, **kwargs)


class TSMetadataResponse(TSBaseModel):
    metadata_details: list[TSMetadataObject] = Field(default_factory=list)


# ── Orgs ───────────────────────────────────────────────────────────────────────


class TSOrgStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class TSOrg(TSBaseModel):
    id: int = Field(alias="org_id")
    name: str = Field(alias="org_name")
    description: str = Field(default="")
    status: TSOrgStatus = TSOrgStatus.ACTIVE
    is_primary: bool = False


class TSOrgsResponse(TSBaseModel):
    orgs: list[TSOrg] = Field(default_factory=list)


# ── Dependencies ───────────────────────────────────────────────────────────────


class TSDependency(TSBaseModel):
    """A directed dependency edge: source object depends on target object."""

    source_id: str
    source_type: MetadataType
    target_id: str
    target_type: MetadataType


# ── Auth / session ─────────────────────────────────────────────────────────────


class TSTokenResponse(TSBaseModel):
    token: str
    token_expiry_duration: int = Field(default=86400)
    valid_for_user_id: str = Field(default="")
    valid_for_username: str = Field(default="")


# ── Sharing / Permissions ──────────────────────────────────────────────────────


class SharePermission(StrEnum):
    READ_ONLY = "READ_ONLY"
    MODIFY = "MODIFY"
    NO_ACCESS = "NO_ACCESS"


class TSShareResult(TSBaseModel):
    object_id: str
    success: bool
    error: str | None = None


class TSPermission(TSBaseModel):
    """One principal's access level on a metadata object."""

    principal_id: str
    principal_name: str
    principal_type: str  # "USER" or "USER_GROUP"
    share_mode: SharePermission
