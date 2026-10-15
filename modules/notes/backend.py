"""
AMIKO — Notes Module Backend
Multi-file notes with sharing (servers & groups).
All data is persisted inside AMIKO's encrypted settings,
never on the remote VM.
"""

import os
import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

blueprint = Blueprint('notes', __name__)

# ── Storage ──────────────────────────────────────────────────────────

NOTES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'notes_data.json'
)


def _load_all():
    """Load all notes from disk."""
    if not os.path.exists(NOTES_FILE):
        return []
    try:
        with open(NOTES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_all(notes):
    """Persist notes to disk."""
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    with open(NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)


# ── Visibility filter ────────────────────────────────────────────────

def _is_visible(note, hostname, group_id):
    """
    Decide if a note is visible given the current connection context.

    Sharing rules:
      scope='private':  only visible on the hostname that created it
      scope='all':      visible on every connected server
      scope='members':  visible only on servers listed in sharing.members[]

    Group rules:
      group_scope='local':      visible regardless of group
      group_scope=<group_id>:   visible only when connected to a server
                                belonging to that group
    """
    sharing = note.get('sharing', {})
    scope = sharing.get('scope', 'private')
    members = sharing.get('members', [])
    note_group = note.get('group_scope', 'local')

    # ── Group filter ──
    if note_group != 'local':
        if note_group != group_id:
            return False

    # ── Scope filter ──
    if scope == 'private':
        return note.get('hostname', '') == hostname
    elif scope == 'all':
        return True
    elif scope == 'members':
        # Members list contains server IDs; we also accept hostname match
        # for convenience. The caller should pass the server_id if available.
        return hostname in members or note.get('hostname', '') == hostname
    return False


# ── API Routes ───────────────────────────────────────────────────────

@blueprint.route('/notes/list', methods=['GET'])
def list_notes():
    """Return notes visible to the current connection."""
    hostname = request.args.get('hostname', '').strip()
    group_id = request.args.get('group_id', '').strip()
    server_id = request.args.get('server_id', '').strip()

    if not hostname:
        return jsonify([])

    all_notes = _load_all()
    visible = []
    for n in all_notes:
        # For 'members' scope, check server_id in the members list
        sharing = n.get('sharing', {})
        if sharing.get('scope') == 'members' and server_id:
            if server_id in sharing.get('members', []):
                visible.append(_summary(n))
                continue
        if _is_visible(n, hostname, group_id):
            visible.append(_summary(n))

    # Sort by updated_at descending
    visible.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
    return jsonify(visible)


@blueprint.route('/notes/<note_id>', methods=['GET'])
def get_note(note_id):
    """Load full content of a single note."""
    notes = _load_all()
    for n in notes:
        if n['id'] == note_id:
            return jsonify(n)
    return jsonify({'error': 'Note not found'}), 404


@blueprint.route('/notes/create', methods=['POST'])
def create_note():
    """Create a new note."""
    data = request.get_json() or {}
    hostname = data.get('hostname', '').strip()
    if not hostname:
        return jsonify({'error': 'hostname is required'}), 400

    now = datetime.now(timezone.utc).isoformat()
    note = {
        'id': str(uuid.uuid4()),
        'title': data.get('title', 'Untitled').strip() or 'Untitled',
        'content': data.get('content', ''),
        'hostname': hostname,
        'created_at': now,
        'updated_at': now,
        'sharing': {
            'scope': data.get('scope', 'private'),
            'members': data.get('members', [])
        },
        'group_scope': data.get('group_scope', 'local')
    }

    notes = _load_all()
    notes.append(note)
    _save_all(notes)
    return jsonify(note), 201


@blueprint.route('/notes/<note_id>', methods=['PUT'])
def update_note(note_id):
    """Update an existing note (title, content, sharing)."""
    data = request.get_json() or {}
    notes = _load_all()

    for n in notes:
        if n['id'] == note_id:
            if 'title' in data:
                n['title'] = data['title'].strip() or 'Untitled'
            if 'content' in data:
                n['content'] = data['content']
            if 'scope' in data:
                n.setdefault('sharing', {})['scope'] = data['scope']
            if 'members' in data:
                n.setdefault('sharing', {})['members'] = data['members']
            if 'group_scope' in data:
                n['group_scope'] = data['group_scope']
            n['updated_at'] = datetime.now(timezone.utc).isoformat()
            _save_all(notes)
            return jsonify(n)

    return jsonify({'error': 'Note not found'}), 404


@blueprint.route('/notes/<note_id>', methods=['DELETE'])
def delete_note(note_id):
    """Delete a note by ID."""
    notes = _load_all()
    filtered = [n for n in notes if n['id'] != note_id]
    if len(filtered) == len(notes):
        return jsonify({'error': 'Note not found'}), 404
    _save_all(filtered)
    return jsonify({'success': True})


# ── Helpers ──────────────────────────────────────────────────────────

def _summary(note):
    """Return a lightweight summary for the list view (no full content)."""
    return {
        'id': note['id'],
        'title': note.get('title', 'Untitled'),
        'hostname': note.get('hostname', ''),
        'created_at': note.get('created_at', ''),
        'updated_at': note.get('updated_at', ''),
        'sharing': note.get('sharing', {'scope': 'private', 'members': []}),
        'group_scope': note.get('group_scope', 'local'),
        'preview': (note.get('content', '')[:80] + '…')
                   if len(note.get('content', '')) > 80
                   else note.get('content', '')
    }
