"""First-run onboarding endpoints (``/api/v1/setup/*``).

Unauthenticated by necessity — they run before any account exists. They are
locked the moment the instance is installed (an admin exists), and can be
additionally gated by a ``SETUP_TOKEN`` env for internet-exposed deployments.

Shares its whole implementation with the CLI (``make setup``) via
:class:`~apps.api.api.services.setup.setup_service.SetupService`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apps.api.api.services.setup.setup_service import SetupError, SetupService
from libs.log_manager.controller import LoggingController


class DbTestRequest(BaseModel):
    database_url: str = Field(..., min_length=1)
    setup_token: str | None = None


class InitRequest(BaseModel):
    admin_email: str = Field(..., min_length=3)
    admin_password: str = Field(..., min_length=8)
    first_name: str | None = None
    database_url: str | None = None
    email_mode: str | None = None
    setup_token: str | None = None


class SetupRouter:
    def __init__(self) -> None:
        self.logger = LoggingController(app_name="SetupRouter")
        self.router = APIRouter(prefix="/setup", tags=["setup"])
        self.service = SetupService()
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("/status", summary="Onboarding status (always available)")(self.status)
        self.router.post("/database/test", summary="Validate a database URL")(self.test_database)
        self.router.post("/init", status_code=201, summary="Create the first admin + persist config")(self.init)

    async def status(self) -> dict:
        return self.service.status()

    async def test_database(self, body: DbTestRequest) -> dict:
        try:
            self.service.check_token(body.setup_token)
            self.service.ensure_not_installed()
        except SetupError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return self.service.test_database(body.database_url)

    async def init(self, body: InitRequest) -> dict:
        try:
            result = self.service.initialize(
                admin_email=body.admin_email,
                admin_password=body.admin_password,
                first_name=body.first_name,
                database_url=body.database_url,
                email_mode=body.email_mode,
                setup_token=body.setup_token,
            )
        except SetupError as e:
            # 409 Conflict — already installed, bad token, or duplicate admin.
            raise HTTPException(status_code=409, detail=str(e)) from e
        self.logger.log_info(
            "Instance initialized via setup",
            {"admin_email": result["admin_email"],
             "restart_required": result["restart_required"]},
        )
        return result
