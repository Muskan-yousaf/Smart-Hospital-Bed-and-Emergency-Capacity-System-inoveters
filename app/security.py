import os, hashlib, hmac, secrets
from datetime import timedelta
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .database import get_db
from .models import User, utcnow

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-in-production")
ALGO = "HS256"
TOKEN_HOURS = 12
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"


def verify_password(password: str, stored: str) -> bool:
    if "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return hmac.compare_digest(h, check)


def create_token(user: User) -> str:
    payload = {"sub": str(user.id), "role": user.role, "exp": utcnow() + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    err = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
        user = db.get(User, int(data["sub"]))
    except Exception:
        raise err
    if not user or not user.is_active:
        raise err
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Allowed roles: {', '.join(roles)}")
        return user
    return checker
