# 🗄️ Supabase Integration - The Database Gateway

## What is this?
This is our **main database connection hub** - the bridge between our application and our PostgreSQL database hosted on Supabase. Think of it as a smart translator that speaks both Python and database language, making it easy to store, retrieve, and manage all our business data.

## 🏗️ What's Inside?

### 📁 Core Files

#### `supabase.py` - The Database Client 🔌
**What it does**: Main interface for all database operations
- **Purpose**: Provides a simple, consistent way to interact with our PostgreSQL database
- **Features**: CRUD operations, client management, transaction handling, connection pooling
- **Real-world analogy**: Like a hotel concierge who can help you with any request

## 🎯 What Does It Do?

### For the System:
- **Data Persistence** - Stores all business data permanently and securely
- **Client Management** - Manages client information following Yoru ID rules
- **Transaction Handling** - Ensures data consistency and integrity
- **Performance Optimization** - Connection pooling and query optimization
- **Security** - Row-level security and proper authentication

### For Developers:
- **Simple API** - Easy-to-use methods for database operations
- **Type Safety** - Proper data validation and type checking
- **Error Handling** - Clear error messages and robust exception handling
- **Query Builder** - Intuitive query construction
- **Async Support** - Non-blocking database operations

### For Business:
- **Data Integrity** - Reliable storage of critical business information
- **Compliance** - Audit trails and data retention policies
- **Scalability** - Handles growing data volumes efficiently
- **Backup & Recovery** - Automated backups and point-in-time recovery

## 🚀 How to Use It

### Basic Database Operations
```python
from app.libs.supabase.supabase import SupabaseClient

# Initialize the database client
db = SupabaseClient()

# Create a new client record
client_data = {
    "client_id": 123500,
    "client_name": "Atonra Partners SA", 
    "client_shortname": "ATONRA",
    "client_bc_id": "2636270c-bf5b-ef11-a837-6045bd2afbd2",
    "client_hv_id": "6199779",
    "client_ab_id": "123500",
    "created_at": "2024-01-01T12:00:00Z",
    "status": "active"
}

# Insert client data
result = db.insert("clients", client_data)
print(f"Created client with ID: {result['client_id']}")
```

### Client Management (Following Yoru Rules)
```python
# Get client by shortname (most common lookup)
client = db.get_client_by_shortname("ATONRA")
print(f"Client: {client['client_name']}")

# Get client by internal ID
client = db.get_client_by_id(123500)

# Get client by Business Central ID
client = db.get_client_by_bc_id("2636270c-bf5b-ef11-a837-6045bd2afbd2")

# Update client information
updated_data = {"status": "inactive", "updated_at": "2024-01-01T12:00:00Z"}
db.update_client("ATONRA", updated_data)

# List all active clients
active_clients = db.get_active_clients()
```

### Advanced Queries
```python
# Search clients with filters
clients = db.query("clients") \
    .select("*") \
    .eq("status", "active") \
    .ilike("client_name", "%Partners%") \
    .order("created_at", desc=True) \
    .limit(10) \
    .execute()

# Get client financial summary
financial_data = db.query("transactions") \
    .select("amount, transaction_type, created_at") \
    .eq("client_shortname", "ATONRA") \
    .gte("created_at", "2024-01-01") \
    .lte("created_at", "2024-01-31") \
    .execute()

# Complex join query
client_with_transactions = db.query("clients") \
    .select("""
        *,
        transactions(amount, transaction_type, created_at)
    """) \
    .eq("client_shortname", "ATONRA") \
    .execute()
```

### Transaction Management
```python
# Use transactions for data consistency
async with db.transaction() as txn:
    try:
        # Create invoice
        invoice = await txn.insert("invoices", {
            "client_shortname": "ATONRA",
            "amount": 1500.00,
            "status": "pending"
        })
        
        # Create transaction record
        await txn.insert("transactions", {
            "client_shortname": "ATONRA",
            "invoice_id": invoice["id"],
            "amount": 1500.00,
            "transaction_type": "invoice_created"
        })
        
        # Update client balance
        await txn.execute_sql("""
            UPDATE clients 
            SET balance = balance + 1500.00 
            WHERE client_shortname = 'ATONRA'
        """)
        
        # Commit all changes together
        await txn.commit()
        
    except Exception as e:
        # Rollback on any error
        await txn.rollback()
        raise e
```

### Bulk Operations
```python
# Insert multiple records efficiently
clients_data = [
    {
        "client_id": 123501,
        "client_name": "Client One SA",
        "client_shortname": "CLIENTONE"
    },
    {
        "client_id": 123502, 
        "client_name": "Client Two SA",
        "client_shortname": "CLIENTTWO"
    }
]

results = db.bulk_insert("clients", clients_data)

# Update multiple records
db.bulk_update("clients", [
    {"client_shortname": "ATONRA", "status": "active"},
    {"client_shortname": "CLIENTONE", "status": "pending"}
])
```

## 📊 Database Schema

### Clients Table
```sql
CREATE TABLE clients (
    client_id BIGINT PRIMARY KEY,
    client_name TEXT NOT NULL,
    client_shortname TEXT UNIQUE NOT NULL,
    client_bc_id UUID,
    client_hv_id TEXT,
    client_ab_id TEXT,
    status TEXT DEFAULT 'active',
    balance DECIMAL(10,2) DEFAULT 0.00,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Transactions Table
```sql
CREATE TABLE transactions (
    id BIGSERIAL PRIMARY KEY,
    client_shortname TEXT REFERENCES clients(client_shortname),
    transaction_type TEXT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    description TEXT,
    reference_id TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Documents Table
```sql
CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    client_shortname TEXT REFERENCES clients(client_shortname),
    document_name TEXT NOT NULL,
    document_type TEXT,
    file_path TEXT,
    file_size BIGINT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

## 🔧 Configuration

### Environment Variables
```bash
# Supabase Connection
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Database Settings
DB_POOL_SIZE=10
DB_TIMEOUT=30
DB_SSL_MODE=require
DB_ECHO=false

# Connection Retry
DB_RETRY_ATTEMPTS=3
DB_RETRY_DELAY=1
```

### Client Initialization
```python
# Basic initialization
db = SupabaseClient()

# Custom configuration
db = SupabaseClient(
    url=os.environ.get("SUPABASE_URL"),
    key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
    timeout=30,
    retry_attempts=3
)

# With connection pooling
db = SupabaseClient(
    pool_size=20,
    max_overflow=30,
    pool_timeout=60
)
```

## 🔍 Query Examples

### Client Operations Following Yoru Rules
```python
# Get all client integration IDs
def get_client_integrations(client_shortname):
    return db.query("clients") \
        .select("client_id, client_bc_id, client_hv_id, client_ab_id") \
        .eq("client_shortname", client_shortname) \
        .single() \
        .execute()

# Find client by any integration ID
def find_client_by_integration_id(integration_id):
    return db.query("clients") \
        .select("*") \
        .or_(f"client_bc_id.eq.{integration_id}," +
             f"client_hv_id.eq.{integration_id}," +
             f"client_ab_id.eq.{integration_id}") \
        .execute()

# Validate client shortname uniqueness
def is_shortname_available(shortname):
    result = db.query("clients") \
        .select("client_shortname") \
        .eq("client_shortname", shortname) \
        .execute()
    return len(result.data) == 0
```

### Financial Queries
```python
# Get client balance
def get_client_balance(client_shortname):
    result = db.query("clients") \
        .select("balance") \
        .eq("client_shortname", client_shortname) \
        .single() \
        .execute()
    return result.data["balance"] if result.data else 0.00

# Monthly transaction summary
def get_monthly_summary(client_shortname, year, month):
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-31"
    
    return db.query("transactions") \
        .select("transaction_type, SUM(amount)") \
        .eq("client_shortname", client_shortname) \
        .gte("created_at", start_date) \
        .lte("created_at", end_date) \
        .group_by("transaction_type") \
        .execute()

# Outstanding invoices
def get_outstanding_invoices(client_shortname):
    return db.query("invoices") \
        .select("*") \
        .eq("client_shortname", client_shortname) \
        .eq("status", "pending") \
        .order("created_at", desc=True) \
        .execute()
```

### Document Management
```python
# Store document metadata
def store_document_metadata(client_shortname, document_info):
    return db.insert("documents", {
        "client_shortname": client_shortname,
        "document_name": document_info["name"],
        "document_type": document_info["type"],
        "file_path": document_info["path"],
        "file_size": document_info["size"],
        "metadata": document_info.get("metadata", {})
    })

# Search documents
def search_client_documents(client_shortname, document_type=None):
    query = db.query("documents") \
        .select("*") \
        .eq("client_shortname", client_shortname)
    
    if document_type:
        query = query.eq("document_type", document_type)
    
    return query.order("created_at", desc=True).execute()
```

## 🚨 Troubleshooting

### Connection Issues
```python
# Test database connection
def test_connection():
    try:
        result = db.query("clients").select("COUNT(*)").execute()
        print(f"✅ Database connected. Total clients: {result.data[0]['count']}")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

# Check table existence
def check_tables():
    required_tables = ["clients", "transactions", "documents", "invoices"]
    for table in required_tables:
        try:
            db.query(table).select("COUNT(*)").limit(1).execute()
            print(f"✅ Table '{table}' exists")
        except Exception as e:
            print(f"❌ Table '{table}' missing or inaccessible: {e}")
```

### Data Validation Issues
```python
# Validate client data before insertion
def validate_client_data(client_data):
    errors = []
    
    # Required fields
    required_fields = ["client_id", "client_name", "client_shortname"]
    for field in required_fields:
        if not client_data.get(field):
            errors.append(f"Missing required field: {field}")
    
    # Shortname format validation
    shortname = client_data.get("client_shortname", "")
    if not shortname.isupper() or not shortname.replace("_", "").isalnum():
        errors.append("client_shortname must be uppercase alphanumeric")
    
    # ID validation
    client_id = client_data.get("client_id")
    if client_id and not isinstance(client_id, int):
        errors.append("client_id must be an integer")
    
    return errors
```

### Performance Issues
```python
# Monitor query performance
import time

def timed_query(query_func, *args, **kwargs):
    start_time = time.time()
    result = query_func(*args, **kwargs)
    end_time = time.time()
    
    print(f"Query executed in {end_time - start_time:.3f} seconds")
    return result

# Usage
client = timed_query(db.get_client_by_shortname, "ATONRA")

# Use indexes for better performance
def create_indexes():
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_clients_shortname ON clients(client_shortname)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_client ON transactions(client_shortname)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_shortname)"
    ]
    
    for index_sql in indexes:
        db.execute_sql(index_sql)
```

## 🔒 Security Features

### Row Level Security (RLS)
```sql
-- Enable RLS on clients table
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;

-- Policy for client data access
CREATE POLICY client_access_policy ON clients
    FOR ALL USING (
        client_shortname = current_setting('app.current_client')::text
    );
```

### Data Encryption
```python
# Encrypt sensitive data before storing
from cryptography.fernet import Fernet

def encrypt_sensitive_data(data, key):
    f = Fernet(key)
    return f.encrypt(data.encode()).decode()

def decrypt_sensitive_data(encrypted_data, key):
    f = Fernet(key)
    return f.decrypt(encrypted_data.encode()).decode()

# Usage with client data
encryption_key = os.environ.get("CLIENT_DATA_ENCRYPTION_KEY")
client_data["encrypted_notes"] = encrypt_sensitive_data(
    client_data["notes"], 
    encryption_key
)
```

## 📁 File Structure
```
supabase/
├── supabase.py         # Main Supabase client
├── models.py           # Data models and schemas
├── queries.py          # Common query functions
├── migrations/         # Database migration files
├── seeds/              # Sample data for testing
└── __init__.py         # Package initialization
```

## 🤝 Integration Examples

### With API Endpoints
```python
# In an API router
@app.get("/clients/{client_shortname}")
async def get_client(client_shortname: str):
    try:
        client = db.get_client_by_shortname(client_shortname)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        return client
    except Exception as e:
        logger.log_error(f"Failed to get client {client_shortname}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

### With Background Workers
```python
# In a job worker
def process_client_invoices(client_shortname):
    with logger.correlation_scope():
        logger.log_info(f"Processing invoices for {client_shortname}")
        
        # Get pending invoices
        invoices = db.get_outstanding_invoices(client_shortname)
        
        for invoice in invoices:
            # Process each invoice
            process_invoice(invoice)
            
            # Update status in database
            db.update("invoices", invoice["id"], {"status": "processed"})
```

This database integration is the foundation that keeps all your business data organized, secure, and accessible!