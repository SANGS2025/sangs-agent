from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
import os, psycopg

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Env / Settings ---
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ISSUER = os.getenv("JWT_ISSUER", "SANGS-JARVIS")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "SANGS-STAFF")
JWT_ACCESS_TTL_SECONDS = int(os.getenv("JWT_ACCESS_TTL_SECONDS", "3600"))
JWT_REFRESH_TTL_SECONDS = int(os.getenv("JWT_REFRESH_TTL_SECONDS", "1209600"))  # 14 days

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is missing. Add it to .env")

_db_url = os.getenv("DATABASE_URL")
if not _db_url:
    raise RuntimeError("DATABASE_URL is missing. Add it to .env")
# psycopg expects plain 'postgresql://' (no '+psycopg')
DB_URL = _db_url.replace("+psycopg", "")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# --- Models ---
class LoginIn(BaseModel):
    email: str
    password: str

class TokenOut(BaseModel):
    token_type: str = "bearer"
    access_token: str
    refresh_token: str
    expires_in: int

# --- DB helpers ---
def db_connect():
    # short-lived connection per request (simple & safe)
    return psycopg.connect(DB_URL)

def authenticate_user(email: str, password: str):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, role, password_hash FROM users WHERE email=%s",
            (email,)
        )
        row = cur.fetchone()
        if not row:
            return None
        uid, em, role, ph = row
        if not ph:
            return None
        if not pwd_ctx.verify(password, ph):
            return None
        return {"id": uid, "email": em, "role": role}

# --- JWT helpers ---
def _now():
    return datetime.now(timezone.utc)

def create_jwt(sub: str, role: str, ttl_seconds: int):
    now = _now()
    payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": sub,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str):
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=["HS256"],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )

# --- Dependency to require a user ---
def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer)
):
    token = None
    # 1) Authorization: Bearer <token>
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials
    # 2) Fallback to httpOnly cookie (if set by login)
    elif "access_token" in request.cookies:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid/expired token")

    return {"email": payload.get("sub"), "role": payload.get("role")}

# --- Routes ---
@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, response: Response):
    user = authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access = create_jwt(user["email"], user["role"], JWT_ACCESS_TTL_SECONDS)
    refresh = create_jwt(user["email"], user["role"], JWT_REFRESH_TTL_SECONDS)

    # Optional: also set httpOnly cookies so the web UI can rely on them
    response.set_cookie(
        key="access_token",
        value=access,
        httponly=True,
        secure=False,  # set True in production (HTTPS)
        samesite="lax",
        max_age=JWT_ACCESS_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=False,  # set True in production
        samesite="lax",
        max_age=JWT_REFRESH_TTL_SECONDS,
        path="/",
    )

    return TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=JWT_ACCESS_TTL_SECONDS,
    )

@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response):
    # accept token from cookie or Authorization header
    token = request.cookies.get("refresh_token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    try:
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid/expired refresh token")

    email = payload.get("sub")
    role  = payload.get("role") or "staff"

    access = create_jwt(email, role, JWT_ACCESS_TTL_SECONDS)
    refresh_tok = create_jwt(email, role, JWT_REFRESH_TTL_SECONDS)

    response.set_cookie("access_token", access, httponly=True, secure=False, samesite="lax", max_age=JWT_ACCESS_TTL_SECONDS, path="/")
    response.set_cookie("refresh_token", refresh_tok, httponly=True, secure=False, samesite="lax", max_age=JWT_REFRESH_TTL_SECONDS, path="/")

    return TokenOut(
        access_token=access,
        refresh_token=refresh_tok,
        expires_in=JWT_ACCESS_TTL_SECONDS,
    )

@router.post("/logout")
def logout(response: Response):
    # clear cookies
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}
