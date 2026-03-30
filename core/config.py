import os
from utils.settings_manager import SettingsManager

# Base directory paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES_DIR = os.path.join(BASE_DIR, 'modules')

# Settings Manager (encrypted storage)
settings = SettingsManager()

# Store active SSH sessions
# Structure: {session_id: {'ssh': client, 'sftp': sftp_client, 'shell': channel, 'thread': thread_obj}}
ssh_sessions = {}
