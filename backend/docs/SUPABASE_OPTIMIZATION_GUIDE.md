# Supabase Optimization Guide

## Overview

This guide provides **strict rules** for creating database views, materialized views, and caching strategies when building your SaaS application from this template.

**Key Principle:** Optimize only when proven slow. Profile first, optimize second.

---

## When to Create a Database View

### ✅ Create a View When:

1. **N+1 Query Pattern Detected** (CRITICAL)
   - Symptom: Loop iterating through records, making 1 query per iteration
   - Example: Fetching features for each subscription in a list
   - Solution: View with JOIN pre-computed

2. **Complex JOINs Used Repeatedly** (HIGH)
   - Symptom: Same 3-4 table JOIN pattern appears in 3+ services
   - Example: user → subscription → plan → features
   - Solution: Regular view to encapsulate JOIN logic

3. **Aggregations in Python Code** (MEDIUM)
   - Symptom: COUNT, SUM, AVG computed in service layer
   - Example: Counting group members in a loop
   - Solution: View with subquery aggregation

4. **Auth Data + Business Data Mix** (HIGH)
   - Symptom: Querying auth.users separately from profiles table
   - Example: Fetching email from auth.users after fetching profile
   - Solution: View joining profiles + auth.users

### ❌ Don't Create a View When:

1. Simple single-table queries (use direct table access)
2. Write-heavy tables (views add overhead on writes)
3. Complex filtering that varies per request (use parameterized queries)
4. Data that changes every second (caching won't help)

---

## View Types: Regular vs Materialized

### Regular View (Use 99% of the time)

**When:**
- Read:write ratio < 100:1
- Query joins 2-4 tables
- No expensive aggregations
- Near real-time data needed

**Pros:**
- Always up-to-date
- No storage overhead
- No refresh management

**Example:**
```sql
CREATE OR REPLACE VIEW user_profiles_with_email AS
SELECT p.*, u.email
FROM profiles p
JOIN auth.users u ON p.id = u.id;
```

---

### Materialized View (Rare, high-impact only)

**When:**
- Read:write ratio > 1000:1
- Complex aggregations (GROUP BY, multiple JOINs)
- Data updated infrequently (< 1/minute)
- Query takes >200ms normally

**Pros:**
- Pre-computed results (fast reads)
- Can be indexed
- Handles complex aggregations

**Cons:**
- Storage overhead
- Requires refresh strategy
- Potential staleness

**Example:**
```sql
CREATE MATERIALIZED VIEW user_subscription_details AS
SELECT s.id, s.user_id, /* complex query with GROUP BY */;

CREATE UNIQUE INDEX idx_mat_view_user
    ON user_subscription_details(user_id);
```

**Refresh Strategy:**
```sql
-- Trigger-based (recommended)
CREATE TRIGGER refresh_on_change
  AFTER INSERT OR UPDATE OR DELETE ON subscriptions
  FOR EACH STATEMENT
  EXECUTE FUNCTION refresh_materialized_view_fn();
```

---

## Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| User-scoped | `user_{domain}_{detail}` | `user_active_grants_enriched` |
| Domain-scoped | `{domain}_with_{enrichment}` | `user_groups_with_counts` |
| Materialized | `{domain}_details` | `user_subscription_details` |

**Rules:**
- Prefix with main entity (`user_`, `organization_`, `plan_`)
- Suffix describes enrichment (`_with_counts`, `_enriched`, `_details`)
- Use snake_case (PostgreSQL convention)
- Max 50 characters

---

## Caching Strategy Rules

### Cache TTL Guidelines

| Data Type | TTL | Reason |
|-----------|-----|--------|
| User profile | 30 min | Changes infrequently |
| Subscription details | 30 min | Plan changes rare |
| RBAC feature access | 5 min | Permission changes need quick propagation |
| Count operations | 5 min | Approximations acceptable |
| Real-time data | No cache | Must be accurate |

### Cache Invalidation Rules

**CRITICAL:** Never use table-wide invalidation for high-write tables.

```python
# ❌ BAD: Invalidates ALL cached records on ANY write
def _invalidate_table_cache(table):
    cache.delete_pattern(f"{table}:*")

# ✅ GOOD: Invalidates specific record + related queries
def _invalidate_record_cache(table, record_id, user_id=None):
    # Specific record
    cache.delete(f"{table}:record:{record_id}")
    # Related list queries (reasonable scope)
    cache.delete_pattern(f"{table}:query:user:{user_id}:*")
```

**Invalidation Triggers:**

| Table Changed | Invalidate |
|---------------|------------|
| `user_grants` | `rbac:*:{user_id}:*` |
| `subscriptions` | `rbac:*:{user_id}:*`, `user_subscription_details` (refresh mat view) |
| `user_group_members` | `rbac:*:{user_id}:*`, `user_groups:*:{user_id}:*` |

---

## Migration File Template

All view changes MUST go in versioned migration files:

**File:** `template/migrations/{YYYYMMDD}_{description}.sql`

```sql
-- Migration: Add user_active_grants_enriched view
-- Description: Eliminates N+1 query pattern in grant_service.py
-- Date: 2026-01-18

-- ===== UP MIGRATION =====

-- Create view
CREATE OR REPLACE VIEW user_active_grants_enriched AS
SELECT
    ug.id,
    ug.user_id,
    ug.feature_id,
    ug.value,
    f.key AS feature_key,
    f.name AS feature_name
FROM user_grants ug
JOIN features f ON ug.feature_id = f.id
WHERE ug.expires_at IS NULL OR ug.expires_at > NOW();

-- Create index on filtered base table
CREATE INDEX IF NOT EXISTS idx_user_grants_active
    ON user_grants(user_id)
    WHERE expires_at IS NULL OR expires_at > NOW();

-- ===== DOWN MIGRATION (ROLLBACK) =====
-- DROP VIEW IF EXISTS user_active_grants_enriched;
-- DROP INDEX IF EXISTS idx_user_grants_active;
```

---

## Testing Checklist

Before deploying any view:

### 1. Performance Testing

```sql
-- Compare query times
EXPLAIN ANALYZE SELECT * FROM user_grants WHERE user_id = 'test-uuid';
-- vs
EXPLAIN ANALYZE SELECT * FROM user_active_grants_enriched WHERE user_id = 'test-uuid';
```

**Target:** View query should be ≤ 10% slower than direct table access

### 2. Data Consistency

```sql
-- Verify row counts match expectations
SELECT COUNT(*) FROM user_active_grants_enriched;
SELECT COUNT(*) FROM user_grants WHERE expires_at IS NULL OR expires_at > NOW();
-- Should match
```

### 3. Service Integration

```python
# Unit test with mock
def test_list_user_grants_uses_view():
    service = GrantService(supabase=mock_supabase)
    service.list_user_grants(user_id, correlation_id)

    # Verify view was queried
    mock_supabase.query_records.assert_called_with(
        "user_active_grants_enriched",
        filters={"user_id": str(user_id)},
        correlation_id=correlation_id
    )
```

---

## Common Patterns

### Pattern 1: Enriched List View

**Problem:** Listing entities requires N+1 queries for related data

**Solution:**
```sql
CREATE OR REPLACE VIEW {entity}_enriched AS
SELECT
    e.*,
    r.field1 AS related_field1
FROM {entity} e
LEFT JOIN {related} r ON e.related_id = r.id
WHERE e.deleted_at IS NULL;
```

---

### Pattern 2: User-Scoped Aggregation

**Problem:** Counting/summing related records per user

**Solution:**
```sql
CREATE OR REPLACE VIEW user_{entity}_summary AS
SELECT
    user_id,
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE status = 'active') AS active_count
FROM {entity}
WHERE deleted_at IS NULL
GROUP BY user_id;
```

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: View on View on View

```sql
-- BAD: 3 levels of views
CREATE VIEW layer3 AS SELECT * FROM layer2;
CREATE VIEW layer2 AS SELECT * FROM layer1;
CREATE VIEW layer1 AS SELECT * FROM base_table;
```

**Why bad:** Query planner struggles, debugging impossible

**Fix:** Flatten into single view

---

### ❌ Anti-Pattern 2: SELECT * in Views

```sql
-- BAD
CREATE VIEW user_profiles AS SELECT * FROM profiles;
```

**Why bad:** Schema changes break view, too many columns transferred

**Fix:** Explicit column list

---

## Monitoring & Alerts

### Metrics to Track

```python
# Add to logging context
{
    "query_count": <number>,
    "cache_hit_rate": <percentage>,
    "view_query_time_ms": <ms>,
}
```

### Alerts to Set Up

1. **Cache hit rate drops below 70%**
   - Trigger: 5-minute average <70%
   - Action: Review cache TTLs

2. **View query time >500ms**
   - Trigger: P95 latency >500ms
   - Action: Review indexes, consider materialized view

---

## Getting Help

**Questions to ask:**

1. Does this query pattern appear in 3+ places? → Consider view
2. Is this data read 100x more than written? → View is good
3. Are there complex JOINs or aggregations? → Regular view
4. Is the query still slow with a view? → Consider materialized view
5. Is data updated constantly? → Cache with short TTL or no cache

**When in doubt:** Start with direct queries, profile with real traffic, optimize with views only when proven slow.

---

## Version

**Version:** 1.0.0
**Date:** 2026-01-18
**Template:** Yoru
