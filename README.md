# Granola Sync
A Python script that exports your [Granola](https://granola.ai) meeting notes to local Markdown files.

## Features
- **Automatic Authentication** - Uses your existing Granola desktop app credentials (no API key setup required)
- **Full Markdown Export** - Converts Granola's content to clean Markdown (so I can view it in Obsidian)
- **Complete Transcripts** - Includes the full meeting transcript with speaker attribution
- **YAML Frontmatter** - Each file includes metadata for easy integration with Obsidian, Logseq, or other tools
- **Year-based Organization** - Files are automatically sorted into year folders (e.g., `2024/`, `2025/`)
- **Incremental Sync** - Only downloads new meetings; skips files that already exist
- **Safe Filenames** - Handles special characters in meeting titles

## Requirements
- Python 3.8+
- Granola desktop app installed and logged in (macOS)

```bash
pip install -r requirements.txt
```

## Usage
```bash
python granola_sync.py
```

### CLI Options
```
-o, --output-dir PATH   Output directory (default: ~/Claude/Granola)
-l, --limit N           Max documents to fetch (default: all)
```

You can also set the output directory via the `GRANOLA_OUTPUT_DIR` environment variable.

### Examples
```bash
# Use default output directory
python granola_sync.py

# Custom output directory
python granola_sync.py -o ~/Documents/Meeting\ Notes

# Fetch only the 50 most recent documents
python granola_sync.py --limit 50
```

The script will:
1. Read your Granola authentication token from `~/Library/Application Support/Granola/supabase.json`
2. Fetch all your meeting documents from the Granola API (with pagination)
3. Export each meeting as a Markdown file to your configured output directory

## Output Structure
```
your-output-folder/
    2024/
        2024-11-15 Weekly Team Standup.md
        2024-12-01 Project Kickoff.md
    2025/
        2025-01-10 Client Meeting.md
        2025-01-15 Design Review.md
```
## Example Output

Each exported file looks like this:

```markdown
---
granola_id: abc123-def456
title: "Weekly Team Standup"
created_at: 2025-01-15T10:00:00Z
participants:
  - name: "Your Name"
    email: "you@example.com"
  - name: "Team Member"
    email: "teammate@example.com"
---

## Agenda

- Review last week's progress
- Discuss blockers
- Plan for next sprint

## Action Items

- Complete feature implementation
- Schedule follow-up meeting

---
## Full Transcript

**Your Name**: Good morning everyone, let's get started with our weekly standup.

**Team Member**: Morning! I've been working on the new dashboard feature.

**Your Name**: Great, how's that progressing?
```

## Logging
The script creates a `granola_sync.log` file in the current directory with detailed sync information. Example output:

```
2025-01-15 10:30:00 - INFO - Fetching document list...
2025-01-15 10:30:01 - INFO - Found 25 documents.
2025-01-15 10:30:01 - INFO - Skipping existing: 2025-01-10 Client Meeting.md
2025-01-15 10:30:02 - INFO - Downloading new: Weekly Team Standup
2025-01-15 10:30:03 - INFO - Sync complete. 25/25 notes saved to /your/output/folder
```

## Vector Search

Search your meeting notes semantically using OpenAI embeddings and ChromaDB.

### Setup
```bash
pip install -r requirements.txt
export OPENAI_API_KEY='sk-...'
```

### Index your notes
```bash
# Index all exported notes
python vectorize.py index -d ~/Claude/Granola

# Re-index everything from scratch
python vectorize.py index -d ~/Claude/Granola --reindex
```

### Search
```bash
# Semantic search
python vectorize.py search "budget discussion Q1"

# More results
python vectorize.py search "onboarding process" -n 10

# Search only notes (skip transcripts)
python vectorize.py search "action items" -s notes

# Search only transcripts
python vectorize.py search "who mentioned the deadline" -s transcript
```

### Stats
```bash
python vectorize.py stats
```

The vector database is stored locally at `~/.local/share/granola-vectors/`.

## Limitations
- macOS only (due to Granola credential file location)
- Requires Granola desktop app to be installed and logged in
- Cannot distinguish between multiple remote speakers

## License
MIT or whatever