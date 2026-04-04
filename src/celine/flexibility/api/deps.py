from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from celine.sdk.auth import JwtUser
from celine.flexibility.db import get_session
from celine.flexibility.security.auth import get_user_from_request, get_service_token

UserDep = Annotated[JwtUser, Depends(get_user_from_request)]
ServiceDep = Annotated[JwtUser, Depends(get_service_token)]
DbDep = Annotated[AsyncSession, Depends(get_session)]
