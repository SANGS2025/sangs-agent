# ~/sangs-agent/auth.py
import os
import time
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr
from jose import jwt, JWTError
from passlib.context import CryptContext

from db import pool  # use the shared pool

router = APIRouter(prefix="/auth", tags=["auth"])

# --- JWT config ---
JWT_SECRET   = os.getenv("JWT_SECRET", "changeme")
JWT_ISSUER   = os.getenv("JWT_ISSUER", "SANGS-JARVIS")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "SANGS-STAFF")
ACCESS_TTL   = int(os.getenv("JWT_ACCESS_TTL_SECONDS", "3600"))
REFRESH_TTL  = int(os.getenv("JWT_REFRESH_TTL_SECONDS", "1209600"))

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class SignupIn(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class TokenOut(BaseModel):
    token_type: str = "bearer"
    access_token: str
    refresh_token: str
    expires_in: int

def _fetch_user(email: str) -> Optional[dict]:
    sql = "SELECT id, email, role, password_hash FROM users WHERE email = %s LIMIT 1"
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (email.lower(),))
        return cur.fetchone()

def _issue_tokens(email: str, role: str) -> TokenOut:
    now = int(time.time())
    access_payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": email,
        "role": role,
        "iat": now,
        "exp": now + ACCESS_TTL,
    }
    refresh_payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": email,
        "role": role,
        "iat": now,
        "exp": now + REFRESH_TTL,
    }
    access  = jwt.encode(access_payload,  JWT_SECRET, algorithm="HS256")
    refresh = jwt.encode(refresh_payload, JWT_SECRET, algorithm="HS256")
    return TokenOut(access_token=access, refresh_token=refresh, expires_in=ACCESS_TTL)

@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn):
    user = _fetch_user(payload.email)
    if not user or not pwd_ctx.verify(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return _issue_tokens(user["email"], user["role"])

@router.post("/signup", response_model=TokenOut)
def signup(payload: SignupIn):
    """
    Public signup endpoint. Creates a new user with 'user' role by default.
    """
    # Check if user already exists
    existing = _fetch_user(payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Validate password strength
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    
    # Hash password
    password_hash = pwd_ctx.hash(payload.password)
    
    # Insert new user with 'user' role (public users, not admin/staff)
    sql = """
    INSERT INTO users (email, password_hash, role, full_name)
    VALUES (%s, %s, %s, %s)
    RETURNING id, email, role
    """
    try:
        with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (payload.email.lower(), password_hash, "user", payload.full_name))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=500, detail="Failed to create user")
            return _issue_tokens(user["email"], user["role"])
    except psycopg.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")

def require_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"email": payload.get("sub"), "role": payload.get("role")}

def require_admin(authorization: str = Header(None)) -> dict:
    user = require_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
