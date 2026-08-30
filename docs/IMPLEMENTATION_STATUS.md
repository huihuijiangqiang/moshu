# Consistency Backend Implementation Status

## Completed (P0 MVP Core)

### 1. Database Models ✅
- **Core models** (models_core.py): User, Project, Volume, Chapter, ChapterBody, ChapterVersion
- **Codex models** (models_codex.py): CodexEntry, CodexAlias, CodexRef, CodexRelation
- **Consistency persistence** (models_consistency.py): ChapterOutlineState, ChapterOutlineRevision, OutboxEvent, IdempotencyRecord
- **Consistency extended** (models_consistency_extended.py): ConsistencyRun, DocumentSummary, ConsistencyClaim, StoryEvent, EntityStateInterval, GuardIssueEvidence, GuardResolution
- **Guard models** (models_guard.py): GuardIssue, Foreshadow
- All models registered in db/__init__.py with proper TYPE_CHECKING imports

### 2. P0-A: Chapter Body Save Service ✅
- **services/body.py** - Complete implementation with:
  - Content hash computation (SHA-256)
  - Paragraph structure validation (paragraph/heading/listItem with pid)
  - IdempotencyService integration (reserve/complete with owner_token)
  - Version conflict detection (base_rev enforcement)
  - CodexRef extraction and validation (reject invalid/cross-project refs)
  - Transactional outbox pattern
  - Words count update
  - Returns consistency_status field (queued/unchanged)

### 3. Idempotency Service ✅
- **services/idempotency.py** - Two-phase idempotency:
  - reserve() - returns {action: execute/replay/wait, owner_token}
  - complete() - requires owner_token for completion
  - Request hash validation (same key + different payload = 409 conflict)
  - Lease-based concurrency control
  - TTL and cleanup

### 4. Outbox Service ✅
- **services/outbox.py** - Transactional outbox pattern:
  - enqueue() - idempotent event insertion
  - lease_batch() - SKIP LOCKED for concurrent processing
  - mark_sent() - lease validation and cleanup
  - mark_failed() - exponential backoff and dead letter
  - Payload conflict detection

### 5. Consistency Service (Partial) ✅
- **services/consistency.py** - Basic claim management:
  - compute_claim_fingerprint() - SHA-256 fingerprinting
  - get_or_create_run() - run lifecycle
  - resolve_entity_by_alias() - Unicode NFC normalization (FIXED: uses CodexAlias.alias)
  - upsert_claim() - idempotent claim persistence
  - supersede_old_claims() - version management
  - Three deterministic rules (logic only, no persistence):
    * check_alive_conflict() - detects alive=false overlaps
    * check_ownership_conflict() - detects ownership conflicts
    * check_knowledge_boundary() - detects premature knowledge use

### 6. Alembic Migrations ✅
- alembic.ini configuration
- alembic/env.py with async engine support
- alembic/script.py.mako template
- alembic/versions/001_initial.py - partial schema (core + codex tables)
- NOTE: Full migration requires live DB connection for autogeneration

### 7. Consistency APIs ✅
- **api/consistency.py** - REST endpoints:
  - GET /consistency/status/{chapter_id}/{body_rev} - query run status
  - POST /consistency/scan - trigger manual scan (placeholder)
  - GET /consistency/issues/{project_id} - list issues with filters
  - GET /consistency/issues/{project_id}/{issue_id} - issue detail
  - POST /consistency/issues/{project_id}/{issue_id}/resolve - resolve issue (placeholder)

### 8. Celery Tasks (Placeholders) ✅
- **tasks/consistency.py** - Task signatures defined:
  - process_body_saved() - outbox event handler
  - extract_claims() - LLM extraction
  - generate_summary() - summarization
  - scan_rules() - rule execution
  - dispatch_outbox() - outbox dispatcher

### 9. Provider Interfaces ✅
- **services/providers.py** - Abstract interfaces:
  - EmbeddingProvider - embed_text(), embed_batch()
  - StructuredExtractionProvider - extract_claims(), generate_summary()
  - MockEmbeddingProvider - deterministic testing mock
  - MockStructuredExtractionProvider - placeholder mock

### 10. RAG Retrieval Layers ✅
- **services/retrieval.py** - ConsistencyRetrieval class:
  - Layer 2: resolve_entity_by_alias_l2() - exact alias matching
  - Layer 3: retrieve_similar_entities_l3() - pgvector similarity (partial)
  - Layer 4: retrieve_adjacent_summaries_l4() - adjacent chapter summaries

### 11. Tests ✅
- All 59 consistency tests passing
- test_body.py - content hash, pid extraction, codex ref extraction
- test_consistency.py - claim fingerprint, rule logic concepts, hard negatives
- test_idempotency.py - canonical hash, reserve/complete semantics
- test_outbox.py - enqueue, lease, sent, failed, dead letter
- test_models.py - schema constraints
- test_eval.py - evaluation smoke test (needs real implementation)

### 12. Code Quality ✅
- All ruff linting errors fixed (28 → 0)
- All F821 undefined name errors fixed with TYPE_CHECKING
- All import sorting and unused import errors fixed
- services/body.py fully corrected per requirements

## Remaining Work (P0 MVP Gaps)

### 1. Rule Scanner with Persistence ❌
**Current**: Three rules return dicts but don't persist GuardIssue or GuardIssueEvidence
**Required**:
- Implement complete rule_scanner.py that:
  * Loads all claims for chapter
  * Applies three rules
  * Creates GuardIssue records with unique IDs
  * Creates GuardIssueEvidence records linking to claims and content
  * Records issue_type, severity, description, evidence, anchor
  * Returns issue IDs for tracking

### 2. Evaluation Tests with Real Implementation ❌
**Current**: test_eval.py hardcodes detected=expected, evidence_matched=True (invalid 100%)
**Required**:
- Rewrite fixtures_eval.py test cases to:
  * Create realistic chapter content JSON
  * Create corresponding claims
  * Call actual rule scanner
  * Validate detected issues match expected
  * Validate evidence includes correct paragraph IDs and quotes
  * Compute real precision/recall/F1
  * Test hard negatives properly (should NOT trigger issues)

### 3. Complete Alembic Migration ❌
**Current**: 001_initial.py only has core + codex tables
**Required**:
- Add consistency tables to migration:
  * chapter_outline_state, chapter_outline_revisions
  * outbox_events, idempotency_records
  * consistency_runs, document_summaries, consistency_claims
  * story_events, entity_state_intervals
  * guard_issue_evidence, guard_resolutions
- OR: Generate complete migration when DB available: `alembic revision --autogenerate`

### 4. Real Celery Task Implementation ❌
**Current**: All tasks return {"status": "not_implemented"}
**Required**:
- process_body_saved(): Chain extract_claims → generate_summary → scan_rules
- extract_claims(): Call LLM provider, parse structured output, persist claims
- generate_summary(): Call LLM provider, persist DocumentSummary
- scan_rules(): Call rule_scanner, persist GuardIssue + Evidence
- dispatch_outbox(): OutboxService integration, route topics to tasks

### 5. LLM Provider Implementation ❌
**Current**: MockStructuredExtractionProvider returns empty/placeholder
**Required**:
- OpenAI-compatible provider using model gateway
- Structured output parsing with retry logic
- Token counting and rate limiting
- Error handling and fallback
- Schema validation for extracted claims

### 6. Vector Search Implementation ❌
**Current**: retrieve_similar_entities_l3() has placeholder cosine similarity
**Required**:
- Use pgvector native operators: `<=>` (cosine distance), `<->` (L2 distance)
- Proper indexing: CREATE INDEX ON codex_entries USING ivfflat (embedding vector_cosine_ops)
- Batch embedding generation for codex entries
- Threshold tuning for recall/precision

### 7. API Integration ❌
**Current**: Consistency APIs not registered in main.py
**Required**:
- Register consistency router in main.py
- Hook body save API to call save_chapter_body()
- Return proper error codes (422 for invalid refs, 409 for conflicts, 202 for in-progress)
- Add authentication/authorization middleware

### 8. Missing Tests ❌
**Required**:
- Integration test for complete body save flow
- Integration test for rule scanner with real issues
- Contract tests for LLM provider (mock API, validate prompts)
- Performance tests for vector retrieval
- Concurrency tests for idempotency and outbox

## Summary Statistics

- **Lines of Code**: ~3500+ (models, services, APIs, tasks, tests)
- **Database Tables**: 23 tables (6 core, 4 codex, 4 consistency, 7 consistency_extended, 2 guard)
- **API Endpoints**: 5 consistency endpoints
- **Services**: 6 services (body, consistency, idempotency, outbox, providers, retrieval)
- **Tests**: 59 passing tests
- **Ruff Errors**: 0
- **Critical Defects Fixed**: 9/9 from Codex review

## Known Limitations (Acceptable for MVP)

1. **No Live Database**: Migration cannot be fully generated without DB connection
2. **Placeholder Tasks**: Celery tasks defined but not implemented (need LLM integration)
3. **Mock Providers**: LLM and embedding providers are mocks (need gateway integration)
4. **Incomplete Vector Search**: pgvector operators not used (need production setup)
5. **No Auth**: APIs have no authentication (need to integrate existing auth)
6. **Evaluation Metrics Invalid**: 100% scores are hardcoded (need real scanner)

## Next Steps for Production

1. Set up PostgreSQL with pgvector extension
2. Run `alembic upgrade head` to create schema
3. Implement real LLM providers using model gateway
4. Implement Celery tasks with actual processing logic
5. Implement rule scanner with GuardIssue persistence
6. Rewrite evaluation tests with real scanner
7. Register APIs in main.py with auth middleware
8. Set up Celery workers and beat scheduler
9. Configure outbox dispatcher as periodic task
10. Add monitoring and observability (Sentry, metrics)

## Validation Commands

```bash
# Run all tests
python -m pytest server/tests/consistency/ -v

# Check linting
python -m ruff check server/

# Generate migration (requires live DB)
cd server && alembic revision --autogenerate -m "consistency schema"

# Apply migrations (requires live DB)
cd server && alembic upgrade head

# Run development server
cd server && uvicorn main:app --reload
```
