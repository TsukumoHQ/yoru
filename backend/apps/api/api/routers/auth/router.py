"""Authentication router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from libs.log_manager.controller import LoggingController
from apps.api.api.dependencies.auth import get_correlation_id
from apps.api.api.models.auth.auth_models import (
    AuthResponse,
    RefreshRequest,
    SignInRequest,
    SignUpRequest,
)
from apps.api.api.services.auth.provider import AuthProvider, get_auth_provider


class AuthRouter:
    """Router for authentication endpoints."""

    def __init__(self):
        self.logger = LoggingController(app_name="AuthRouter")
        self.router = APIRouter(prefix="/auth", tags=["auth"])
        self.auth_service: AuthProvider | None = None
        self._setup_routes()

    def initialize_services(self):
        """Initialize required services."""
        self.auth_service = get_auth_provider()

    def get_router(self) -> APIRouter:
        """Get the FastAPI router."""
        return self.router

    def _setup_routes(self):
        """Set up auth routes."""
        self.router.post(
            "/signup",
            response_model=AuthResponse,
            status_code=201,
            summary="Register a new user",
        )(self.signup)

        self.router.post(
            "/signin",
            response_model=AuthResponse,
            status_code=200,
            summary="Sign in with email and password",
        )(self.signin)

        self.router.post(
            "/signout",
            status_code=200,
            summary="Sign out the current user",
        )(self.signout)

        self.router.post(
            "/refresh",
            response_model=AuthResponse,
            status_code=200,
            summary="Refresh access token",
        )(self.refresh)

    async def signup(
        self,
        request: Request,
        data: SignUpRequest,
        correlation_id: str = Depends(get_correlation_id),
    ) -> AuthResponse:
        """
        Register a new user.

        Creates a new user account and returns authentication tokens.

        **Request Body:**
        - **email**: Valid email address
        - **password**: Minimum 8 characters
        - **first_name**: Optional first name
        - **last_name**: Optional last name

        **Example Request:**
        ```bash
        curl -X POST http://localhost:8000/api/v1/auth/signup \\
          -H "Content-Type: application/json" \\
          -d '{
            "email": "user@example.com",
            "password": "SecureP@ssw0rd",
            "first_name": "John",
            "last_name": "Doe"
          }'
        ```

        **Returns:**
        - **access_token**: JWT token for authenticated requests (expires in 1 hour)
        - **refresh_token**: Token for refreshing access (expires in 7 days)
        - **user**: User profile information
        """
        return await self.auth_service.sign_up(data, correlation_id)

    async def signin(
        self,
        request: Request,
        data: SignInRequest,
        correlation_id: str = Depends(get_correlation_id),
    ) -> AuthResponse:
        """
        Sign in with email and password.

        Authenticates an existing user and returns fresh tokens.

        **Example Request:**
        ```bash
        curl -X POST http://localhost:8000/api/v1/auth/signin \\
          -H "Content-Type: application/json" \\
          -d '{
            "email": "user@example.com",
            "password": "SecureP@ssw0rd"
          }'
        ```

        **Example Response:**
        ```json
        {
          "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
          "token_type": "bearer",
          "expires_in": 3600,
          "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
          "user": {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "email": "user@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "role": "user"
          }
        }
        ```

        **Returns:**
        - **access_token**: JWT token for authenticated requests
        - **refresh_token**: Token for refreshing access
        - **user**: User profile information
        """
        return await self.auth_service.sign_in(data, correlation_id)

    async def signout(
        self,
        request: Request,
        correlation_id: str = Depends(get_correlation_id),
    ) -> dict:
        """
        Sign out the current user.

        Invalidates the current session.
        """
        # Get token from Authorization header if present
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "") if auth_header else ""

        await self.auth_service.sign_out(token, correlation_id)
        return {"message": "Successfully signed out"}

    async def refresh(
        self,
        request: Request,
        data: RefreshRequest,
        correlation_id: str = Depends(get_correlation_id),
    ) -> AuthResponse:
        """
        Refresh the access token using a refresh token.

        Returns new access token and refresh token.
        """
        return await self.auth_service.refresh_token(data, correlation_id)
