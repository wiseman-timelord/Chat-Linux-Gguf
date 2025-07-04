# scripts/utility.py

import re
import subprocess
import json
import time
from pathlib import Path
from datetime import datetime
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from newspaper import Article
import scripts.temporary as temporary
from scripts.temporary import HISTORY_DIR, ALLOWED_EXTENSIONS, current_session_id, session_label

def get_available_gpus():
    """Detect available NVIDIA GPUs using nvidia-smi."""
    try:
        output = subprocess.check_output("nvidia-smi --query-gpu=name --format=csv,noheader", shell=True).decode()
        return [line.strip() for line in output.split('\n') if line.strip()] or ["No GPU"]
    except Exception as e:
        print(f"GPU error: {str(e)[:60]}"); time.sleep(1)
        return ["No GPU"]

def generate_session_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def generate_session_label(session_log):
    if not session_log:
        return "Untitled"
    text = " ".join([msg['content'].replace("User:\n", "", 1) if msg['role'] == 'user' else msg['content'] for msg in session_log])
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Simplified keyword extraction (removed YAKE dependency)
    words = re.findall(r'\b\w{4,}\b', text.lower())
    freq = {}
    for word in words:
        freq[word] = freq.get(word, 0) + 1
    if not freq:
        return "No description"
    top_word = max(freq, key=freq.get)
    return top_word.capitalize()[:25]

def save_session_history(session_log, attached_files):
    if not temporary.current_session_id:
        temporary.current_session_id = generate_session_id()
    temporary.session_label = generate_session_label(session_log)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    session_file = Path(HISTORY_DIR) / f"session_{temporary.current_session_id}.json"
    temp_file = session_file.with_suffix('.tmp')
    session_data = {
        "session_id": temporary.current_session_id,
        "label": temporary.session_label,
        "history": session_log,
        "attached_files": attached_files
    }
    with open(temp_file, "w") as f:
        json.dump(session_data, f)
    os.replace(temp_file, session_file)
    manage_session_history()
    return "Session saved"

def load_session_history(session_file):
    try:
        with open(session_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Load error: {str(e)[:60]}"); time.sleep(1)
        return None, "Error", [], []
    
    session_id = data.get("session_id", session_file.stem.replace('session_', ''))
    label = data.get("label", "Untitled")
    history = data.get("history", [])
    attached_files = [file for file in data.get("attached_files", []) if Path(file).exists()]
    temporary.session_attached_files = attached_files
    return session_id, label, history, attached_files

def manage_session_history():
    history_dir = Path(HISTORY_DIR)
    session_files = sorted(history_dir.glob("session_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    while len(session_files) > temporary.MAX_HISTORY_SLOTS:
        oldest_file = session_files.pop()
        oldest_file.unlink()
        print("Deleted old session"); time.sleep(1)

def web_search(query: str, num_results: int = 3) -> str:
    """Perform a web search and return formatted results."""
    try:
        results = DuckDuckGoSearchAPIWrapper().results(query, num_results)
        if not results:
            print("No search results"); time.sleep(1)
            return "No results"
        
        formatted = []
        links = []
        for result in results:
            link = result.get('link', '').strip()
            if not link:
                continue
            domain = re.sub(r'https?://(www\.)?([^/]+).*', r'\2', link)
            snippet = result.get('snippet', 'No snippet').strip()
            formatted.append(f"[{domain}]({link}): {snippet}")
            links.append(link)
        
        if links:
            formatted.append("\n\nLinks:\n" + "\n".join([f"- {link}" for link in links]))
        
        return "\n\n".join(formatted)
    except Exception as e:
        error_msg = f"Search error: {str(e)[:60]}"
        print(error_msg); time.sleep(1)
        return error_msg

def delete_all_session_histories():
    history_dir = Path(HISTORY_DIR)
    for file in history_dir.glob('*.json'):
        try:
            file.unlink()
            print("Deleted history"); time.sleep(1)
        except Exception as e:
            print(f"Delete error: {str(e)[:60]}"); time.sleep(1)
    return "History cleared"

def get_saved_sessions():
    history_dir = Path(HISTORY_DIR)
    session_files = sorted(history_dir.glob("session_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    return [f.name for f in session_files]

def process_files(files, existing_files, max_files, is_attach=True):
    if not files:
        return "No files", existing_files
    new_files = [f for f in files if os.path.isfile(f) and f not in existing_files]
    if not new_files:
        return "No new files", existing_files
    for f in new_files:
        file_name = Path(f).name
        existing_files = [ef for ef in existing_files if Path(ef).name != file_name]
    available_slots = max_files - len(existing_files)
    processed_files = new_files[:available_slots]
    updated_files = processed_files + existing_files
    if is_attach:
        temporary.session_attached_files = updated_files
    return f"Added {len(processed_files)} files", updated_files

def eject_file(file_list, slot_index, is_attach=True):
    if 0 <= slot_index < len(file_list):
        removed_file = file_list.pop(slot_index)
        if is_attach:
            temporary.session_attached_files = file_list
        status_msg = f"Ejected {Path(removed_file).name}"
    else:
        status_msg = "No file"
    return [file_list, status_msg]