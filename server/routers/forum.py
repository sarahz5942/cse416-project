"""Collaboration forum (FR-9): posts with replies.

A top-level post has a title and a category; a reply is a ForumPost whose
`parent_id` points at the post (models.communication). Replies don't nest.
A post or reply can be made "as" an organization the author is a verified
member of (admins may pick any).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Security, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, selectinload

from authentication import get_current_user
from database import get_db
from models.communication import ForumCategory, ForumPost
from models.user import User
from services.pagination import Page, paged
from services.permissions import as_uuid, require_member

router = APIRouter(prefix="/forum", tags=["forum"])


# =============================================================================
# Schemas
# =============================================================================

class ReplyOut(BaseModel):
    id: str
    body: str
    author_id: str | None            # None if the author's account was deleted
    author_name: str | None
    organization_id: str | None
    organization_name: str | None
    created_at: dt.datetime


class PostOut(BaseModel):
    id: str
    title: str
    body: str
    category: ForumCategory
    author_id: str | None
    author_name: str | None
    organization_id: str | None
    organization_name: str | None
    created_at: dt.datetime
    replies: list[ReplyOut]          # oldest first


def _text(v: str, what: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError(f"{what} can't be empty.")
    return v


class PostIn(BaseModel):
    title: str = Field(max_length=255)
    body: str = Field(max_length=10000)
    category: ForumCategory = ForumCategory.GENERAL
    organization_id: str | None = Field(None, description="Post as this organization (you must be a verified member)")

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _text(v, "A title")

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _text(v, "A post")


class ReplyIn(BaseModel):
    body: str = Field(max_length=5000)
    organization_id: str | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _text(v, "A reply")


def reply_out(p: ForumPost) -> ReplyOut:
    return ReplyOut(id=str(p.id), body=p.body,
                    author_id=str(p.author_id) if p.author_id else None,
                    author_name=p.author.name if p.author else None,
                    organization_id=str(p.organization_id) if p.organization_id else None,
                    organization_name=p.organization.name if p.organization else None,
                    created_at=p.created_at)


def post_out(p: ForumPost, replies: list[ForumPost]) -> PostOut:
    return PostOut(id=str(p.id), title=p.title or "", body=p.body, category=p.category,
                   author_id=str(p.author_id) if p.author_id else None,
                   author_name=p.author.name if p.author else None,
                   organization_id=str(p.organization_id) if p.organization_id else None,
                   organization_name=p.organization.name if p.organization else None,
                   created_at=p.created_at, replies=[reply_out(r) for r in replies])


def _load():
    return (selectinload(ForumPost.author), selectinload(ForumPost.organization))


def _posts_out(db: Session, posts: list[ForumPost]) -> list[PostOut]:
    """Attach replies by parent_id. (ForumPost.replies isn't used: as declared in
    the model it points at the parent, not the children.)"""
    by_post: dict = {p.id: [] for p in posts}
    if posts:
        for r in (db.query(ForumPost).filter(ForumPost.parent_id.in_(by_post))
                  .order_by(ForumPost.created_at).options(*_load()).all()):
            by_post[r.parent_id].append(r)
    return [post_out(p, by_post[p.id]) for p in posts]


def _get_post(db: Session, post_id: str) -> ForumPost:
    post = (db.query(ForumPost)
            .filter(ForumPost.id == as_uuid(post_id, "post"), ForumPost.parent_id.is_(None))
            .options(*_load()).first())
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    return post


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=list[PostOut])
def list_posts(
    response: Response,
    category: ForumCategory | None = Query(None, description="Only this category"),
    page: Page = Depends(paged(default_limit=100, max_limit=200)),
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["read:forum"]),
):
    """Top-level posts with their replies, newest first.
    Paginate with ?limit=&offset= (the total is in the X-Total-Count header)."""
    q = db.query(ForumPost).filter(ForumPost.parent_id.is_(None))
    if category is not None:
        q = q.filter(ForumPost.category == category)
    q = q.order_by(ForumPost.created_at.desc(), ForumPost.id).options(*_load())
    posts = page.query(q, response).all()
    return _posts_out(db, posts)


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    body: PostIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["post:forum"]),
):
    """Start a new post, optionally on behalf of one of your organizations."""
    org_id = require_member(db, user, body.organization_id) if body.organization_id else None
    post = ForumPost(author_id=user.id, organization_id=org_id, title=body.title,
                     body=body.body, category=body.category)
    db.add(post)
    db.commit()
    return _posts_out(db, [_get_post(db, str(post.id))])[0]


@router.post("/{post_id}/replies", response_model=ReplyOut, status_code=status.HTTP_201_CREATED)
def create_reply(
    post_id: str,
    body: ReplyIn,
    db: Session = Depends(get_db),
    user: User = Security(get_current_user, scopes=["post:forum"]),
):
    """Reply to a post (not to another reply)."""
    parent = _get_post(db, post_id)
    org_id = require_member(db, user, body.organization_id) if body.organization_id else None
    reply = ForumPost(author_id=user.id, organization_id=org_id, parent_id=parent.id,
                      body=body.body, category=parent.category)
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply_out(reply)
