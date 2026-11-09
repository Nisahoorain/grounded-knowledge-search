# AI Notes — Grounded Knowledge Search

## Phase 4 — Chunking and Embedding

**Status:** Complete

**Objective:** Convert extracted PDF content into searchable chunks, generate vector embeddings, and store them in ChromaDB.

---

## 1. Chunking Implementation

**File:** `src/ingestion/chunker.py`

A custom page-aware chunking implementation was developed.

### Text Chunking

- Uses fixed-size word-based chunking.
- Supports overlapping chunks.
- Keeps short page content together when it fits within the configured chunk size.
- Preserves page numbers and document metadata.

### Table Chunking

Tables use a separate row-aware chunking strategy.

- Splits content by table rows.
- Keeps financial labels and values together whenever possible.
- Uses row-based overlap.
- Avoids splitting financial statements using ordinary text chunking.

### Metadata Preservation

Each chunk contains:

- `doc_id`
- `company`
- `sector`
- `desk`
- `doc_type`
- `period`
- `page`
- `content_type`
- `chunk_index`
- `chunk_id`
- `text`
- `n_tokens`

### Chunk ID Generation

Chunk IDs are deterministic.

The ID is generated using SHA-1 hashing of:

- Document ID
- Page number
- Content type
- Chunk position
- Chunk text

This ensures that unchanged chunks generate the same IDs when processed again.

---

## 2. Embedding Generation

**File:** `src/retrieval/embeddings.py`

### Model

```text
sentence-transformers/all-MiniLM-L6-v2
```

The model runs locally using Sentence Transformers.

### Configuration

| Setting | Value |
|---|---|
| Model | all-MiniLM-L6-v2 |
| Embedding dimension | 384 |
| Embedding batch size | 64 |
| Execution | Local |

### Batch Processing

Embedding generation uses batches of 64 texts instead of encoding the entire corpus in one operation.

This reduces memory pressure when processing large numbers of chunks.

Each chunk receives:

- `embedding`
- `embedding_model`

The embedding dimension was verified using the model's sentence embedding dimension and an encoded sample.

---

## 3. ChromaDB Vector Storage

**File:** `src/retrieval/vector_store.py`

### Database Configuration

| Setting | Value |
|---|---|
| Database | ChromaDB |
| Collection | `gks_chunks` |
| Storage | Persistent |
| Distance metric | Cosine |
| Upsert batch size | 200 |
| Embedding dimension | 384 |

Database location:

```text
data/chroma_db
```

### Stored Information

Each ChromaDB record contains:

- Chunk ID
- Chunk text
- Embedding vector
- Document metadata

Metadata includes:

```text
doc_id
company
sector
desk
doc_type
period
page
content_type
chunk_id
```

The `page` value represents the physical PDF page number.

### Indexing Process

The `--all` option processes all extracted JSON files.

For each file:

1. Load the extracted JSON.
2. Generate chunks.
3. Generate embeddings in batches.
4. Upsert records into ChromaDB.
5. Continue with the next file.

This processes one PDF at a time rather than loading the entire corpus and all its embeddings into memory simultaneously.

---

## 4. Idempotent Ingestion

Indexing supports unchanged and modified filings.

### File Hashing

SHA-256 hashes of extracted JSON files are maintained in:

```text
data/chroma_db/index_hashes.json
```

### Indexing Behaviour

| Condition | Action |
|---|---|
| New extracted file | Generate and insert chunks |
| Unchanged file | Skip embedding and indexing |
| Changed file | Delete old document chunks and insert replacements |

The document ID is used to identify existing chunks belonging to a changed filing.

### Verification Results

| Test | Result |
|---|---|
| Initial ChromaDB records | 26,799 |
| Unchanged files on rerun | 119 skipped |
| Repeated-run ChromaDB count | 26,799 |
| Changed filing replacement | Successful |
| Third run on the same filing | Skipped |

The initial hash records were populated for the existing indexed corpus without regenerating embeddings.

---

## 5. Final Index Statistics

| Metric | Result |
|---|---:|
| Source PDFs | 119 |
| Extracted JSON files | 119 |
| Total chunks | 26,799 |
| Indexed documents | 119 |
| ChromaDB records | 26,799 |
| Embedding dimension | 384 |
| Missing document IDs | 0 |

### Storage Locations

```text
data/
├── extracted/
└── chroma_db/
    ├── index_hashes.json
    └── ChromaDB persistent storage
```

---

## 6. Important Implementation Notes

1. Chunk data is generated from extracted JSON files; separate chunk JSON files are not currently persisted.
2. Embeddings are generated locally, not through an external embedding API.
3. ChromaDB uses persistent storage.
4. SHA-256 file hashes are used for indexing change detection.
5. Deterministic chunk IDs support safe repeated upserts.
6. Changed documents require deletion of their previous chunks before replacement.

---

## 7. Current Status and Next Work

### Completed

- Page-aware text chunking.
- Table-aware chunking.
- Metadata preservation.
- Deterministic chunk IDs.
- Local embedding generation.
- Batch embedding.
- ChromaDB storage.
- Idempotent indexing.
- Changed-file replacement.

### Pending — Phase 5

**Retrieval and Search**

Planned implementation:

1. Accept a user query.
2. Generate its embedding using the same embedding model.
3. Search the `gks_chunks` collection.
4. Retrieve the Top-K relevant chunks.
5. Return document metadata and physical page references.
6. Evaluate retrieval relevance and accuracy.

LLM-based answer generation will be developed after retrieval has been implemented and evaluated.
