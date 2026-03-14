from flask import Blueprint, request, Response, stream_with_context
import json
from utils.sftp_helper import connect_ftp, connect_sftp, search_keyword_in_files, search_keyword_in_files_ftp

blueprint = Blueprint('keyword_search', __name__)

@blueprint.route('/search')
def search():
    """
    Keyword search in files via SFTP/FTP
    SSE endpoint
    """
    hostname = request.args.get('hostname')
    port = int(request.args.get('port', 22))
    username = request.args.get('username')
    password = request.args.get('password')
    path = request.args.get('path')
    keyword = request.args.get('keyword')
    case_sensitive = request.args.get('case_sensitive') == 'true'

    if not all([hostname, port, username, password, path, keyword]):
        return "Missing arguments", 400

    def generate():
        client = None
        sftp = None

        try:
            if port == 21:
                yield f"data: {json.dumps({'type': 'info', 'message': f'FTP Connection to {hostname}:{port}...' })}\n\n"
                client = connect_ftp(hostname, port, username, password)
                yield f"data: {json.dumps({'type': 'success', 'message': 'FTP Connection established!' })}\n\n"

                for event in search_keyword_in_files_ftp(client, path, keyword, case_sensitive):
                    if event.get('type') == 'summary':
                        yield f"data: {json.dumps({'type': 'done', 'message': 'Search completed.'})}\n\n"
                    else:
                        yield f"data: {json.dumps(event)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'info', 'message': f'SFTP Connection to {hostname}:{port}...' })}\n\n"
                client, sftp = connect_sftp(hostname, port, username, password)
                yield f"data: {json.dumps({'type': 'success', 'message': 'SFTP Connection established!' })}\n\n"

                for event in search_keyword_in_files(sftp, path, keyword, case_sensitive):
                    if event.get('type') == 'summary':
                        yield f"data: {json.dumps({'type': 'done', 'message': 'Search completed.'})}\n\n"
                    else:
                        yield f"data: {json.dumps(event)}\n\n"

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
