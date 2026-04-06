from flask import Blueprint, request, Response, stream_with_context
import json
from core.config import ssh_sessions
from utils.sftp_helper import connect_ftp, connect_sftp, search_large_files, search_large_files_ftp

blueprint = Blueprint('large_files', __name__)

@blueprint.route('/search-large-files')
def search_large_files_endpoint():
    """
    Search for large files via SFTP/FTP
    SSE endpoint
    """
    session_id = request.args.get('session_id')
    path = request.args.get('path')
    threshold_mb = float(request.args.get('threshold_mb', 50))

    if not all([session_id, path]):
        return "Missing arguments", 400

    if session_id not in ssh_sessions:
        return "Invalid session", 400

    session_data = ssh_sessions[session_id]
    hostname = session_data['hostname']
    port = session_data['port']
    username = session_data['username']
    password = session_data['password']

    def generate():
        client = None
        sftp = None

        try:
            if port == 21:
                yield f"data: {json.dumps({'type': 'info', 'message': f'FTP Connection to {hostname}:{port}...' })}\n\n"
                client = connect_ftp(hostname, port, username, password)
                yield f"data: {json.dumps({'type': 'success', 'message': 'FTP Connection established!' })}\n\n"

                for event in search_large_files_ftp(client, path, threshold_mb):
                    yield f"data: {json.dumps(event)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'info', 'message': f'SFTP Connection to {hostname}:{port}...' })}\n\n"
                client, sftp = connect_sftp(hostname, port, username, password)
                yield f"data: {json.dumps({'type': 'success', 'message': 'SFTP Connection established!' })}\n\n"

                for event in search_large_files(sftp, path, threshold_mb):
                    yield f"data: {json.dumps(event)}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'message': 'Search completed.'})}\n\n"

        except ConnectionRefusedError:
            if port == 21:
                yield f"data: {json.dumps({'type': 'error', 'message': 'FTP Error: Connection Refused (Port 21). FTP service is not active. Try SFTP PORT: 22.' })}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Error: Connection Refused.' })}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Error: {str(e)}'})}\n\n"
        finally:
            if sftp:
                sftp.close()
            if client:
                if port == 21:
                    client.quit()
                else:
                    client.close()

    return Response(stream_with_context(generate()), content_type='text/event-stream')
