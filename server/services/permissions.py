"""Who may act for which organization, and who gets told about what."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from authentication import scopes_for_user
from models.organization_and_venue import Organization, OrganizationMember, venue_hosts
from models.user import User, UserRole


def has_scope(user: User, scope: str) -> bool:
    """Whether the user's role grants `scope` (same source as their token)."""
    return scope in scopes_for_user(user)


def require_scope(user: User, *scopes: str) -> None:
    """403 unless the user has at least one of `scopes`."""
    if not any(has_scope(user, s) for s in scopes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Not enough permissions")


def as_uuid(value, what: str = "id") -> uuid.UUID:
    """Parse a UUID from a path/body value, 404-ing on garbage."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown {what}.")


def _current_membership_filter(today: dt.date):
    return (
        OrganizationMember.verified_at.isnot(None),
        OrganizationMember.term_start <= today,
        or_(OrganizationMember.term_end.is_(None), OrganizationMember.term_end >= today),
    )


def verified_org_ids(db: Session, user: User) -> set[uuid.UUID]:
    """Organizations the user is a verified, current member of (NFR-1, NFR-2)."""
    rows = (
        db.query(OrganizationMember.organization_id)
        .filter(OrganizationMember.user_id == user.id,
                *_current_membership_filter(dt.date.today()))
        .all()
    )
    return {r[0] for r in rows}


def require_member(db: Session, user: User, organization_id) -> uuid.UUID:
    """403 unless the user is a verified, current member of the organization.
    Admins with handle:requests may act for any organization."""
    org_id = as_uuid(organization_id, "organization")
    if db.get(Organization, org_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    if has_scope(user, "handle:requests"):
        return org_id
    if org_id not in verified_org_ids(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You're not a verified current member of that organization. "
                   "Ask an administrator to verify your membership.",
        )
    return org_id


def org_leaders(db: Session, organization_id) -> list[User]:
    """Verified, current, active members of an organization."""
    return (
        db.query(User)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .filter(OrganizationMember.organization_id == organization_id,
                User.is_active.is_(True),
                *_current_membership_filter(dt.date.today()))
        .all()
    )


def admins(db: Session) -> list[User]:
    return db.query(User).filter(User.role == UserRole.ADMIN, User.is_active.is_(True)).all()


def venue_host_ids(db: Session, venue_id) -> set[uuid.UUID]:
    if venue_id is None:
        return set()
    rows = db.execute(select(venue_hosts.c.user_id)
                      .where(venue_hosts.c.venue_id == venue_id)).all()
    return {r[0] for r in rows}


def can_manage_venue(db: Session, user: User, venue_id) -> bool:
    """Admins manage every venue; venue hosts only the venues they host."""
    if user.role == UserRole.ADMIN:
        return True
    return user.role == UserRole.VENUE_HOST and user.id in venue_host_ids(db, venue_id)
