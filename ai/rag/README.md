# RAG — Runbook Retrieval

Semantic search over operational runbooks using ChromaDB as the vector store
and sentence-transformers for embeddings.

## Architecture
- **Embeddings**: sentence-transformers (all-MiniLM-L6-v2)
- **Vector Store**: ChromaDB (persistent, local)
- **Orchestration**: LangChain retrieval chains

## Timeline
Week 11 implementation.
