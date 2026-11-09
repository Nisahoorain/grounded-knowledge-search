# Grounded Knowledge Search

A document-based Retrieval-Augmented Generation (RAG) system designed to search financial information from company filings and answer questions using relevant document content and source references.

The project focuses on extracting, processing, indexing, and retrieving information from financial documents, especially annual reports (10-K), quarterly reports (10-Q), current reports (8-K), and earnings documents.

## Project Overview

The system uses a corpus of 119 financial PDFs and a dataset of 20 questions.

Its primary goal is to make financial information easier to search while preserving document context, including company, reporting period, document type, and physical page number.

Special attention is given to financial tables because important values such as revenue, total assets, accounts payable, and cash flows often cannot be reliably retrieved from ordinary PDF text extraction alone.

### Key Objectives

- Extract text and financial tables from PDF documents.
- Preserve document metadata and physical page numbers.
- Convert extracted content into manageable, searchable chunks.
- Generate vector embeddings using a local embedding model.
- Store chunks and embeddings in ChromaDB.
- Retrieve relevant information based on user questions.
- Support grounded answers with document and page references.

## Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python |
| PDF Text Extraction | PyMuPDF |
| Table Extraction | pdfplumber |
| Text Chunking | Custom Python implementation |
| Embedding Model | sentence-transformers/all-MiniLM-L6-v2 |
| Vector Database | ChromaDB |
| Embedding Dimension | 384 |
| Configuration | Environment settings / project defaults |

## System Architecture

```text
                 Financial PDF Corpus
                         |
                         v
                  PDF Ingestion
                         |
              +----------+----------+
              |                     |
              v                     v
         Text Extraction      Table Extraction
           (PyMuPDF)           (pdfplumber)
              |                     |
              +----------+----------+
                         |
                         v
                Extracted JSON Files
                   data/extracted
                         |
                         v
                  Content Chunking
                  (Page-Aware)
                         |
              +----------+----------+
              |                     |
              v                     v
          Text Chunks           Table Chunks
              |                     |
              +----------+----------+
                         |
                         v
                  Embedding Model
                all-MiniLM-L6-v2
                         |
                         v
                    Embeddings
                         |
                         v
                    ChromaDB
                   gks_chunks
                         |
                         v
                Retrieval / Search
                   (Next Phase)
                         |
                         v
                Grounded Answers
                   (Future Work)
```

## Project Structure

```text
grounded-knowledge-search/
│
├── src/
│   ├── ingestion/
│   │   ├── table_extractor.py
│   │   ├── chunker.py
│   │   └── ingest.py
│   │
│   ├── retrieval/
│   │   ├── embeddings.py
│   │   └── vector_store.py
│   │
│   ├── analysis/
│   │
│   └── config.py
│
├── data/
│   ├── extracted/
│   └── chroma_db/
│       └── index_hashes.json
│
├── pdfs/
├── manifest.csv
├── pdf_inventory.csv
├── CORPUS_ANALYSIS.md
├── DECISIONS.md
├── EXPERIMENTS.md
├── AI_NOTES.md
└── README.md
```

## Dataset

The project currently contains:

| Metric | Count |
|---|---:|
| Financial PDFs | 119 |
| Manifest Records | 119 |
| Evaluation Questions | 20 |
| Total Physical Pages | 18,371 |
| Detected Tables | 8,007 |

### Document Types

The corpus includes:

- 10-K annual reports
- 10-Q quarterly reports
- 8-K current reports
- Earnings documents
- Annual report documents

Original manifest document types are preserved during processing.

## Completed Work

### 1. Dataset Inspection and Analysis

- Verified all 119 PDFs against the manifest.
- Confirmed matching document IDs and filenames.
- Checked for missing and duplicate documents.
- Analyzed document types, companies, sectors, and reporting periods.
- Inspected representative PDFs and their financial statements.

### 2. PDF Ingestion and Extraction

- Processed all 119 PDFs.
- Extracted document content page by page.
- Used PyMuPDF for text extraction.
- Used pdfplumber for financial table extraction.
- Saved extracted content as JSON files in `data/extracted`.
- Generated `pdf_inventory.csv`.

**Ingestion result:**

- Saved: 119
- Skipped: 0
- Failed: 0

### 3. Financial Table Extraction

Developed a custom table extraction and cleanup pipeline.

Work includes:

- Layout-first row merging.
- Financial amount detection.
- Label and value preservation.
- Handling negative financial values.
- Removing irrelevant footer content.
- Preserving financial statement row order.

Representative PDF checks include:

| Document | Physical Page | Extracted Rows |
|---|---:|---:|
| 3M 2015 10-K | 50 | 19 |
| 3M 2015 10-K | 52 | 41 |
| 3M 2015 10-K | 54 | 36 |
| Adobe 2015 10-K | 34 | 16 |

Detailed extraction decisions and experiments are documented in `DECISIONS.md` and `EXPERIMENTS.md`.

### 4. Chunking and Embedding — Complete

Implemented page-aware chunking with separate handling for text and tables.

Key features:

- Fixed-size text chunking with overlap.
- Table-aware row chunking.
- Preservation of physical page numbers.
- Preservation of document metadata.
- Deterministic chunk IDs.
- Local embedding generation.
- Batch embedding to reduce memory usage.

**Embedding configuration:**

- Model: `all-MiniLM-L6-v2`
- Embedding dimension: 384
- Embedding batch size: 64

**Results:**

| Metric | Result |
|---|---:|
| PDFs processed | 119 |
| Total chunks | 26,799 |
| Embedding dimension | 384 |
| ChromaDB records | 26,799 |

### 5. ChromaDB Indexing and Idempotent Ingestion — Complete

Implemented persistent vector storage using ChromaDB.

Collection:

```text
gks_chunks
```

Database location:

```text
data/chroma_db
```

Stored information includes:

- Chunk ID
- Chunk text
- Embedding vector
- Document ID
- Company
- Sector
- Desk
- Document type
- Reporting period
- Physical page number
- Content type

Idempotent indexing is implemented using SHA-256 hashes.

Hash records are maintained in:

```text
data/chroma_db/index_hashes.json
```

The indexing process:

1. Calculates the hash of each extracted JSON file.
2. Skips files whose hashes have not changed.
3. Deletes old chunks belonging to a changed document.
4. Embeds and inserts the replacement chunks.

**Verification:**

| Test | Result |
|---|---|
| Initial indexed records | 26,799 |
| Unchanged files on rerun | 119 skipped |
| ChromaDB count after rerun | 26,799 |
| Changed filing replacement | Successful |
| Third run on same filing | Skipped |

## Current Project Status

| Component | Status |
|---|---|
| Dataset inspection | Complete |
| PDF ingestion | Complete |
| Text extraction | Complete |
| Financial table extraction | Implemented; representative pages verified |
| Page-aware chunking | Complete |
| Embedding generation | Complete |
| ChromaDB indexing | Complete |
| Idempotent ingestion | Complete |
| Semantic search | Pending |
| Retrieval evaluation | Pending |
| LLM-based answer generation | Pending |
| Source-grounded responses | Pending |

## Next Phase — Retrieval and Search

The next development phase focuses on retrieving relevant chunks from ChromaDB.

Planned workflow:

```text
User Question
      |
      v
Query Embedding
      |
      v
ChromaDB Similarity Search
      |
      v
Top-K Relevant Chunks
      |
      v
Document and Page References
```

The first priority is to implement and evaluate semantic search before introducing LLM-based answer generation.

## Documentation

Additional project documentation:

- `CORPUS_ANALYSIS.md` — Dataset and PDF analysis.
- `DECISIONS.md` — Production table extraction decisions.
- `EXPERIMENTS.md` — Verified, failed, and unresolved extraction experiments.
- `AI_NOTES.md` — Technical implementation notes.

---

**Project:** Grounded Knowledge Search  
**Current Milestone:** Phase 4 — Chunking and Embedding Complete  
**Next Milestone:** Phase 5 — Retrieval and Search
