"""
Vectorize and search Granola meeting notes.

Reads exported markdown files, chunks them, generates embeddings via OpenAI,
stores them in a local ChromaDB database, and provides semantic search + analysis.

Usage:
    # Index all notes from your output directory
    python vectorize.py index -d /path/to/granola/notes

    # Search your notes semantically
    python vectorize.py search "budget discussion Q1"

    # Search with more results
    python vectorize.py search "onboarding process" -n 10

    # Show collection stats
    python vectorize.py stats

    # Re-index everything (wipe and rebuild)
    python vectorize.py index -d /path/to/granola/notes --reindex
"""

import argparse
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

# --- Configuration ---
DEFAULT_NOTES_DIR = Path("/Users/maxxyung/Claude/Granola")
CHROMA_DB_DIR = Path.home() / ".local/share/granola-vectors"
COLLECTION_NAME = "granola_notes"
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_MAX_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200


def get_embedding_function():
    """Create OpenAI embedding function, requiring OPENAI_API_KEY."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "Error: OPENAI_API_KEY environment variable is required.\n"
            "Set it with: export OPENAI_API_KEY='sk-...'"
        )
        sys.exit(1)
    return OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name=EMBEDDING_MODEL,
    )


def get_collection(embedding_fn):
    """Get or create the ChromaDB collection."""
    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown file."""
    metadata = {}
    body = text

    match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        return metadata, body

    fm_block = match.group(1)
    body = match.group(2).strip()

    # Simple key-value parsing (avoids PyYAML dependency)
    for line in fm_block.splitlines():
        if ":" in line and not line.startswith("  "):
            key, _, value = line.partition(":")
            value = value.strip().strip('"')
            if key.strip() in ("granola_id", "title", "created_at"):
                metadata[key.strip()] = value

    # Extract participant names
    participants = []
    in_participants = False
    for line in fm_block.splitlines():
        if line.startswith("participants:"):
            in_participants = True
            continue
        if in_participants:
            if line.strip().startswith("- name:"):
                name = line.split(":", 1)[1].strip().strip('"')
                if name:
                    participants.append(name)
            elif not line.startswith("  "):
                break
    if participants:
        metadata["participants"] = ", ".join(participants)

    return metadata, body


def split_notes_and_transcript(body: str) -> tuple[str, str]:
    """Split body into notes section and transcript section."""
    marker = "## Full Transcript"
    idx = body.find(marker)
    if idx == -1:
        return body.strip(), ""
    notes = body[:idx].rstrip("-\n ").strip()
    transcript = body[idx + len(marker):].strip()
    return notes, transcript


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks, breaking at paragraph boundaries."""
    if not text or len(text) <= max_chars:
        return [text] if text.strip() else []

    paragraphs = re.split(r"\n\n+", text)
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            # Keep overlap from end of current chunk
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def index_file(collection, filepath: Path, reindex: bool = False) -> int:
    """Index a single markdown file into ChromaDB. Returns number of chunks added."""
    text = filepath.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)

    doc_id = metadata.get("granola_id", filepath.stem)
    title = metadata.get("title", filepath.stem)

    # Check if already indexed (skip unless reindexing)
    if not reindex:
        existing = collection.get(where={"granola_id": doc_id})
        if existing and existing["ids"]:
            return 0

    # Split into notes and transcript
    notes, transcript = split_notes_and_transcript(body)

    chunks_added = 0

    # Chunk and store notes
    for i, chunk in enumerate(chunk_text(notes)):
        chunk_id = f"{doc_id}__notes__{i}"
        meta = {
            "granola_id": doc_id,
            "title": title,
            "section": "notes",
            "chunk_index": i,
            "source_file": str(filepath),
        }
        if metadata.get("created_at"):
            meta["created_at"] = metadata["created_at"]
        if metadata.get("participants"):
            meta["participants"] = metadata["participants"]

        collection.upsert(ids=[chunk_id], documents=[chunk], metadatas=[meta])
        chunks_added += 1

    # Chunk and store transcript
    for i, chunk in enumerate(chunk_text(transcript)):
        chunk_id = f"{doc_id}__transcript__{i}"
        meta = {
            "granola_id": doc_id,
            "title": title,
            "section": "transcript",
            "chunk_index": i,
            "source_file": str(filepath),
        }
        if metadata.get("created_at"):
            meta["created_at"] = metadata["created_at"]
        if metadata.get("participants"):
            meta["participants"] = metadata["participants"]

        collection.upsert(ids=[chunk_id], documents=[chunk], metadatas=[meta])
        chunks_added += 1

    return chunks_added


def cmd_index(args):
    """Index all markdown files from the notes directory."""
    notes_dir = Path(args.directory)
    if not notes_dir.exists():
        print(f"Error: Directory not found: {notes_dir}")
        sys.exit(1)

    embedding_fn = get_embedding_function()

    if args.reindex:
        # Wipe existing collection
        CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        try:
            client.delete_collection(COLLECTION_NAME)
        except ValueError:
            pass
        print("Cleared existing index.")

    collection = get_collection(embedding_fn)

    md_files = sorted(notes_dir.rglob("*.md"))
    if not md_files:
        print(f"No markdown files found in {notes_dir}")
        return

    print(f"Found {len(md_files)} markdown files in {notes_dir}")

    total_chunks = 0
    indexed_files = 0
    skipped_files = 0

    for filepath in md_files:
        try:
            chunks = index_file(collection, filepath, reindex=args.reindex)
            if chunks > 0:
                indexed_files += 1
                total_chunks += chunks
                print(f"  Indexed: {filepath.name} ({chunks} chunks)")
            else:
                skipped_files += 1
        except Exception as e:
            print(f"  Error indexing {filepath.name}: {e}")

    print(f"\nDone. Indexed {indexed_files} files ({total_chunks} chunks). "
          f"Skipped {skipped_files} already-indexed files.")
    print(f"Vector DB stored at: {CHROMA_DB_DIR}")


def cmd_search(args):
    """Semantic search across indexed notes."""
    embedding_fn = get_embedding_function()
    collection = get_collection(embedding_fn)

    if collection.count() == 0:
        print("No documents indexed yet. Run 'vectorize.py index' first.")
        sys.exit(1)

    n_results = args.num_results

    # Build optional filters
    where_filter = None
    if args.section:
        where_filter = {"section": args.section}

    results = collection.query(
        query_texts=[args.query],
        n_results=n_results,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    if not docs:
        print("No results found.")
        return

    print(f'Search results for: "{args.query}"\n')

    # Group by meeting to show unique meetings first
    seen_titles = set()
    for doc, meta, dist in zip(docs, metas, distances):
        similarity = 1 - dist  # cosine distance -> similarity
        title = meta.get("title", "Unknown")
        section = meta.get("section", "?")
        created = meta.get("created_at", "")[:10]
        participants = meta.get("participants", "")

        is_new_meeting = title not in seen_titles
        seen_titles.add(title)

        if is_new_meeting:
            print(f"{'=' * 60}")
        print(f"  [{similarity:.0%} match] {title}")
        print(f"  Date: {created}  |  Section: {section}")
        if participants:
            print(f"  Participants: {participants}")
        print(f"  ---")
        # Show a preview of the matching chunk
        preview = doc[:300].replace("\n", " ")
        if len(doc) > 300:
            preview += "..."
        print(f"  {preview}")
        print()

    print(f"Showing {len(docs)} results from {len(seen_titles)} meetings.")


def cmd_stats(args):
    """Show stats about the indexed collection."""
    embedding_fn = get_embedding_function()
    collection = get_collection(embedding_fn)

    total = collection.count()
    if total == 0:
        print("No documents indexed yet.")
        return

    # Get all metadata to compute stats
    all_data = collection.get(include=["metadatas"])
    metas = all_data["metadatas"]

    unique_docs = set()
    section_counts = {"notes": 0, "transcript": 0}
    dates = []

    for meta in metas:
        unique_docs.add(meta.get("granola_id", ""))
        section = meta.get("section", "")
        if section in section_counts:
            section_counts[section] += 1
        created = meta.get("created_at", "")
        if created:
            dates.append(created[:10])

    dates.sort()

    print(f"Granola Vector DB Stats")
    print(f"{'=' * 40}")
    print(f"Total chunks:     {total}")
    print(f"Unique meetings:  {len(unique_docs)}")
    print(f"Note chunks:      {section_counts['notes']}")
    print(f"Transcript chunks:{section_counts['transcript']}")
    if dates:
        print(f"Date range:       {dates[0]} to {dates[-1]}")
    print(f"DB location:      {CHROMA_DB_DIR}")


def main():
    parser = argparse.ArgumentParser(
        description="Vectorize and search Granola meeting notes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Index command
    idx_parser = subparsers.add_parser("index", help="Index markdown notes into vector DB")
    idx_parser.add_argument(
        "-d", "--directory",
        type=str,
        default=os.environ.get("GRANOLA_OUTPUT_DIR", str(DEFAULT_NOTES_DIR)),
        help="Directory containing exported Granola markdown files.",
    )
    idx_parser.add_argument(
        "--reindex",
        action="store_true",
        help="Wipe and rebuild the entire index.",
    )

    # Search command
    search_parser = subparsers.add_parser("search", help="Semantic search across notes")
    search_parser.add_argument("query", type=str, help="Search query in natural language.")
    search_parser.add_argument(
        "-n", "--num-results",
        type=int,
        default=5,
        help="Number of results to return (default: 5).",
    )
    search_parser.add_argument(
        "-s", "--section",
        choices=["notes", "transcript"],
        default=None,
        help="Filter results to only notes or transcript sections.",
    )

    # Stats command
    subparsers.add_parser("stats", help="Show index statistics")

    args = parser.parse_args()

    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
