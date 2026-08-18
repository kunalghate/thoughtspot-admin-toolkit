"""
Update availability.

GET /api/v1/update  — is a newer release available, and what does the user type?
"""

import logging

from fastapi import APIRouter

from ts_admin.services.update_service import UpdateCheck, check_for_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/update", tags=["update"])


@router.get("", response_model=UpdateCheck)
async def get_update_status(force: bool = False) -> UpdateCheck:
    """
    Compare the running version against the latest GitHub Release.

    Never fails: when GitHub is unreachable the response carries
    `checked=false` and the UI shows nothing at all.
    """
    return await check_for_update(force=force)
