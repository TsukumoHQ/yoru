# ⚡ Redis Integration - The Lightning-Fast Memory Cache

## What is this?
This is our **high-speed memory system** - like having a super-fast notebook where you can instantly write down and retrieve any information. Think of it as the difference between having information on your desk (Redis) versus having to walk to a filing cabinet (database) every time you need something.

## 🎯 What Does It Do?

### For the System:
- **Speed Boost** - Makes everything faster by keeping frequently used data in memory
- **Session Management** - Remembers user sessions and preferences
- **Task Queues** - Manages background job queues efficiently
- **Real-time Data** - Stores temporary data that needs instant access
- **Rate Limiting** - Controls how often users can make requests

### For Users:
- **Instant Response** - Pages load faster because data is already in memory
- **Seamless Experience** - System remembers your preferences and state
- **Real-time Updates** - Live notifications and status updates
- **Smooth Performance** - No delays when accessing frequently used information

## 🚀 How to Use It

### Basic Caching Operations
```python
from app.libs.redis.redis import RedisClient

# Initialize Redis client
cache = RedisClient()

# Store data with expiration
cache.set("user_session_123", {
    "user_id": "user123",
    "client_shortname": "ATONRA",
    "login_time": "2024-01-01T12:00:00Z",
    "preferences": {"theme": "dark", "language": "en"}
}, expire_seconds=3600)  # Expires in 1 hour

# Retrieve data
session_data = cache.get("user_session_123")
if session_data:
    print(f"User logged in as: {session_data['user_id']}")

# Check if key exists
if cache.exists("user_session_123"):
    print("User session is active")
```

### Client-Specific Caching
```python
# Cache client data with proper naming
def cache_client_data(client_shortname, data):
    cache_key = f"client:{client_shortname}:data"
    cache.set(cache_key, data, expire_seconds=1800)  # 30 minutes

# Cache financial summaries
def cache_financial_summary(client_shortname, period, summary):
    cache_key = f"financial:{client_shortname}:{period}"
    cache.set(cache_key, summary, expire_seconds=3600)  # 1 hour

# Usage
cache_client_data("ATONRA", {
    "client_id": 123500,
    "client_name": "Atonra Partners SA",
    "status": "active"
})

cache_financial_summary("ATONRA", "2024-01", {
    "total_revenue": 15000.00,
    "total_expenses": 8000.00,
    "profit": 7000.00
})
```

### Advanced Caching Patterns
```python
# Cache with automatic refresh
def get_or_refresh_client_data(client_shortname):
    cache_key = f"client:{client_shortname}:full_data"
    
    # Try to get from cache first
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
    
    # If not in cache, fetch from database
    from app.libs.supabase.supabase import SupabaseClient
    db = SupabaseClient()
    fresh_data = db.get_client_by_shortname(client_shortname)
    
    # Store in cache for future requests
    cache.set(cache_key, fresh_data, expire_seconds=600)  # 10 minutes
    return fresh_data

# Bulk caching operations
def cache_multiple_items(items_dict, prefix="", expire_seconds=3600):
    """Cache multiple items at once"""
    pipe = cache.pipeline()
    for key, value in items_dict.items():
        full_key = f"{prefix}:{key}" if prefix else key
        pipe.set(full_key, value, expire_seconds)
    pipe.execute()

# Usage
client_data = {
    "ATONRA": {"balance": 5000.00, "status": "active"},
    "CLIENTTWO": {"balance": 3000.00, "status": "pending"}
}
cache_multiple_items(client_data, prefix="client_summary")
```

### Session Management
```python
# User session handling
class SessionManager:
    def __init__(self):
        self.cache = RedisClient()
        self.session_timeout = 7200  # 2 hours

    def create_session(self, user_id, client_shortname):
        session_id = f"session:{user_id}:{int(time.time())}"
        session_data = {
            "user_id": user_id,
            "client_shortname": client_shortname,
            "created_at": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat()
        }
        
        self.cache.set(session_id, session_data, expire_seconds=self.session_timeout)
        return session_id

    def get_session(self, session_id):
        return self.cache.get(session_id)

    def update_activity(self, session_id):
        session_data = self.cache.get(session_id)
        if session_data:
            session_data["last_activity"] = datetime.now().isoformat()
            self.cache.set(session_id, session_data, expire_seconds=self.session_timeout)

    def destroy_session(self, session_id):
        self.cache.delete(session_id)
```

### Task Queue Management
```python
# Background job queue using Redis
class TaskQueue:
    def __init__(self, queue_name="default"):
        self.cache = RedisClient()
        self.queue_name = f"queue:{queue_name}"

    def enqueue_task(self, task_data, priority="normal"):
        task_id = f"task:{int(time.time())}:{hash(str(task_data))}"
        
        # Store task data
        self.cache.set(f"task_data:{task_id}", task_data, expire_seconds=86400)  # 24 hours
        
        # Add to queue based on priority
        queue_key = f"{self.queue_name}:{priority}"
        self.cache.lpush(queue_key, task_id)
        
        return task_id

    def dequeue_task(self, priority="normal"):
        queue_key = f"{self.queue_name}:{priority}"
        task_id = self.cache.rpop(queue_key)
        
        if task_id:
            task_data = self.cache.get(f"task_data:{task_id}")
            return task_id, task_data
        return None, None

    def get_queue_size(self, priority="normal"):
        queue_key = f"{self.queue_name}:{priority}"
        return self.cache.llen(queue_key)

# Usage
task_queue = TaskQueue("financial_reports")

# Add task to queue
task_id = task_queue.enqueue_task({
    "script_name": "monthly_report",
    "client_shortname": "ATONRA",
    "period": "2024-01"
}, priority="high")

# Process tasks
task_id, task_data = task_queue.dequeue_task("high")
if task_data:
    print(f"Processing task: {task_data}")
```

### Rate Limiting
```python
# API rate limiting with Redis
class RateLimiter:
    def __init__(self):
        self.cache = RedisClient()

    def is_allowed(self, identifier, limit=100, window=3600):
        """
        Check if request is allowed based on rate limits
        
        Args:
            identifier: User/client identifier
            limit: Maximum requests allowed
            window: Time window in seconds
        """
        key = f"rate_limit:{identifier}"
        current_time = int(time.time())
        
        # Use sliding window with sorted sets
        pipe = self.cache.pipeline()
        
        # Remove old entries
        pipe.zremrangebyscore(key, 0, current_time - window)
        
        # Count current requests
        pipe.zcard(key)
        
        # Add current request
        pipe.zadd(key, {str(current_time): current_time})
        
        # Set expiration
        pipe.expire(key, window)
        
        results = pipe.execute()
        request_count = results[1]
        
        return request_count < limit

    def get_remaining_requests(self, identifier, limit=100, window=3600):
        key = f"rate_limit:{identifier}"
        current_time = int(time.time())
        
        # Clean old entries and count current
        pipe = self.cache.pipeline()
        pipe.zremrangebyscore(key, 0, current_time - window)
        pipe.zcard(key)
        results = pipe.execute()
        
        current_count = results[1]
        return max(0, limit - current_count)

# Usage
rate_limiter = RateLimiter()

def api_endpoint_with_rate_limiting(client_shortname):
    if not rate_limiter.is_allowed(f"client:{client_shortname}", limit=1000, window=3600):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # Process the request
    return {"status": "success"}
```

## 🔧 Configuration

### Environment Variables
```bash
# Redis Connection
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password
REDIS_DB=0

# Connection Pool
REDIS_POOL_SIZE=10
REDIS_MAX_CONNECTIONS=20
REDIS_SOCKET_TIMEOUT=30
REDIS_SOCKET_CONNECT_TIMEOUT=30

# Cache Settings
REDIS_DEFAULT_TTL=3600
REDIS_MAX_TTL=86400
REDIS_KEY_PREFIX=yoru

# Clustering (if using Redis Cluster)
REDIS_CLUSTER_NODES=redis1:6379,redis2:6379,redis3:6379
REDIS_CLUSTER_ENABLED=false
```

### Redis Client Configuration
```python
# Custom Redis configuration
redis_config = {
    "host": os.environ.get("REDIS_HOST", "localhost"),
    "port": int(os.environ.get("REDIS_PORT", 6379)),
    "password": os.environ.get("REDIS_PASSWORD"),
    "db": int(os.environ.get("REDIS_DB", 0)),
    "decode_responses": True,
    "socket_timeout": 30,
    "socket_connect_timeout": 30,
    "retry_on_timeout": True,
    "health_check_interval": 30
}

cache = RedisClient(**redis_config)
```

## 📊 Cache Performance Monitoring

### Cache Statistics
```python
# Monitor cache performance
def get_cache_stats():
    info = cache.info()
    return {
        "memory_used": info.get("used_memory_human"),
        "memory_peak": info.get("used_memory_peak_human"),
        "hits": info.get("keyspace_hits", 0),
        "misses": info.get("keyspace_misses", 0),
        "hit_rate": calculate_hit_rate(info),
        "connected_clients": info.get("connected_clients", 0),
        "uptime": info.get("uptime_in_seconds", 0)
    }

def calculate_hit_rate(info):
    hits = info.get("keyspace_hits", 0)
    misses = info.get("keyspace_misses", 0)
    total = hits + misses
    return (hits / total * 100) if total > 0 else 0

# Cache key analysis
def analyze_cache_keys():
    keys = cache.keys("*")
    analysis = {}
    
    for key in keys:
        key_type = cache.type(key)
        ttl = cache.ttl(key)
        memory_usage = cache.memory_usage(key) if hasattr(cache, 'memory_usage') else 0
        
        prefix = key.split(':')[0] if ':' in key else 'no_prefix'
        
        if prefix not in analysis:
            analysis[prefix] = {
                "count": 0,
                "total_memory": 0,
                "types": {},
                "ttl_distribution": {"persistent": 0, "temporary": 0}
            }
        
        analysis[prefix]["count"] += 1
        analysis[prefix]["total_memory"] += memory_usage
        analysis[prefix]["types"][key_type] = analysis[prefix]["types"].get(key_type, 0) + 1
        
        if ttl == -1:
            analysis[prefix]["ttl_distribution"]["persistent"] += 1
        else:
            analysis[prefix]["ttl_distribution"]["temporary"] += 1
    
    return analysis
```

## 🚨 Troubleshooting

### Connection Issues
```python
# Test Redis connection
def test_redis_connection():
    try:
        # Test basic connectivity
        cache.ping()
        print("✅ Redis connected successfully")
        
        # Test read/write operations
        test_key = "health_check_test"
        cache.set(test_key, "test_value", expire_seconds=60)
        value = cache.get(test_key)
        cache.delete(test_key)
        
        if value == "test_value":
            print("✅ Redis read/write operations working")
        else:
            print("❌ Redis read/write operations failed")
            
        return True
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False
```

### Memory Management
```python
# Monitor and manage Redis memory
def manage_redis_memory():
    info = cache.info()
    memory_used = info.get("used_memory")
    max_memory = info.get("maxmemory", 0)
    
    if max_memory > 0:
        memory_usage_percent = (memory_used / max_memory) * 100
        
        if memory_usage_percent > 90:
            print("⚠️ Redis memory usage high - cleaning expired keys")
            cache.execute_command("MEMORY", "PURGE")
            
        if memory_usage_percent > 95:
            print("🚨 Redis memory critical - clearing temporary caches")
            # Clear temporary caches
            temp_keys = cache.keys("temp:*")
            if temp_keys:
                cache.delete(*temp_keys)
```

### Cache Warming
```python
# Warm up cache with frequently accessed data
def warm_cache():
    """Pre-load frequently accessed data into cache"""
    from app.libs.supabase.supabase import SupabaseClient
    db = SupabaseClient()
    
    # Cache active clients
    active_clients = db.get_active_clients()
    for client in active_clients:
        cache_key = f"client:{client['client_shortname']}:data"
        cache.set(cache_key, client, expire_seconds=1800)
    
    print(f"Warmed cache with {len(active_clients)} client records")
    
    # Cache common configurations
    config_data = {
        "api_limits": {"requests_per_hour": 1000},
        "features": {"ai_enabled": True, "reports_enabled": True}
    }
    cache.set("system:config", config_data, expire_seconds=3600)
    
    print("Cache warming completed")
```

## 📁 File Structure
```
redis/
├── redis.py            # Main Redis client implementation
├── cache.py            # High-level caching utilities
├── session.py          # Session management
├── queue.py            # Task queue implementation
├── rate_limiter.py     # Rate limiting utilities
└── __init__.py         # Package initialization
```

## 🤝 Integration Examples

### With API Middleware
```python
# Cache middleware for FastAPI
from fastapi import Request, Response

async def cache_middleware(request: Request, call_next):
    # Check if response is cached
    cache_key = f"api:{request.method}:{request.url.path}:{str(request.query_params)}"
    cached_response = cache.get(cache_key)
    
    if cached_response:
        return Response(
            content=cached_response["content"],
            status_code=cached_response["status_code"],
            headers=cached_response["headers"]
        )
    
    # If not cached, process request
    response = await call_next(request)
    
    # Cache successful responses
    if response.status_code == 200:
        response_data = {
            "content": response.body,
            "status_code": response.status_code,
            "headers": dict(response.headers)
        }
        cache.set(cache_key, response_data, expire_seconds=300)  # 5 minutes
    
    return response
```

### With Database Queries
```python
# Cache database query results
def cached_query(cache_key, query_func, *args, **kwargs):
    """Generic function to cache database query results"""
    
    # Try cache first
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result
    
    # Execute query if not cached
    result = query_func(*args, **kwargs)
    
    # Cache the result
    cache.set(cache_key, result, expire_seconds=600)  # 10 minutes
    
    return result

# Usage
def get_client_invoices_cached(client_shortname):
    cache_key = f"invoices:{client_shortname}:all"
    return cached_query(
        cache_key,
        db.get_client_invoices,
        client_shortname
    )
```

This Redis integration is your speed boost system that makes everything lightning-fast!