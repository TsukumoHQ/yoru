"""
Service pour gestion des heartbeats utilisateurs.

Système de keep-alive :
- Users envoient un ping toutes les 15 minutes
- Après 4 pings manqués (1 heure), session invalidée
- Stockage Redis pour performance

Respect des règles :
- python.structure.mdc : Service stateless + DI
- python.logging.mdc : LoggingController avec correlation_id
- python.async.mdc : async def, await
- scale.state.mdc : Redis pour état volatile
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.redis.redis import RedisManager
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store


class HeartbeatService:
    """Service pour gestion des heartbeats utilisateurs."""

    # Configuration
    PING_INTERVAL_SECONDS = 15 * 60  # 15 minutes
    MAX_MISSED_PINGS = 4  # Après 4 pings manqués = déconnexion
    HEARTBEAT_TTL = PING_INTERVAL_SECONDS * (MAX_MISSED_PINGS + 1)  # TTL Redis avec marge

    # Redis keys
    HEARTBEAT_PREFIX = "heartbeat:user:"
    ACTIVE_USERS_SET = "heartbeat:active_users"

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        redis: RedisManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="HeartbeatService")
        # Heartbeat/presence is a Redis-backed nicety. A self-host instance with
        # no Redis must not 500 — degrade to "no presence info" instead.
        if redis is not None:
            self.redis = redis
        else:
            try:
                self.redis = RedisManager()
            except Exception as e:
                self.redis = None
                self.logger.log_warning(
                    "Redis unavailable — heartbeat/presence disabled",
                    {"component": "HeartbeatService", "error": str(e)},
                )

    def _get_heartbeat_key(self, user_id: UUID | str) -> str:
        """Construire la clé Redis pour un user."""
        return f"{self.HEARTBEAT_PREFIX}{user_id}"

    async def record_ping(
        self,
        user_id: UUID,
        correlation_id: str = "",
    ) -> dict:
        """
        Enregistrer un ping (heartbeat) pour un utilisateur.

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID

        Returns:
            dict avec next_ping_deadline et status
        """
        context = {
            "operation": "record_ping",
            "component": "HeartbeatService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_debug("Recording heartbeat ping", context)

        now = datetime.now(timezone.utc)
        if self.redis is None:
            # Presence disabled (no Redis) — acknowledge without persisting.
            return {
                "status": "ok",
                "presence": "disabled",
                "next_ping_deadline": None,
            }
        heartbeat_key = self._get_heartbeat_key(user_id)

        # Stocker le timestamp du dernier ping avec TTL
        heartbeat_data = {
            "user_id": str(user_id),
            "last_ping_at": now.isoformat(),
            "ping_count": 1,  # Reset à chaque ping
        }

        try:
            await self.redis.set_value(
                heartbeat_key,
                heartbeat_data,
                ex=self.HEARTBEAT_TTL,
                correlation_id=correlation_id,
            )
            # Ajouter à l'ensemble des users actifs
            await self.redis.client.sadd(self.ACTIVE_USERS_SET, str(user_id))
        except Exception:
            # Redis unreachable (self-host without Redis) → acknowledge the ping
            # without persisting presence; fall through to the normal response.
            self.logger.log_warning(
                "Heartbeat persist skipped — Redis unavailable", context
            )

        # Calculer deadline du prochain ping
        next_deadline = now.timestamp() + self.PING_INTERVAL_SECONDS
        disconnect_deadline = now.timestamp() + (self.PING_INTERVAL_SECONDS * self.MAX_MISSED_PINGS)

        self.logger.log_debug(
            "Heartbeat recorded",
            {**context, "next_ping_deadline": next_deadline},
        )

        return {
            "status": "ok",
            "last_ping_at": now.isoformat(),
            "next_ping_deadline": datetime.fromtimestamp(next_deadline, tz=timezone.utc).isoformat(),
            "disconnect_deadline": datetime.fromtimestamp(disconnect_deadline, tz=timezone.utc).isoformat(),
            "ping_interval_seconds": self.PING_INTERVAL_SECONDS,
            "max_missed_pings": self.MAX_MISSED_PINGS,
        }

    async def get_heartbeat_status(
        self,
        user_id: UUID,
        correlation_id: str = "",
    ) -> dict | None:
        """
        Récupérer le statut heartbeat d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID

        Returns:
            dict avec statut ou None si pas de heartbeat
        """
        context = {
            "operation": "get_heartbeat_status",
            "component": "HeartbeatService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }

        if self.redis is None:
            return None  # presence disabled (no Redis)

        heartbeat_key = self._get_heartbeat_key(user_id)
        try:
            heartbeat_data = await self.redis.get_value(
                heartbeat_key,
                decode_json=True,
                correlation_id=correlation_id,
            )
        except Exception:
            # Redis unreachable at call time (self-host without Redis) → degrade.
            return None

        if not heartbeat_data:
            self.logger.log_debug("No heartbeat found for user", context)
            return None

        # Calculer si le user est considéré actif
        last_ping_at = datetime.fromisoformat(heartbeat_data["last_ping_at"])
        now = datetime.now(timezone.utc)
        elapsed = (now - last_ping_at).total_seconds()
        missed_pings = int(elapsed // self.PING_INTERVAL_SECONDS)

        return {
            "user_id": heartbeat_data["user_id"],
            "last_ping_at": heartbeat_data["last_ping_at"],
            "elapsed_seconds": elapsed,
            "missed_pings": missed_pings,
            "is_active": missed_pings < self.MAX_MISSED_PINGS,
            "should_disconnect": missed_pings >= self.MAX_MISSED_PINGS,
        }

    async def get_inactive_users(
        self,
        correlation_id: str = "",
    ) -> list[str]:
        """
        Récupérer la liste des users inactifs (à déconnecter).

        Args:
            correlation_id: Request correlation ID

        Returns:
            Liste des user_ids à déconnecter
        """
        context = {
            "operation": "get_inactive_users",
            "component": "HeartbeatService",
            "correlation_id": correlation_id,
        }
        self.logger.log_info("Checking for inactive users", context)

        # Récupérer tous les users actifs enregistrés
        active_users = await self.redis.client.smembers(self.ACTIVE_USERS_SET)

        if not active_users:
            self.logger.log_debug("No active users registered", context)
            return []

        inactive_users = []
        now = datetime.now(timezone.utc)
        max_elapsed = self.PING_INTERVAL_SECONDS * self.MAX_MISSED_PINGS

        for user_id in active_users:
            heartbeat_key = self._get_heartbeat_key(user_id)
            heartbeat_data = await self.redis.get_value(
                heartbeat_key,
                decode_json=True,
                correlation_id=correlation_id,
            )

            if not heartbeat_data:
                # Heartbeat expiré (TTL Redis) = inactif
                inactive_users.append(user_id)
                continue

            last_ping_at = datetime.fromisoformat(heartbeat_data["last_ping_at"])
            elapsed = (now - last_ping_at).total_seconds()

            if elapsed >= max_elapsed:
                inactive_users.append(user_id)

        self.logger.log_info(
            f"Found {len(inactive_users)} inactive users",
            {**context, "inactive_count": len(inactive_users)},
        )

        return inactive_users

    async def disconnect_user(
        self,
        user_id: str,
        correlation_id: str = "",
    ) -> bool:
        """
        Déconnecter un utilisateur inactif.

        Actions :
        - Supprime le heartbeat Redis
        - Retire de l'ensemble des users actifs
        - Invalide la session Supabase (optionnel)

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID

        Returns:
            True si déconnexion réussie
        """
        context = {
            "operation": "disconnect_user",
            "component": "HeartbeatService",
            "correlation_id": correlation_id,
            "user_id": user_id,
        }
        self.logger.log_info("Disconnecting inactive user", context)

        try:
            # Supprimer heartbeat Redis
            heartbeat_key = self._get_heartbeat_key(user_id)
            await self.redis.delete_key(heartbeat_key, correlation_id=correlation_id)

            # Retirer de l'ensemble des users actifs
            await self.redis.client.srem(self.ACTIVE_USERS_SET, user_id)

            # Optionnel : Invalider la session Supabase
            # Note: Supabase ne supporte pas facilement l'invalidation de session
            # côté serveur. Le client devra re-authentifier au prochain appel.
            # Pour une vraie invalidation, il faudrait :
            # 1. Utiliser une blacklist de tokens dans Redis
            # 2. Ou forcer un refresh token côté Supabase (via admin API)

            self.logger.log_info("User disconnected successfully", context)
            return True

        except Exception as e:
            self.logger.log_error(
                "Failed to disconnect user",
                {**context, "error": str(e)},
            )
            return False

    async def cleanup_heartbeat(
        self,
        user_id: UUID,
        correlation_id: str = "",
    ) -> None:
        """
        Nettoyer le heartbeat lors d'une déconnexion volontaire (logout).

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID
        """
        context = {
            "operation": "cleanup_heartbeat",
            "component": "HeartbeatService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_debug("Cleaning up heartbeat on logout", context)

        heartbeat_key = self._get_heartbeat_key(user_id)
        await self.redis.delete_key(heartbeat_key, correlation_id=correlation_id)
        await self.redis.client.srem(self.ACTIVE_USERS_SET, str(user_id))
