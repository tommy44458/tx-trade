"""Retired credential import endpoint: local connections must be reentered."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/settings", tags=["Local settings"])


@router.post("/migrate-credentials")
def migrate_credentials():
    raise HTTPException(410, {
        "code": "CREDENTIAL_REENTRY_REQUIRED",
        "message": "請在設定重新輸入金鑰或重新登入。舊加密資料已保留，不會存取系統金鑰圈。",
    })
