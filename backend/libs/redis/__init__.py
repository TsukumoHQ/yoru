"""
Redis library for Yoru.

This module provides:
- RedisManager: Low-level Redis operations (queues, key-value operations) - ASYNC
- CacheManager: High-level caching interface for database results, API responses - SYNC
- Global connection pools shared across all instances (thread-safe, prevents connection exhaustion)

Usage Examples:

Basic caching (SYNC):
    from libs.redis import CacheManager

    cache = CacheManager()  # Uses global shared sync pool
    cache.set("db", "clients", client_data, ttl=3600)
    result = cache.get("db", "clients")

Queue operations (ASYNC):
    from libs.redis import RedisManager
    
    redis = RedisManager()  # Uses global shared async pool
    await redis.push_to_queue("scripts", job_data)
    job = await redis.pop_from_queue("scripts", decode_json=True)
    
Note: All instances share global ConnectionPools (async/sync).
This prevents "Too many connections" errors (max 100 connections per pool instead of 10 per instance).
"""

from .redis import (
    RedisManager,
    RedisConnectionError,
    RedisPermissionError,
    get_global_async_redis_pool,
    get_global_sync_redis_pool,
)
from .cache import CacheManager, CacheKeyError, CacheOperationError

__all__ = [
    # Core Redis functionality
    "RedisManager",
    "RedisConnectionError",
    "RedisPermissionError",
    # Cache functionality
    "CacheManager",
    "CacheKeyError",
    "CacheOperationError",
    # Global pools (for advanced usage)
    "get_global_async_redis_pool",
    "get_global_sync_redis_pool",
]
