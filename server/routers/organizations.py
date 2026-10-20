"""Organizations (clubs) and joining them."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from authentication import get_current_user
from database import get_db
from models.organization_and_venue import Organization, OrganizationMember
from models.user import User
from routers.admin import MembershipOut, membership_out, notify_admins_of_join
from services.permissions import as_uuid, has_scope, verified_org_ids

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationOut(BaseModel):
    id: str
    name: str
    description: str | None
    verified_member_count: int
    members: list[MembershipOut] | None = None   # only for admins and the org's members


class JoinBody(BaseModel):
    position_title: str | None = Field(None, max_length=100, description='e.g. "President"')


@router.get("", response_model=list[OrganizationOut])
def list_organizations(
    search: str | None = Query(None, description="Name contains"),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    counts = dict(
        db.query(OrganizationMember.organization_id, func.count())
        .filter(OrganizationMember.verified_at.isnot(None))
        .group_by(OrganizationMember.organization_id).all())
    q = db.query(Organization)
    if search:
        q = q.filter(func.lower(Organization.name).like(f"%{search.lower()}%"))
    return [OrganizationOut(id=str(o.id), name=o.name, description=o.description,
                            verified_member_count=counts.get(o.id, 0))
            for o in q.order_by(Organization.name).all()]


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_organization(
    organization_id: str,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    org = db.get(Organization, as_uuid(organization_id, "organization"))
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    see_members = has_scope(user, "manage:users") or org.id in verified_org_ids(db, user)
    return OrganizationOut(
        id=str(org.id), name=org.name, description=org.description,
        verified_member_count=sum(1 for m in org.memberships if m.verified_at),
        members=[membership_out(m) for m in org.memberships] if see_members else None)


@router.post("/{organization_id}/join", response_model=MembershipOut,
             status_code=status.HTTP_201_CREATED)
def request_to_join(
    organization_id: str,
    body: JoinBody | None = None,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user),
):
    """Ask to be verified as an E-board member. An admin verifies you before
    you can book rooms for the organization."""
    org = db.get(Organization, as_uuid(organization_id, "organization"))
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    existing = db.get(OrganizationMember, (user.id, org.id))
    today = dt.date.today()
    if existing is not None:
        if existing.term_end is None or existing.term_end >= today:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="You're already a member (or waiting for verification).")
        db.delete(existing)  # an expired term: start a fresh membership
        db.flush()
    m = OrganizationMember(user_id=user.id, organization_id=org.id, term_start=today,
                           position_title=body.position_title if body else None)
    db.add(m)
    notify_admins_of_join(db, user, org)
    db.commit()
    db.refresh(m)
    return membership_out(m)
