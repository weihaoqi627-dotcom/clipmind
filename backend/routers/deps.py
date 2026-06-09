"""
FastAPI 依赖：从 JWT token 获取当前用户
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserStatus
from ..auth import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 Bearer token 解析当前用户"""
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供认证 token")

    user_id = decode_access_token(creds.credentials)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效或已过期")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")

    if user.status == UserStatus.DISABLED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账户已被禁用")

    return user
