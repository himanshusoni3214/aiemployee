from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
def hash_password(p: str) -> str: return pwd_context.hash(p)
def verify_password(p: str, h: str) -> bool: return pwd_context.verify(p, h)
def create_token(sub: str, role: str) -> str:
    exp = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({'sub': sub, 'role': role, 'exp': exp}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
