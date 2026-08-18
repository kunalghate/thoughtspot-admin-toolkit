"""
group_service — read-only query layer for the Groups page.

Reads:
  - list_groups()                — paginated group grid from CachedGroup
  - get_group_detail(ts_guid)    — profile + privileges + member users

Group Management v1 is read-only (CS Tools precedent): browsing, privileges
audit, and membership inspection. All writes stay in the ThoughtSpot UI.
SQLite-only, so every function is sync.
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy.orm import aliased
from sqlmodel import Session, col, func, select

import ts_admin.database as _db
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_user import CachedUser, UserGroupMembership

logger = logging.getLogger(__name__)


def _group_row_to_dict(g: CachedGroup, member_count: int, created_by: str | None = None) -> dict:
    return {
        "ts_guid": g.ts_guid,
        "name": g.name,
        "display_name": g.display_name,
        "description": g.description,
        "org_id": g.org_id,
        "privileges": g.get_privileges(),
        "member_count": member_count,
        # Display name of the creating user, resolved from the cached users
        # table. None when the creator is not in the cache (users never synced,
        # or the account was deleted upstream) — the caller renders the raw
        # GUID fallback rather than claiming the group has no creator.
        "created_by": created_by or (g.author_guid or None),
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "modified_at": g.modified_at.isoformat() if g.modified_at else None,
        "synced_at": g.synced_at.isoformat() if g.synced_at else None,
    }


def list_groups(
    *,
    cluster_id: str,
    org_id: int | None = None,
    search: str | None = None,
    sort_field: str = "name",
    sort_order: Literal["asc", "desc"] = "asc",
    record_offset: int = 0,
    page_size: int = 200,
) -> tuple[list[dict], int]:
    """Paginated group grid with a per-group member count."""
    with Session(_db.get_engine()) as session:
        # Groups are org-scoped, so count membership per (group, org) — the
        # same group GUID can appear as separate rows in different orgs.
        member_counts = (
            select(
                UserGroupMembership.group_guid,
                UserGroupMembership.org_id,
                func.count().label("member_count"),
            )
            .where(UserGroupMembership.cluster_id == cluster_id)
            .group_by(UserGroupMembership.group_guid, UserGroupMembership.org_id)
            .subquery()
        )
        member_count_col = func.coalesce(member_counts.c.member_count, 0)

        # Creator display name. Users are cluster-scoped (not org-scoped), so
        # the join is on cluster_id + GUID only. LEFT JOIN on purpose: a group
        # whose creator has been deleted upstream must still list.
        creator = aliased(CachedUser)

        base = (
            select(CachedGroup, member_count_col.label("member_count"), col(creator.display_name))
            .outerjoin(
                member_counts,
                (member_counts.c.group_guid == CachedGroup.ts_guid) & (member_counts.c.org_id == CachedGroup.org_id),
            )
            .outerjoin(
                creator,
                (col(creator.cluster_id) == CachedGroup.cluster_id) & (col(creator.ts_guid) == CachedGroup.author_guid),
            )
            .where(CachedGroup.cluster_id == cluster_id)
        )
        if org_id is not None:
            base = base.where(CachedGroup.org_id == org_id)
        if search:
            pattern = f"%{search}%"
            base = base.where(
                col(CachedGroup.name).ilike(pattern)
                | col(CachedGroup.display_name).ilike(pattern)
                | col(CachedGroup.description).ilike(pattern)
            )

        count_q = select(func.count()).select_from(base.subquery())
        total = session.exec(count_q).one()

        sort_col = {
            "name": col(CachedGroup.name),
            "display_name": col(CachedGroup.display_name),
            "member_count": member_count_col,
            "created_at": col(CachedGroup.created_at),
            "modified_at": col(CachedGroup.modified_at),
            "synced_at": col(CachedGroup.synced_at),
            "created_by": col(creator.display_name),
        }.get(sort_field, col(CachedGroup.name))
        base = base.order_by(sort_col.desc() if sort_order == "desc" else sort_col.asc())
        base = base.offset(record_offset).limit(page_size)

        rows = session.exec(base).all()
        return [_group_row_to_dict(g, count, created_by) for g, count, created_by in rows], total


def get_group_detail(*, cluster_id: str, ts_guid: str, org_id: int | None = None) -> dict | None:
    """
    Single group + its member users (from the membership junction table).

    Membership is always scoped to the group row's own org — the grid counts
    per (group, org), so an unscoped count here would disagree with the row the
    user clicked. Pass org_id to disambiguate when the same GUID is cached in
    more than one org; without it the lowest org_id wins, deterministically.
    """
    with Session(_db.get_engine()) as session:
        group_q = select(CachedGroup).where(
            CachedGroup.cluster_id == cluster_id,
            CachedGroup.ts_guid == ts_guid,
        )
        if org_id is not None:
            group_q = group_q.where(CachedGroup.org_id == org_id)
        group = session.exec(group_q.order_by(col(CachedGroup.org_id).asc())).first()
        if group is None:
            return None

        # Count from the junction table (not the joined users) so the number
        # matches the grid even when users haven't been synced yet.
        member_count = session.exec(
            select(func.count())
            .select_from(UserGroupMembership)
            .where(
                UserGroupMembership.cluster_id == cluster_id,
                UserGroupMembership.group_guid == ts_guid,
                UserGroupMembership.org_id == group.org_id,
            )
        ).one()

        members = session.exec(
            select(CachedUser)
            .join(
                UserGroupMembership,
                (UserGroupMembership.user_guid == CachedUser.ts_guid)
                & (UserGroupMembership.cluster_id == CachedUser.cluster_id),
            )
            .where(
                UserGroupMembership.cluster_id == cluster_id,
                UserGroupMembership.group_guid == ts_guid,
                UserGroupMembership.org_id == group.org_id,
            )
            .order_by(col(CachedUser.username).asc())
        ).all()

        creator_name = (
            session.exec(
                select(col(CachedUser.display_name)).where(
                    CachedUser.cluster_id == cluster_id,
                    CachedUser.ts_guid == group.author_guid,
                )
            ).first()
            if group.author_guid
            else None
        )

        out = _group_row_to_dict(group, member_count, creator_name)
        out["members"] = [
            {
                "ts_guid": u.ts_guid,
                "username": u.username,
                "display_name": u.display_name,
                "email": u.email,
                "status": u.status,
            }
            for u in members
        ]
        return out
