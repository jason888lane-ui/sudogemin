import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core import get_settings
bearer = HTTPBearer()
def current_tenant(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    s = get_settings()
    try:
        claims = jwt.decode(credentials.credentials, s.jwt_secret, algorithms=["HS256"],
            issuer=s.jwt_issuer, audience=s.jwt_audience,
            options={"require": ["exp", "sub", "tenant_id"]})
        tenant_id = claims.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id missing")
        return tenant_id
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc
