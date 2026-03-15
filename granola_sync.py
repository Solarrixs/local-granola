import argparse
import logging
import json
import os
import platform
import requests
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

# --- Configuration ---
DEFAULT_OUTPUT_DIR = Path("/Users/maxxyung/Claude/Granola")
CREDS_FILE = Path.home() / "Library/Application Support/Granola/supabase.json"
API_BASE_URL = "https://api.granola.ai"
USER_AGENT = "Granola/5.354.0"
DEFAULT_LIMIT = 100

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('granola_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_platform():
    """Verify we're running on macOS where Granola stores credentials."""
    if platform.system() != "Darwin":
        logger.error(
            f"Unsupported platform: {platform.system()}. "
            "Granola stores credentials in ~/Library/Application Support/, "
            "which is only available on macOS."
        )
        sys.exit(1)

def load_access_token() -> Optional[str]:
    """Retrieves the access token from the local Granola configuration file."""
    if not CREDS_FILE.exists():
        logger.error(f"Credentials file missing at: {CREDS_FILE}")
        return None
        
    try:
        with open(CREDS_FILE, 'r') as f:
            data = json.load(f)
            
        if 'workos_tokens' not in data:
            logger.error("workos_tokens key missing in credentials.")
            return None

        workos_tokens = json.loads(data['workos_tokens'])
        token = workos_tokens.get('access_token')
        
        if not token:
            logger.error("Access token is null or empty.")
            return None
            
        return token
        
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to parse credentials: {e}")
        return None

def get_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": USER_AGENT,
        "X-Client-Version": USER_AGENT.split('/')[1]
    }

def fetch_documents(token: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """Retrieves metadata for all available Granola documents with pagination."""
    url = f"{API_BASE_URL}/v2/get-documents"
    all_docs = []
    offset = 0
    page_size = min(limit, 100)

    while True:
        payload = {
            "limit": page_size,
            "offset": offset,
            "include_last_viewed_panel": True
        }

        try:
            response = requests.post(url, headers=get_headers(token), json=payload)
            response.raise_for_status()
            data = response.json()
            docs = data.get("docs", [])
        except requests.RequestException as e:
            logger.error(f"API Error (get-documents, offset={offset}): {e}")
            break

        if not docs:
            break

        all_docs.extend(docs)

        if len(all_docs) >= limit:
            all_docs = all_docs[:limit]
            break

        if len(docs) < page_size:
            break

        offset += len(docs)
        time.sleep(0.1)

    return all_docs

def fetch_transcript(token: str, doc_id: str) -> Optional[List[Dict[str, Any]]]:
    """Retrieves the full transcript for a specific document ID."""
    url = f"{API_BASE_URL}/v1/get-document-transcript"
    payload = {"document_id": doc_id}
    
    try:
        response = requests.post(url, headers=get_headers(token), json=payload)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"API Error (transcript {doc_id}): {e}")
        return None

def parse_prosemirror(node: Dict[str, Any]) -> str:
    """Recursively converts ProseMirror JSON structure into Markdown."""
    if not isinstance(node, dict):
        return ""

    node_type = node.get('type', '')
    content = node.get('content', [])
    text = node.get('text', '')

    child_text = ''.join(parse_prosemirror(child) for child in content)

    if node_type == 'doc':
        return child_text
    elif node_type == 'heading':
        level = node.get('attrs', {}).get('level', 1)
        return f"{'#' * level} {child_text}\n\n"
    elif node_type == 'paragraph':
        return f"{child_text}\n\n"
    elif node_type == 'bulletList':
        return child_text + '\n'
    elif node_type == 'orderedList':
        return child_text + '\n'
    elif node_type == 'listItem':
        return f"- {child_text.strip()}\n" 
    elif node_type == 'text':
        marks = node.get('marks', [])
        for mark in marks:
            m_type = mark.get('type')
            if m_type == 'bold':
                text = f"**{text}**"
            elif m_type == 'italic':
                text = f"*{text}*"
            elif m_type == 'code':
                text = f"`{text}`"
            elif m_type == 'link':
                href = mark.get('attrs', {}).get('href', '')
                return f"[{text}]({href})"
        return text
    elif node_type == 'horizontalRule':
        return "\n---\n\n"

    return child_text

def sanitize_filename(name: str) -> str:
    # 1. Custom Replacements
    name = name.replace("<>", "and")
    name = name.replace(":", "")     # Delete colons
    name = name.replace("/", "-")    # Slashes to dashes
    
    # 2. Standard invalid character stripping (remaining ones)
    invalid_chars = '"\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "-")
    
    # 3. Collapse multiple spaces
    clean_name = " ".join(name.split())
    
    return clean_name

def resolve_speaker_name(segment: Dict[str, Any], creator_name: str, attendee_names: List[str]) -> str:
    """
    Maps 'source' to actual names based on Granola's recording logic.
    """
    source = segment.get('source')
    
    # "microphone" is the person running Granola (The Creator)
    if source == 'microphone':
        return creator_name or "Me"
    
    # "system" is the audio coming from the computer (The Attendees)
    if source == 'system':
        if len(attendee_names) == 1:
            return attendee_names[0]
        elif len(attendee_names) > 1:
            # If multiple attendees, we can't distinguish them by source alone
            return "Remote Speaker"
        else:
            return "Speaker"

    return "Unknown"

def format_transcript(transcript_data: List[Dict[str, Any]], creator_name: str, attendee_names: List[str]) -> str:
    if not transcript_data:
        return ""
        
    segments = []
    for segment in transcript_data:
        if text := segment.get('text'):
            name = resolve_speaker_name(segment, creator_name, attendee_names)
            segments.append(f"**{name}**: {text}")
            
    if not segments:
        return ""
        
    return "\n\n---\n## Full Transcript\n\n" + "\n\n".join(segments)

def extract_people(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to safely extract people info from the messy 'people' object"""
    people_data = doc.get('people') or {}
    
    # Extract Creator
    creator = people_data.get('creator') or {}
    creator_info = {
        'name': creator.get('name') or "Me",
        'email': creator.get('email')
    }
    
    # Extract Attendees
    raw_attendees = people_data.get('attendees') or []
    attendees_info = []
    
    for att in raw_attendees:
        if isinstance(att, dict):
            # Sometimes name is nested in details.person.name.fullName
            name = att.get('email') # Fallback to email
            details = att.get('details') or {}
            person = details.get('person') or {}
            name_obj = person.get('name') or {}
            
            if isinstance(name_obj, dict):
                possible_name = name_obj.get('fullName')
                if possible_name:
                    name = possible_name
            elif isinstance(name_obj, str):
                name = name_obj

            attendees_info.append({
                'name': name,
                'email': att.get('email')
            })
            
    return {
        'creator': creator_info,
        'attendees': attendees_info
    }

def sync_document(doc: Dict[str, Any], token: str, output_dir: Path) -> bool:
    doc_id = doc.get("id")
    title = doc.get("title", "Untitled")
    created_at_str = doc.get('created_at', '')

    if not doc_id:
        return False

    # --- 1. Date Parsing & Folder Setup ---
    date_prefix = "0000-00-00"
    year_folder = "Unknown_Year"

    if created_at_str:
        try:
            dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
            date_prefix = dt.strftime('%Y-%m-%d')
            year_folder = str(dt.year)
        except ValueError:
            logger.warning(f"Could not parse date '{created_at_str}' for '{title}'")

    # Create Year Subfolder if it doesn't exist (e.g. /2025/)
    target_dir = output_dir / year_folder
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {target_dir}: {e}")
            return False

    filename = f"{date_prefix} {sanitize_filename(title)}.md"
    filepath = target_dir / filename

    # --- CHECK IF EXISTS ---
    if filepath.exists():
        logger.info(f"Skipping existing: {filename}")
        return True

    logger.info(f"Downloading new: {title}")

    # --- 2. Content Parsing ---
    markdown_notes = ""
    panel = doc.get("last_viewed_panel")
    if isinstance(panel, dict):
        content = panel.get("content")
        if isinstance(content, dict) and content.get("type") == "doc":
            markdown_notes = parse_prosemirror(content)

    # --- 3. Participants & Transcript ---
    transcript_data = fetch_transcript(token, doc_id)
    
    people = extract_people(doc)
    creator_name = people['creator']['name']
    attendee_names = [a['name'] for a in people['attendees']]

    transcript_text = format_transcript(transcript_data, creator_name, attendee_names)
    time.sleep(0.1)

    # --- 4. YAML Frontmatter ---
    def yaml_escape(value: str) -> str:
        """Escape a string for safe use as a YAML double-quoted value."""
        return value.replace('\\', '\\\\').replace('"', '\\"')

    frontmatter = "---\n"
    frontmatter += f"granola_id: {doc_id}\n"
    frontmatter += f'title: "{yaml_escape(title)}"\n'
    frontmatter += f"created_at: {created_at_str}\n"

    frontmatter += "participants:\n"
    frontmatter += f'  - name: "{yaml_escape(people["creator"]["name"])}"\n'
    if people['creator']['email']:
        frontmatter += f'    email: "{yaml_escape(people["creator"]["email"])}"\n'

    for att in people['attendees']:
        frontmatter += f'  - name: "{yaml_escape(att["name"] or "")}"\n'
        if att['email']:
            frontmatter += f'    email: "{yaml_escape(att["email"])}"\n'

    frontmatter += "---\n\n"
    
    full_content = frontmatter + markdown_notes + transcript_text

    # --- 5. Save ---
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)
        return True
    except IOError as e:
        logger.error(f"Failed to write {filename}: {e}")
        return False

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Granola meeting notes to local Markdown files."
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=os.environ.get("GRANOLA_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)),
        help=f"Output directory for Markdown files (default: {DEFAULT_OUTPUT_DIR}). "
             "Can also be set via GRANOLA_OUTPUT_DIR env var.",
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=int(os.environ.get("GRANOLA_LIMIT", "0")),
        help="Maximum number of documents to fetch (default: all).",
    )
    return parser.parse_args()


def main():
    check_platform()

    args = parse_args()
    output_dir = Path(args.output_dir)
    limit = args.limit if args.limit > 0 else DEFAULT_LIMIT * 100  # effectively unlimited

    if not output_dir.exists():
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created output directory: {output_dir}")
        except OSError as e:
            logger.critical(f"Could not create output directory: {e}")
            return

    token = load_access_token()
    if not token:
        return

    logger.info("Fetching document list...")
    documents = fetch_documents(token, limit=limit)
    logger.info(f"Found {len(documents)} documents.")

    success_count = 0
    for doc in documents:
        try:
            if sync_document(doc, token, output_dir):
                success_count += 1
        except (KeyError, ValueError, TypeError, OSError) as e:
            logger.error(f"Error processing doc '{doc.get('title')}': {e}")
            continue

    logger.info(f"Sync complete. {success_count}/{len(documents)} notes saved to {output_dir}")

if __name__ == "__main__":
    main()