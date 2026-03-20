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
    LIVEBOARD        = "LIVEBOARD"
    ANSWER           = "ANSWER"
    LOGICAL_TABLE    = "LOGICAL_TABLE"      # Worksheet
    ONE_TO_ONE_LOGICAL = "ONE_TO_ONE_LOGICAL"  # Table / View


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
    id: str
    name: str
    type: MetadataType
    owner_id: str = Field(alias="owner_id", default="")
    author_id: str = Field(alias="author_id", default="")
    author_name: str = Field(alias="author_name", default="")
    created: datetime | None = None
    modified: datetime | None = None
    tags: list[TSTag] = Field(default_factory=list)

    # Populated from usage stats endpoint when available
    last_accessed: datetime | None = None
    view_count: int = 0


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
