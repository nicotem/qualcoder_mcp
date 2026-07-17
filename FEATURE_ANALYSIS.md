# Feature Analysis & Enhancement Plan

> **HISTORICAL DOCUMENT (v0.1.0 planning).** The "current feature set"
> below describes an early version. The server now exposes 48 tools
> including AI-assisted coding, codebook editing, and memo writing — see
> the README and CHANGELOG for the current surface.

## Current Feature Set (v0.1.0)

### ✅ Resources (9) - Read-only data access
1. **Project Info** - Metadata, version, coder name
2. **Codes List** - All codes with categories
3. **Categories List** - Hierarchical code categories
4. **Code Details** - Specific code with statistics
5. **Files List** - All source files
6. **File Content** - Full text content
7. **Cases List** - All cases/participants
8. **Case Details** - Case with text segments
9. **Journal Entries** - Research journal

### ✅ Tools (9) - Operations and analysis
**Project Management (3):**
1. `list_available_projects()` - Discover projects
2. `select_project()` - Switch projects
3. `get_current_project()` - Current project info

**Data Analysis (6):**
4. `search_coded_text()` - Search segments
5. `get_coded_segments()` - All segments for a code
6. `get_coding_frequencies()` - Usage statistics
7. `search_memos()` - Find notes
8. `export_code_report()` - Detailed code report
9. `get_project_summary()` - Project overview

### ✅ Prompts (4) - Analysis templates
1. `analyze_theme()` - Theme analysis
2. `compare_codes()` - Code comparison
3. `summarize_project()` - Project summary
4. `explore_case()` - Case analysis

### ✅ Security & Quality
- Path validation
- Input validation
- Error handling
- Read-only access
- LIKE escaping
- Schema validation

---

## Feature Gaps Analysis

### 🔴 HIGH VALUE - Missing Major Features

#### 1. **Attributes** (Demographics/Metadata)
**What**: Qualcoder supports attributes for files and cases (e.g., age, gender, location)
**Database**: `attribute_type`, `attribute` tables
**Use Case**:
- "Show me codes used by participants over 50"
- "Compare coding patterns by gender"
- "Which files have attribute 'interview_type=focus_group'?"

**Value**: ⭐⭐⭐⭐⭐ Critical for demographic analysis

#### 2. **Co-occurrence Analysis**
**What**: Find codes that appear together in same segments
**Database**: Query `code_text` with overlapping positions
**Use Case**:
- "What codes appear together with 'workplace stress'?"
- "Find patterns of co-occurring themes"
- "Which codes never appear together?"

**Value**: ⭐⭐⭐⭐⭐ Essential for pattern discovery

#### 3. **Code Relationships/Links**
**What**: Qualcoder's graph/network features
**Database**: `manage_links`, `gr_*` tables
**Use Case**:
- "Show me the relationship network between codes"
- "What codes are linked to 'motivation'?"
- "Visualize code hierarchies"

**Value**: ⭐⭐⭐⭐ Great for theoretical development

#### 4. **Case-Code Matrix**
**What**: Which codes appear in which cases
**Database**: Join `code_text`, `case_text`, `cases`
**Use Case**:
- "Which cases mention 'job satisfaction'?"
- "Create a matrix of cases vs themes"
- "Find cases that never mention certain codes"

**Value**: ⭐⭐⭐⭐⭐ Core comparative analysis

#### 5. **Coder Comparison**
**What**: Compare coding between multiple researchers
**Database**: `code_text` has `owner` field
**Use Case**:
- "Compare my coding with John's coding"
- "Calculate inter-coder reliability"
- "Find disagreements in coding"

**Value**: ⭐⭐⭐⭐ Important for reliability

### 🟡 MEDIUM VALUE - Nice to Have

#### 6. **Image/Audio/Video Segment Access**
**What**: Access to coded media segments
**Database**: `code_image`, `code_av` tables
**Use Case**:
- "What image regions are coded with X?"
- "Show me video timestamps for theme Y"

**Value**: ⭐⭐⭐ Depends on media use

#### 7. **Advanced Statistics**
**What**: More quantitative metrics
**Use Case**:
- "Calculate Cohen's Kappa"
- "Show coding density by file"
- "Statistical significance of patterns"

**Value**: ⭐⭐⭐ For mixed methods

#### 8. **Timeline Analysis**
**What**: Temporal patterns in coding
**Database**: Date fields in various tables
**Use Case**:
- "How has my coding evolved over time?"
- "When was this theme first identified?"
- "Show coding activity timeline"

**Value**: ⭐⭐⭐ For longitudinal studies

#### 9. **Saved Queries**
**What**: Access stored SQL queries
**Database**: `stored_sql` table
**Use Case**:
- "Run my saved query 'complex_pattern'"
- "List all saved queries"

**Value**: ⭐⭐ Advanced users only

### 🟢 LOW VALUE - Minor Enhancements

#### 10. **Batch Operations**
- Export multiple code reports at once
- Analyze multiple codes simultaneously

**Value**: ⭐⭐ Convenience

#### 11. **Text Mining Integration**
- Word frequency analysis
- N-gram extraction
- Topic modeling results

**Value**: ⭐⭐ Already in Qualcoder UI

#### 12. **Reference Management**
- RIS citation data access
**Database**: `ris` table

**Value**: ⭐ Niche use case

---

## Recommended Implementation Priority

### Phase 1: Core Analysis (Implement Now) ⚡

**1. Attributes System**
```python
@mcp.tool()
def get_file_attributes(file_id: int)
def get_case_attributes(case_id: int)
def query_by_attribute(attr_name: str, attr_value: str)
def list_all_attributes()
```
**Why**: Essential for demographic analysis, frequently needed

**2. Co-occurrence Analysis**
```python
@mcp.tool()
def find_code_cooccurrences(code_id: int, window_size: int)
def get_cooccurrence_matrix()
```
**Why**: Core qualitative analysis feature, highly requested

**3. Case-Code Matrix**
```python
@mcp.tool()
def get_case_code_matrix()
def get_codes_by_case(case_id: int)
def get_cases_by_code(code_id: int)
```
**Why**: Fundamental comparative analysis

### Phase 2: Advanced Analysis (Implement Next) 📊

**4. Coder Comparison**
```python
@mcp.tool()
def compare_coders(coder1: str, coder2: str)
def get_coder_agreement_stats(coder1: str, coder2: str)
def list_coding_differences(code_id: int)
```
**Why**: Important for team projects, reliability

**5. Code Relationships**
```python
@mcp.tool()
def get_code_links()
def get_code_network(code_id: int)
```
**Why**: Theoretical development, visualization

### Phase 3: Specialized Features (Optional) 🎯

**6. Media Coding**
```python
@mcp.tool()
def get_image_segments(code_id: int)
def get_av_segments(code_id: int)
```
**Why**: Only if user works with media

**7. Advanced Statistics**
```python
@mcp.tool()
def calculate_coding_statistics()
def get_inter_coder_reliability()
```
**Why**: Mixed methods researchers

---

## Feature Complexity Assessment

| Feature | Complexity | Value | Lines of Code | Priority |
|---------|-----------|-------|---------------|----------|
| Attributes | Low | ⭐⭐⭐⭐⭐ | ~150 | 🔴 HIGH |
| Co-occurrence | Medium | ⭐⭐⭐⭐⭐ | ~200 | 🔴 HIGH |
| Case-Code Matrix | Low | ⭐⭐⭐⭐⭐ | ~100 | 🔴 HIGH |
| Coder Comparison | Medium | ⭐⭐⭐⭐ | ~150 | 🟡 MEDIUM |
| Code Relationships | Medium | ⭐⭐⭐⭐ | ~120 | 🟡 MEDIUM |
| Media Segments | Low | ⭐⭐⭐ | ~80 | 🟢 LOW |
| Statistics | High | ⭐⭐⭐ | ~300 | 🟢 LOW |

---

## User Story Examples

### With Attributes:
```
"Show me all codes used by participants over age 50"
"Compare workplace stress mentions by gender"
"Which urban participants discussed remote work?"
```

### With Co-occurrence:
```
"What themes appear together with workplace culture?"
"Find codes that never appear with job satisfaction"
"Show me the co-occurrence network"
```

### With Case-Code Matrix:
```
"Which participants mentioned work-life balance?"
"Create a comparison table of themes by case"
"Find cases that discuss both themes X and Y"
```

### With Coder Comparison:
```
"How similar is my coding to Sarah's?"
"Calculate inter-rater reliability for code 'motivation'"
"Show disagreements between coders"
```

---

## Recommended Next Steps

**Immediate (Today):**
1. ✅ Implement Attributes (file & case)
2. ✅ Implement Co-occurrence analysis
3. ✅ Implement Case-Code matrix

**Short-term (This Week):**
4. ⏸️ Coder comparison (if multi-coder projects)
5. ⏸️ Code relationships/links

**Future:**
- Media segments (if needed)
- Advanced statistics (if requested)
- Saved queries (advanced users)

---

## Questions for User

1. **Do you work with case/file attributes?** (demographics, metadata)
   - If YES → Attributes is critical

2. **Do you have multiple coders on projects?**
   - If YES → Coder comparison is important

3. **Do you code images, audio, or video?**
   - If YES → Media segments needed

4. **What analysis do you most frequently do in Qualcoder?**
   - This guides priorities

---

## Estimated Implementation Time

**Phase 1 (Core)**: 2-3 hours
- Attributes: 45 min
- Co-occurrence: 1 hour
- Case-Code Matrix: 30 min
- Testing: 45 min

**Phase 2 (Advanced)**: 2 hours
**Phase 3 (Specialized)**: 3+ hours

**Recommendation**: Implement Phase 1 now - these are the most universally useful features that will benefit any qualitative researcher.
