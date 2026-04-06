from flask import Blueprint, request, jsonify
import json
import os

blueprint = Blueprint('notes', __name__)

NOTES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'notes.json'
)


def _load_notes():
    if not os.path.exists(NOTES_FILE):
        return {}
    try:
        with open(NOTES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_notes(data):
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    with open(NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@blueprint.route('/notes/load', methods=['POST'])
def load_note():
    data = request.get_json() or {}
    hostname = data.get('hostname', '').strip()
    if not hostname:
        return jsonify({'error': 'hostname is required'}), 400

    notes = _load_notes()
    content = notes.get(hostname, '')
    return jsonify({'hostname': hostname, 'content': content})


@blueprint.route('/notes/save', methods=['POST'])
def save_note():
    data = request.get_json()
    hostname = data.get('hostname', '').strip()
    content = data.get('content', '')

    if not hostname:
        return jsonify({'error': 'hostname is required'}), 400

    notes = _load_notes()
    if content:
        notes[hostname] = content
    elif hostname in notes:
        del notes[hostname]

    _save_notes(notes)
    return jsonify({'success': True, 'hostname': hostname})
