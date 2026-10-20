"""Authentication endpoints: register, login, and current-user."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models.user import User, UserRole
from authentication import (
    authenticate_user,
    create_access_token,
    hash_password,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Request/response schemas (Pydantic, separate from the DB model) ---
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: UserRole

    class Config:
        from_attributes = True  # lets it read from the SQLAlchemy model


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Endpoints ---
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new account. New users default to the club_leader role,
    unverified; an admin verifies them and assigns their organization."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=UserRole.CLUB_LEADER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse(id=str(user.id), email=user.email, name=user.name, role=user.role)


@router.post("/token", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Log in with email (as the username field) and password; returns a token."""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user))


@router.get("/me", response_model=UserResponse)
def read_me(current_user: User = Depends(get_current_user)):
    """Return the currently logged-in user."""
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        role=current_user.role,
    )
