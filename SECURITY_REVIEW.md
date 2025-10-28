# Security Review Report - Qualcoder MCP Server

**Date**: 2025-10-28
**Reviewer**: Claude Code
**Scope**: Complete code review of qualcoder_mcp package

## Executive Summary

The Qualcoder MCP server was reviewed for security vulnerabilities and code quality issues. The code demonstrates **good security practices** in several areas, particularly SQL injection prevention through parameterized queries and read-only database access. However, **several medium-priority issues** were identified that should be addressed to improve robustness and security.

**Overall Risk Level**: MEDIUM
**Critical Issues**: 0
**High Priority Issues**: 3
**Medium Priority Issues**: 6
**Low Priority Issues**: 4

---

## ✅ Security Strengths

### 1. SQL Injection Prevention - EXCELLENT
**Status**: ✅ Secure

All database queries use parameterized queries with `?` placeholders:

```python
cursor = self.conn.execute(
    "SELECT ... WHERE cid = ?",
    (code_id,)  # Parameters passed as tuple
)
```

The LIKE queries that use `f"%{query}%"` perform string interpolation in Python before passing to SQL, which is safe:

```python
# This is SAFE - interpolation happens in Python, not SQL
cursor.execute("WHERE seltext LIKE ?", (f"%{query}%",))
```

**Verdict**: No SQL injection vulnerabilities found.

### 2. Read-Only Database Access - EXCELLENT
**Status**: ✅ Secure

The database connection uses SQLite's read-only mode:

```python
self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
```

**Verdict**: Prevents any accidental or malicious data modification.

### 3. No Remote Code Execution
**Status**: ✅ Secure

The code does not use `eval()`, `exec()`, or dynamic imports with user-controlled input.

---

## ⚠️ High Priority Issues

### HIGH-1: Path Traversal via QUALCODER_PROJECT_PATH
**Severity**: HIGH
**File**: `server.py:33-40`, `database.py:12-24`

**Issue**: The database path is read from an environment variable without sufficient validation. While the user controls this via Claude Desktop config, malicious config could point to any SQLite database on the system.

**Current Code**:
```python
db_path = os.environ.get("QUALCODER_PROJECT_PATH")
# Only checks existence, not that it's a valid .qda file
self.db_path = Path(db_path)
if not self.db_path.exists():
    raise FileNotFoundError(...)
```

**Attack Scenario**:
- User could point to `/etc/some_sqlite.db`
- User could use path traversal: `../../sensitive/data.db`
- Could read any SQLite database on the system

**Risk**: Information disclosure - access to unintended databases

**Recommendation**:
```python
def validate_qda_path(db_path: str) -> Path:
    """Validate that the path is a legitimate .qda file."""
    path = Path(db_path).resolve()  # Resolve symlinks and relative paths

    # Check file extension
    if path.suffix.lower() != '.qda':
        raise ValueError(f"Invalid file: must be a .qda file, got {path.suffix}")

    # Check file exists
    if not path.exists():
        raise FileNotFoundError(f"Database file not found: {path}")

    # Check it's a regular file (not a directory or special file)
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    # Optional: Verify it's actually a SQLite database
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        cursor.fetchone()
        conn.close()
    except sqlite3.DatabaseError:
        raise ValueError(f"Invalid SQLite database: {path}")

    return path
```

### HIGH-2: Unbounded Query Results
**Severity**: HIGH
**File**: `database.py` (multiple methods)

**Issue**: The `limit` parameters are not validated and could cause:
- Memory exhaustion with extremely large limits
- Negative limit values causing errors
- Missing limits on some queries

**Current Code**:
```python
def get_coded_text_segments(self, code_id: int, limit: int = 100):
    # No validation of limit parameter
    cursor = self.conn.execute("... LIMIT ?", (code_id, limit))
```

**Attack Scenario**:
```python
get_coded_text_segments(1, limit=999999999)  # Could exhaust memory
get_coded_text_segments(1, limit=-1)         # Undefined behavior
```

**Recommendation**:
```python
def validate_limit(limit: int, max_limit: int = 10000) -> int:
    """Validate and cap limit parameter."""
    if limit < 1:
        raise ValueError(f"Limit must be positive, got {limit}")
    if limit > max_limit:
        logger.warning(f"Limit {limit} exceeds maximum {max_limit}, capping")
        return max_limit
    return limit

def get_coded_text_segments(self, code_id: int, limit: int = 100):
    limit = validate_limit(limit, max_limit=5000)
    # ... rest of code
```

### HIGH-3: Missing Error Handling and Information Disclosure
**Severity**: HIGH
**File**: `database.py`, `server.py` (all methods)

**Issue**: No try-catch blocks around database operations. Raw SQLite errors could leak:
- Database structure information
- File paths
- SQL query details

**Current Code**:
```python
def get_code_details(self, code_id: int):
    cursor = self.conn.execute("""...""", (code_id,))
    # No error handling - exceptions propagate with full details
```

**Attack Scenario**:
- Malformed database could trigger errors with sensitive info
- Invalid IDs could expose query structure
- Database corruption errors reveal file paths

**Recommendation**:
```python
def get_code_details(self, code_id: int) -> Optional[Dict[str, Any]]:
    """Get detailed information about a specific code."""
    try:
        cursor = self.conn.execute("""
            SELECT ...
            WHERE c.cid = ?
        """, (code_id,))
        # ... rest of logic
    except sqlite3.Error as e:
        logger.error(f"Database error in get_code_details: {e}")
        # Return generic error, don't expose details
        raise RuntimeError("Failed to retrieve code details") from None
    except Exception as e:
        logger.error(f"Unexpected error in get_code_details: {e}")
        raise RuntimeError("An unexpected error occurred") from None
```

---

## ⚠️ Medium Priority Issues

### MED-1: LIKE Wildcard Injection
**Severity**: MEDIUM
**File**: `database.py:426-449`, `database.py:527-581`

**Issue**: User input in LIKE queries is not escaped. SQLite wildcards (`%`, `_`) in user input could cause unexpected behavior.

**Current Code**:
```python
cursor.execute("WHERE ct.seltext LIKE ?", (f"%{query}%",))
```

**Attack Scenario**:
```python
search_coded_text("%")  # Returns everything
search_coded_text("___") # Returns all 3-character strings
```

**Risk**: Not a security vulnerability but could cause confusing results

**Recommendation**:
```python
def escape_like_pattern(pattern: str) -> str:
    """Escape SQLite LIKE wildcards in user input."""
    # Escape special characters
    pattern = pattern.replace('\\', '\\\\')
    pattern = pattern.replace('%', '\\%')
    pattern = pattern.replace('_', '\\_')
    return pattern

def search_coded_text(self, query: str, ...):
    escaped_query = escape_like_pattern(query)
    cursor.execute(
        "WHERE ct.seltext LIKE ? ESCAPE '\\'",
        (f"%{escaped_query}%",)
    )
```

### MED-2: No Input Type Validation
**Severity**: MEDIUM
**File**: `database.py` (all methods taking IDs)

**Issue**: ID parameters are typed as `int` but not validated at runtime. Python's dynamic typing means wrong types could be passed.

**Current Code**:
```python
def get_code_details(self, code_id: int):  # Type hint, not enforced
    cursor.execute("... WHERE cid = ?", (code_id,))
```

**Attack Scenario**:
```python
get_code_details("'; DROP TABLE code_name; --")  # Type mismatch
get_code_details(None)  # Could cause errors
```

**Risk**: LOW - parameterized queries protect against injection, but could cause crashes

**Recommendation**:
```python
def get_code_details(self, code_id: int) -> Optional[Dict[str, Any]]:
    if not isinstance(code_id, int):
        raise TypeError(f"code_id must be int, got {type(code_id)}")
    if code_id < 0:
        raise ValueError(f"code_id must be non-negative, got {code_id}")
    # ... rest of code
```

### MED-3: Database Connection Not Properly Closed
**Severity**: MEDIUM
**File**: `database.py:26-29`

**Issue**: Using `__del__` for cleanup is unreliable. Python doesn't guarantee `__del__` will be called.

**Current Code**:
```python
def __del__(self):
    if hasattr(self, 'conn'):
        self.conn.close()
```

**Risk**: Resource leak if connection not closed

**Recommendation**:
```python
from contextlib import contextmanager

class QualcoderDatabase:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Explicitly close the database connection."""
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
            self.conn = None

    def __del__(self):
        # Keep as backup but add explicit close
        self.close()

# Usage:
with QualcoderDatabase(path) as db:
    codes = db.list_codes()
```

### MED-4: Logging Sensitive Information
**Severity**: MEDIUM
**File**: `server.py:40`

**Issue**: Full database path is logged, which may contain sensitive information (usernames, project names).

**Current Code**:
```python
logger.info(f"Connected to Qualcoder database: {db_path}")
```

**Recommendation**:
```python
# Log only the filename, not full path
logger.info(f"Connected to Qualcoder database: {Path(db_path).name}")
```

### MED-5: No Rate Limiting
**Severity**: MEDIUM
**File**: `server.py` (all tools)

**Issue**: No protection against abuse. A malicious actor could repeatedly call expensive operations.

**Risk**: Denial of Service via resource exhaustion

**Recommendation**:
Implement rate limiting in FastMCP or add throttling:
```python
from functools import wraps
from time import time

def rate_limit(calls_per_minute=60):
    last_calls = []

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time()
            # Remove old calls
            last_calls[:] = [t for t in last_calls if now - t < 60]

            if len(last_calls) >= calls_per_minute:
                raise RuntimeError("Rate limit exceeded")

            last_calls.append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

@mcp.tool()
@rate_limit(calls_per_minute=30)
def search_coded_text(...):
    ...
```

### MED-6: Large Data Export Without Pagination
**Severity**: MEDIUM
**File**: `server.py:277`

**Issue**: `export_code_report` uses `limit=1000` which could return large amounts of data.

**Current Code**:
```python
segments = get_db().get_coded_text_segments(code_id, limit=1000)
```

**Recommendation**:
```python
# Add pagination support or reduce limit
segments = get_db().get_coded_text_segments(code_id, limit=500)
# Or add warning in docstring about large exports
```

---

## ⚠️ Low Priority Issues

### LOW-1: Linear Search in export_code_report
**Severity**: LOW
**File**: `server.py:261-266`

**Issue**: Linear search through all codes could be slow for large projects.

**Recommendation**: Use a dictionary for O(1) lookup if needed frequently.

### LOW-2: Inconsistent Error Response Format
**Severity**: LOW
**File**: `server.py:91, 118, 144, 269`

**Issue**: Error responses are JSON objects, but success responses have different structures.

**Recommendation**: Standardize error response format across all tools.

### LOW-3: No Database Schema Validation
**Severity**: LOW
**File**: `database.py:12-24`

**Issue**: Code assumes specific database schema but doesn't verify it's a Qualcoder database.

**Recommendation**:
```python
def validate_qualcoder_schema(self):
    """Verify this is a Qualcoder database."""
    required_tables = ['project', 'code_name', 'code_text', 'source']
    cursor = self.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    tables = {row[0] for row in cursor.fetchall()}

    missing = set(required_tables) - tables
    if missing:
        raise ValueError(f"Invalid Qualcoder database: missing tables {missing}")
```

### LOW-4: No Version Compatibility Check
**Severity**: LOW
**File**: `database.py`

**Issue**: Qualcoder databases have version numbers (v6, v7, v8, v13). Code doesn't check compatibility.

**Recommendation**:
```python
def check_database_version(self):
    cursor = self.conn.execute("SELECT databaseversion FROM project")
    version = cursor.fetchone()[0]
    supported_versions = ['v6', 'v7', 'v8', 'v13']
    if version not in supported_versions:
        logger.warning(f"Untested database version: {version}")
```

---

## Code Quality Issues

### Quality-1: Magic Numbers
- Hard-coded limits (50, 100, 1000) should be constants
- Recommendation: `DEFAULT_SEARCH_LIMIT = 50`, `MAX_SEGMENT_LIMIT = 1000`

### Quality-2: No Type Checking
- Consider using `mypy` for static type checking
- Add runtime validation with `pydantic` or similar

### Quality-3: Insufficient Documentation
- Methods lack information about exceptions they raise
- No documentation on expected database schema

---

## Summary of Recommendations

### Immediate Actions (High Priority)
1. ✅ Add path validation for .qda files
2. ✅ Validate and cap limit parameters
3. ✅ Add comprehensive error handling
4. ✅ Escape LIKE wildcard characters

### Short Term (Medium Priority)
5. Add explicit close() method and context manager support
6. Reduce logging of sensitive paths
7. Implement rate limiting for tools
8. Add input type validation

### Long Term (Low Priority)
9. Database schema validation
10. Version compatibility checks
11. Standardize error responses
12. Add comprehensive test suite

---

## Security Best Practices Followed

✅ Parameterized SQL queries (prevents SQL injection)
✅ Read-only database access (prevents data modification)
✅ No use of eval/exec (prevents code injection)
✅ Type hints for API clarity
✅ Clear separation of concerns (database.py vs server.py)
✅ Logging for debugging and monitoring

---

## Conclusion

The Qualcoder MCP server demonstrates **fundamentally sound security practices**, particularly in the critical area of SQL injection prevention. The identified issues are primarily about **defense in depth** and **robustness** rather than critical vulnerabilities.

**Recommended Action**: Address HIGH priority issues before production use, particularly:
- Path validation
- Input parameter validation
- Error handling

The code is suitable for **personal use** in its current state, but should be **hardened** for broader distribution or use with untrusted configurations.

---

## Mitigation Status

| Issue | Status | Priority |
|-------|--------|----------|
| Path Traversal | 🔴 Not Fixed | HIGH |
| Unbounded Queries | 🔴 Not Fixed | HIGH |
| Error Handling | 🔴 Not Fixed | HIGH |
| LIKE Wildcards | 🔴 Not Fixed | MEDIUM |
| Input Validation | 🔴 Not Fixed | MEDIUM |
| Connection Cleanup | 🔴 Not Fixed | MEDIUM |
| Sensitive Logging | 🔴 Not Fixed | MEDIUM |
| Rate Limiting | 🔴 Not Fixed | MEDIUM |
| Large Exports | 🔴 Not Fixed | MEDIUM |

**Next Step**: Implement fixes for HIGH and MEDIUM priority issues.
