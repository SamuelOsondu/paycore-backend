from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    kyc,
    merchants,
    transactions,
    transfers,
    users,
    wallets,
    webhooks,
    withdrawals,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(wallets.router)
api_router.include_router(transactions.router)
api_router.include_router(kyc.router)
api_router.include_router(admin.router)
api_router.include_router(transfers.router)
api_router.include_router(merchants.router)
api_router.include_router(webhooks.router)
api_router.include_router(withdrawals.router)
