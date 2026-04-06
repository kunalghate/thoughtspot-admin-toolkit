"""
Pydantic response models for ThoughtSpot REST API v2.

These models define the shape of data returned by the TS API.
All API responses are parsed into these models — raw dicts never
leave the ts_client layer.

Only fields we actively use are declared. Extra fields are ignored (model_config).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TSBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── Enums ──────────────────────────────────────────────────────────────────────

class MetadataType(StrEnum):
    LIVEBOARD          = "LIVEBOARD"
    ANSWER             = "ANSWER"
    LOGICAL_TABLE      = "LOGICAL_TABLE"       # generic (used in TS API type field)
    WORKSHEET          = "WORKSHEET"           # subtype: standard worksheet
    ONE_TO_ONE_LOGICAL = "ONE_TO_ONE_LOGICAL"  # subtype: physical table
    AGGR_WORKSHEET     = "AGGR_WORKSHEET"      # subtype: materialized view / agg worksheet
    SQL_VIEW           = "SQL_VIEW"            # subtype: SQL view
    USER_DEFINED       = "USER_DEFINED"        # subtype: user-defined data source


class UserStatus(StrEnum):
    ACTIVE   = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuthType(StrEnum):
    BASIC   = "basic"
    TRUSTED = "trusted"
    BEARER  = "bearer"


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
    created: datetime | None = None
    modified: datetime | None = None


class TSGroupsResponse(TSBaseModel):
    user_groups: list[TSGroup] = Field(default_factory=list)


# ── Metadata (content) ─────────────────────────────────────────────────────────

class TSTag(TSBaseModel):
    id: str
    name: str
    color: str = Field(default="")


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
    author_name: str = ""       # authorDisplayName from metadata_header
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
            flat["owner_id"]    = header.get("author", "")
            flat["author_name"] = header.get("authorDisplayName", "")
            created_ms  = header.get("created")
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
                for t in raw_tags if isinstance(t, dict)
            ]
            obj = flat
        return super().model_validate(obj, **kwargs)


class TSMetadataResponse(TSBaseModel):
    metadata_details: list[TSMetadataObject] = Field(default_factory=list)


# ── Orgs ───────────────────────────────────────────────────────────────────────

class TSOrgStatus(StrEnum):
    ACTIVE   = "ACTIVE"
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


# ── Sharing ────────────────────────────────────────────────────────────────────

class SharePermission(StrEnum):
    READ_ONLY = "READ_ONLY"
    MODIFY    = "MODIFY"
    NO_ACCESS = "NO_ACCESS"


class TSShareResult(TSBaseModel):
    object_id: str
    success: bool
    error: str | None = None
