#!/usr/bin/env python3
"""
Duplicate File Scanner Flask Application
Enhanced version with modern UI, smart suggestions, and email notifications
"""

import os
import json
import hashlib
import threading
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import difflib

from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

# Configuration
SCAN_DIRECTORY = os.environ.get('SCAN_DIRECTORY', '/mnt/mc_media/')
SCAN_INTERVAL = 900  # 15 minutes in seconds
CHUNK_SIZE = 50  # Process files in chunks for better responsiveness
EMAIL_RECIPIENT = 'toc@lillybroadcasting.com'
EMAIL_FROM = 'duplicates@system.local'
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'localhost')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '25'))

# File to track seen duplicates
SEEN_DUPLICATES_FILE = 'seen_duplicates.json'

# Global variables
scan_results = {
    'duplicates': {},
    'underscore_files': {},
    'space_files': [],
    'last_scan': None,
    'scanning': False,
    'progress': {
        'current_file': '',
        'processed': 0,
        'total': 0,
        'stage': 'idle'
    }
}

# Track seen duplicates
seen_duplicates = set()

def load_seen_duplicates():
    """Load previously seen duplicates from file"""
    global seen_duplicates
    try:
        if os.path.exists(SEEN_DUPLICATES_FILE):
            with open(SEEN_DUPLICATES_FILE, 'r') as f:
                seen_duplicates = set(json.load(f))
    except Exception as e:
        print(f"Error loading seen duplicates: {e}")
        seen_duplicates = set()

def save_seen_duplicates():
    """Save seen duplicates to file"""
    try:
        with open(SEEN_DUPLICATES_FILE, 'w') as f:
            json.dump(list(seen_duplicates), f)
    except Exception as e:
        print(f"Error saving seen duplicates: {e}")

def send_email_notification(new_duplicates):
    """Send email notification about new duplicates"""
    if not new_duplicates:
        return

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = f'New Duplicate Files Detected - {len(new_duplicates)} groups'

        # Build email body
        body = f"New duplicate files have been detected:\n\n"
        body += f"Total new duplicate groups: {len(new_duplicates)}\n"
        body += f"Scan time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        for group_name, files in new_duplicates.items():
            body += f"\n{'='*60}\n"
            body += f"Group: {group_name}\n"
            body += f"Files ({len(files)}):\n"
            for file_info in files:
                body += f"  - {file_info['path']}\n"
                body += f"    Size: {file_info['size'] / 1024:.2f} KB\n"
                body += f"    Modified: {file_info['modified']}\n"

        msg.attach(MIMEText(body, 'plain'))

        # Send email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.send_message(msg)

        print(f"Email notification sent for {len(new_duplicates)} new duplicate groups")
    except Exception as e:
        print(f"Error sending email notification: {e}")

class FileScanner:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.results = {
            'duplicates': {},
            'underscore_files': {},
            'space_files': [],
            'last_scan': None
        }
        self.stop_scan = False

    def get_file_hash_fast(self, filepath, max_size=1024*1024):  # Only hash first 1MB for speed
        """Calculate MD5 hash of file content (partial for large files)"""
        try:
            hash_md5 = hashlib.md5()
            with open(filepath, "rb") as f:
                data = f.read(max_size)  # Read only first MB
                hash_md5.update(data)
            return hash_md5.hexdigest()
        except:
            return None

    def get_file_info_fast(self, filepath):
        """Get file information quickly"""
        try:
            # Check if file exists first
            if not filepath.exists():
                return None

            stat = filepath.stat()
            return {
                'path': str(filepath),
                'name': filepath.name,
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                'modified_timestamp': stat.st_mtime,
                'extension': filepath.suffix.lower(),
                'hash': None  # Skip hash initially for speed
            }
        except Exception as e:
            print(f"Error getting info for {filepath}: {e}")
            return None

    def is_related_pair(self, file1, file2):
        """Check if two files are related (e.g., TS and XML with same name)"""
        # Get extensions
        ext1 = Path(file1['path']).suffix.lower()
        ext2 = Path(file2['path']).suffix.lower()

        # Get base names without extension
        name1 = Path(file1['path']).stem
        name2 = Path(file2['path']).stem

        # If names are the same and one is TS and the other is XML, they're related
        if name1 == name2:
            if (ext1 == '.ts' and ext2 == '.xml') or (ext1 == '.xml' and ext2 == '.ts'):
                return True
            # Also handle MXF and XML pairs
            if (ext1 == '.mxf' and ext2 == '.xml') or (ext1 == '.xml' and ext2 == '.mxf'):
                return True

        return False

    def suggest_file_to_keep(self, files):
        """Suggest which file to keep based on timestamp and extension"""
        if not files or len(files) < 2:
            return None

        # First, sort by modification time (newest first)
        sorted_by_time = sorted(files, key=lambda x: x.get('modified_timestamp', 0), reverse=True)

        # Get the newest files (might be multiple with same timestamp)
        newest_timestamp = sorted_by_time[0].get('modified_timestamp', 0)
        newest_files = [f for f in sorted_by_time if f.get('modified_timestamp', 0) == newest_timestamp]

        # If only one newest file, return it
        if len(newest_files) == 1:
            return newest_files[0]

        # If multiple files with same timestamp, prefer .MOV files
        mov_files = [f for f in newest_files if f.get('extension', '').lower() == '.mov']
        if mov_files:
            return mov_files[0]

        # Otherwise, return the first newest file
        return newest_files[0]

    def find_similar_names(self, names, threshold=0.8):
        """Group similar filenames together with higher threshold"""
        groups = []
        processed = set()

        for name in names:
            if name in processed:
                continue

            similar_group = [name]
            processed.add(name)

            for other_name in names:
                if other_name in processed:
                    continue

                # Calculate similarity
                similarity = difflib.SequenceMatcher(None, name.lower(), other_name.lower()).ratio()
                if similarity >= threshold:
                    similar_group.append(other_name)
                    processed.add(other_name)

            if len(similar_group) > 1:
                groups.append(similar_group)

        return groups

    def update_progress(self, stage, current_file='', processed=0, total=0):
        """Update scan progress"""
        global scan_results
        scan_results['progress'] = {
            'stage': stage,
            'current_file': str(current_file)[-60:] if current_file else '',  # Show last 60 chars
            'processed': processed,
            'total': total
        }

    def scan_files_generator(self):
        """Generator that yields files for processing"""
        if not self.directory.exists():
            print(f"Directory {self.directory} does not exist")
            return

        file_count = 0
        # First pass - count files
        self.update_progress('Counting files...')
        try:
            for filepath in self.directory.rglob('*'):
                if self.stop_scan:
                    return
                if filepath.is_file():
                    file_count += 1
                    if file_count % 100 == 0:  # Update every 100 files
                        self.update_progress(f'Counting files... ({file_count} found)')
        except Exception as e:
            print(f"Error counting files: {e}")
            return

        print(f"Found {file_count} total files")

        # Second pass - yield files for processing
        processed = 0
        for filepath in self.directory.rglob('*'):
            if self.stop_scan:
                return
            if filepath.is_file():
                processed += 1
                self.update_progress('Processing files', filepath, processed, file_count)
                yield filepath

    def scan(self):
        """Perform the file scan with progress tracking"""
        global seen_duplicates

        self.stop_scan = False

        # Dictionary to store files by name (without extension)
        files_by_name = defaultdict(list)
        underscore_files = defaultdict(list)
        space_files = []

        # Process files in chunks
        chunk = []
        for filepath in self.scan_files_generator():
            if self.stop_scan:
                break

            chunk.append(filepath)

            # Process chunk when full
            if len(chunk) >= CHUNK_SIZE:
                self.process_file_chunk(chunk, files_by_name, underscore_files, space_files)
                chunk = []
                time.sleep(0.01)  # Small delay to allow other operations

        # Process remaining files
        if chunk and not self.stop_scan:
            self.process_file_chunk(chunk, files_by_name, underscore_files, space_files)

        if self.stop_scan:
            return self.results

        # Analyze results
        self.update_progress('Analyzing duplicates...')

        # Find duplicates (same name, different extensions or paths) - only groups with 2+ files
        duplicates = {}
        for name, file_list in files_by_name.items():
            if len(file_list) > 1:
                # Filter out related pairs (TS/XML, MXF/XML)
                # Only flag as duplicate if there are actual duplicates after removing related pairs
                filtered_files = []

                # Group files that aren't related pairs
                for i, file1 in enumerate(file_list):
                    is_part_of_pair = False
                    for j, file2 in enumerate(file_list):
                        if i != j and self.is_related_pair(file1, file2):
                            is_part_of_pair = True
                            break

                    # Only include if not part of a related pair, or if there are more than 2 files
                    if not is_part_of_pair or len(file_list) > 2:
                        filtered_files.append(file1)

                # Special handling: if we have exactly 2 files and they're a TS/XML pair, skip them
                if len(file_list) == 2 and self.is_related_pair(file_list[0], file_list[1]):
                    continue

                # Only add to duplicates if there are still multiple files after filtering
                if len(filtered_files) > 1:
                    # Add suggestion for which file to keep
                    suggested = self.suggest_file_to_keep(filtered_files)
                    duplicates[name] = {
                        'files': filtered_files,
                        'suggested_keep': suggested['path'] if suggested else None
                    }

        # Group underscore files - only groups with 2+ files
        self.update_progress('Grouping underscore files...')
        underscore_groups = {}
        for first_part, file_list in underscore_files.items():
            if len(file_list) > 1:
                # Check if there's a file with the exact name (no underscores) in the main files list
                base_file = None
                underscore_variants = []

                for file_info in file_list:
                    filename = Path(file_info['path']).stem
                    if filename.lower() == first_part:
                        # This is the base file without underscores
                        base_file = file_info
                    else:
                        # This is a variant with underscores
                        underscore_variants.append(file_info)

                # If no base file, all files are variants
                if base_file is None:
                    underscore_variants = file_list

                # Store the group with metadata about base file
                group_data = {
                    'files': file_list,
                    'base_file': base_file,
                    'variants': underscore_variants,
                    'has_base': base_file is not None
                }
                underscore_groups[first_part] = group_data

        self.update_progress('Complete')

        # Check for new duplicates and send email if any
        new_duplicates = {}
        for name, dup_data in duplicates.items():
            # Create a unique key for this duplicate group
            file_paths = sorted([f['path'] for f in dup_data['files']])
            dup_key = f"{name}::{','.join(file_paths)}"

            if dup_key not in seen_duplicates:
                new_duplicates[name] = dup_data['files']
                seen_duplicates.add(dup_key)

        # Send email if there are new duplicates
        if new_duplicates:
            print(f"Found {len(new_duplicates)} new duplicate groups")
            send_email_notification(new_duplicates)
            save_seen_duplicates()

        self.results = {
            'duplicates': duplicates,
            'underscore_files': underscore_groups,
            'space_files': space_files,
            'last_scan': datetime.now().isoformat()
        }

        return self.results

    def process_file_chunk(self, chunk, files_by_name, underscore_files, space_files):
        """Process a chunk of files"""
        for filepath in chunk:
            if self.stop_scan:
                break

            try:
                # Skip .bxf files as they are associated with .mxf and .ts files
                if filepath.suffix.lower() == '.bxf':
                    continue

                file_info = self.get_file_info_fast(filepath)
                if not file_info:
                    continue

                filename = filepath.name
                name_without_ext = filepath.stem

                # Group by filename without extension
                files_by_name[name_without_ext.lower()].append(file_info)

                # Check for underscores - group by first part before underscore
                if '_' in filename:
                    # Get the first part before the first underscore
                    first_part = filename.split('_')[0].lower()
                    underscore_files[first_part].append(file_info)

                # Check for spaces
                if ' ' in filename:
                    space_files.append(file_info)

            except Exception as e:
                print(f"Error processing {filepath}: {e}")
                continue

def background_scanner():
    """Background thread for periodic scanning"""
    global scan_results

    # Load seen duplicates at startup
    load_seen_duplicates()

    scanner = FileScanner(SCAN_DIRECTORY)

    while True:
        try:
            print(f"Starting background scan at {datetime.now()}")
            scan_results['scanning'] = True
            results = scanner.scan()
            scan_results.update(results)
            scan_results['scanning'] = False
            scan_results['progress']['stage'] = 'idle'
            print(f"Background scan completed at {datetime.now()}")
        except Exception as e:
            print(f"Error during background scan: {e}")
            scan_results['scanning'] = False
            scan_results['progress']['stage'] = 'error'

        time.sleep(SCAN_INTERVAL)

# Start background scanner
scanner_thread = threading.Thread(target=background_scanner, daemon=True)
scanner_thread.start()

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html',
                         results=scan_results,
                         scan_directory=SCAN_DIRECTORY,
                         scan_interval_minutes=SCAN_INTERVAL // 60)

@app.route('/scan')
def manual_scan():
    """Trigger manual scan"""
    global scan_results
    if not scan_results['scanning']:
        def run_scan():
            global scan_results
            scanner = FileScanner(SCAN_DIRECTORY)
            scan_results['scanning'] = True
            try:
                results = scanner.scan()
                scan_results.update(results)
            except Exception as e:
                print(f"Manual scan error: {e}")
            finally:
                scan_results['scanning'] = False
                scan_results['progress']['stage'] = 'idle'

        # Run scan in background thread
        scan_thread = threading.Thread(target=run_scan, daemon=True)
        scan_thread.start()

    return redirect(url_for('index'))

@app.route('/api/delete', methods=['POST'])
def delete_file():
    """Delete a selected file"""
    data = request.get_json()
    filepath = data.get('filepath')

    if not filepath:
        return jsonify({'error': 'No filepath provided'}), 400

    try:
        file_path = Path(filepath)

        # Security check - ensure file is within scan directory
        scan_dir_resolved = Path(SCAN_DIRECTORY).resolve()
        file_path_resolved = file_path.resolve()

        if not str(file_path_resolved).startswith(str(scan_dir_resolved)):
            return jsonify({'error': 'File outside scan directory'}), 403

        # Check if file exists
        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404

        if not file_path.is_file():
            return jsonify({'error': 'Not a file'}), 400

        # Delete the file
        file_path.unlink()

        # Verify deletion
        if file_path.exists():
            return jsonify({'error': 'File deletion failed'}), 500

        print(f"Successfully deleted: {filepath}")

        # Return success so the UI can remove it from the DOM
        return jsonify({
            'success': True,
            'message': f'Successfully deleted {file_path.name}',
            'remove_from_ui': True
        })
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        print(f"Error deleting {filepath}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/rename', methods=['POST'])
def rename_file():
    """Rename a selected file"""
    data = request.get_json()
    old_path = data.get('old_path')
    new_name = data.get('new_name')

    if not old_path or not new_name:
        return jsonify({'error': 'Missing parameters'}), 400

    try:
        old_file_path = Path(old_path)
        if not old_file_path.exists():
            return jsonify({'error': 'File not found'}), 404

        # Security check
        if not str(old_file_path.resolve()).startswith(str(Path(SCAN_DIRECTORY).resolve())):
            return jsonify({'error': 'File outside scan directory'}), 403

        new_name = secure_filename(new_name)
        new_file_path = old_file_path.parent / new_name

        if new_file_path.exists():
            return jsonify({'error': 'File with new name already exists'}), 400

        old_file_path.rename(new_file_path)

        return jsonify({'success': True, 'message': f'Renamed to {new_name}', 'refresh': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get current scan status"""
    return jsonify({
        'scanning': scan_results['scanning'],
        'last_scan': scan_results['last_scan'],
        'duplicate_count': len(scan_results['duplicates']),
        'underscore_count': len(scan_results['underscore_files']),
        'space_count': len(scan_results['space_files']),
        'progress': scan_results['progress']
    })

@app.route('/api/stop_scan', methods=['POST'])
def stop_scan():
    """Stop current scan"""
    global scan_results
    scan_results['progress']['stage'] = 'stopping'
    return jsonify({'success': True, 'message': 'Scan stop requested'})

@app.route('/api/delete_variants', methods=['POST'])
def delete_variants():
    """Delete all underscore variants in a group, keeping only the base file"""
    data = request.get_json()
    filepaths = data.get('filepaths', [])

    if not filepaths:
        return jsonify({'error': 'No filepaths provided'}), 400

    deleted_files = []
    errors = []

    for filepath in filepaths:
        try:
            file_path = Path(filepath)
            if file_path.exists() and file_path.is_file():
                # Security check - ensure file is within scan directory
                if not str(file_path.resolve()).startswith(str(Path(SCAN_DIRECTORY).resolve())):
                    errors.append(f'File outside scan directory: {filepath}')
                    continue

                file_path.unlink()
                deleted_files.append(filepath)
            else:
                errors.append(f'File not found: {filepath}')
        except Exception as e:
            errors.append(f'Error deleting {filepath}: {str(e)}')

    if deleted_files:
        return jsonify({
            'success': True,
            'message': f'Deleted {len(deleted_files)} variant files',
            'deleted_files': deleted_files,
            'errors': errors,
            'remove_from_ui': True
        })
    else:
        return jsonify({'error': 'No files were deleted', 'errors': errors}), 500

# HTML Template (embedded with modern, enhanced UI)
template_html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Duplicate File Manager</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --primary-color: #2563eb;
            --primary-hover: #1d4ed8;
            --danger-color: #dc2626;
            --danger-hover: #b91c1c;
            --success-color: #16a34a;
            --success-hover: #15803d;
            --warning-color: #f59e0b;
            --warning-hover: #d97706;
            --background: #f8fafc;
            --surface: #ffffff;
            --text-primary: #1e293b;
            --text-secondary: #64748b;
            --border-color: #e2e8f0;
            --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
            --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: var(--background);
            color: var(--text-primary);
            line-height: 1.6;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }

        .header {
            background: var(--surface);
            padding: 2rem;
            border-radius: 12px;
            box-shadow: var(--shadow-md);
            margin-bottom: 2rem;
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        h1 {
            font-size: 2rem;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .header-icon {
            font-size: 2rem;
        }

        .scan-info {
            display: flex;
            align-items: center;
            gap: 1rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        .scan-path {
            font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
            background: var(--background);
            padding: 0.5rem 1rem;
            border-radius: 6px;
        }

        .btn {
            padding: 0.625rem 1.25rem;
            border-radius: 8px;
            border: none;
            font-weight: 500;
            font-size: 0.875rem;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
        }

        .btn:hover {
            transform: translateY(-1px);
            box-shadow: var(--shadow-md);
        }

        .btn:active {
            transform: translateY(0);
        }

        .btn-primary {
            background: var(--primary-color);
            color: white;
        }

        .btn-primary:hover {
            background: var(--primary-hover);
        }

        .btn-danger {
            background: var(--danger-color);
            color: white;
        }

        .btn-danger:hover {
            background: var(--danger-hover);
        }

        .btn-warning {
            background: var(--warning-color);
            color: white;
        }

        .btn-warning:hover {
            background: var(--warning-hover);
        }

        .btn-success {
            background: var(--success-color);
            color: white;
        }

        .btn-success:hover {
            background: var(--success-hover);
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        .status {
            background: var(--surface);
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: var(--shadow-sm);
            margin-bottom: 2rem;
        }

        .status.scanning {
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
            border-left: 4px solid var(--warning-color);
        }

        .status.complete {
            background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
            border-left: 4px solid var(--success-color);
        }

        .progress-bar {
            width: 100%;
            height: 8px;
            background-color: rgba(0, 0, 0, 0.1);
            border-radius: 4px;
            margin-top: 1rem;
            overflow: hidden;
        }

        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--primary-color), var(--primary-hover));
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .progress-text {
            font-size: 0.875rem;
            margin-top: 0.5rem;
            color: var(--text-secondary);
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .stat-card {
            background: var(--surface);
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: var(--shadow-sm);
            text-align: center;
            transition: all 0.3s;
        }

        .stat-card:hover {
            box-shadow: var(--shadow-md);
            transform: translateY(-2px);
        }

        .stat-number {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--primary-color), var(--primary-hover));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .stat-label {
            font-size: 0.875rem;
            color: var(--text-secondary);
            margin-top: 0.5rem;
        }

        .section {
            margin-bottom: 3rem;
        }

        .section-header {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 1.5rem;
            padding-bottom: 0.75rem;
            border-bottom: 2px solid var(--border-color);
        }

        .section-header h2 {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
        }

        .section-icon {
            font-size: 1.5rem;
        }

        .file-group {
            background: var(--surface);
            border-radius: 12px;
            margin-bottom: 1.5rem;
            box-shadow: var(--shadow-sm);
            overflow: hidden;
            transition: all 0.3s;
        }

        .file-group:hover {
            box-shadow: var(--shadow-md);
        }

        .file-group-header {
            background: linear-gradient(135deg, #f1f5f9, #e2e8f0);
            padding: 1.25rem 1.5rem;
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
        }

        .group-title {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            font-size: 1rem;
        }

        .group-info {
            color: var(--text-secondary);
            font-weight: 400;
            font-size: 0.875rem;
        }

        .suggestion-badge {
            background: linear-gradient(135deg, #10b981, #059669);
            color: white;
            padding: 0.375rem 0.75rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .file-item {
            padding: 1.25rem 1.5rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
        }

        .file-item:last-child {
            border-bottom: none;
        }

        .file-item:hover {
            background: var(--background);
        }

        .file-item.suggested {
            background: linear-gradient(90deg, rgba(16, 185, 129, 0.05), rgba(5, 150, 105, 0.05));
            border-left: 3px solid var(--success-color);
        }

        .file-info {
            flex-grow: 1;
        }

        .file-path {
            font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
            color: var(--text-primary);
            font-size: 0.875rem;
            word-break: break-all;
            margin-bottom: 0.5rem;
        }

        .file-meta {
            display: flex;
            gap: 1.5rem;
            color: var(--text-secondary);
            font-size: 0.8125rem;
        }

        .file-meta-item {
            display: flex;
            align-items: center;
            gap: 0.375rem;
        }

        .file-actions {
            display: flex;
            gap: 0.75rem;
            flex-shrink: 0;
        }

        .toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: var(--text-primary);
            color: white;
            padding: 1rem 1.5rem;
            border-radius: 8px;
            box-shadow: var(--shadow-lg);
            opacity: 0;
            transform: translateY(100px);
            transition: all 0.3s;
            z-index: 1000;
            max-width: 400px;
        }

        .toast.show {
            opacity: 1;
            transform: translateY(0);
        }

        .toast.success {
            background: var(--success-color);
        }

        .toast.error {
            background: var(--danger-color);
        }

        .empty-state {
            text-align: center;
            padding: 3rem;
            color: var(--text-secondary);
        }

        .empty-state-icon {
            font-size: 4rem;
            margin-bottom: 1rem;
            opacity: 0.5;
        }

        @media (max-width: 768px) {
            .container {
                padding: 1rem;
            }

            .header-top {
                flex-direction: column;
                gap: 1rem;
            }

            .file-item {
                flex-direction: column;
                align-items: flex-start;
                gap: 1rem;
            }

            .file-actions {
                width: 100%;
            }

            .stats {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-top">
                <h1>
                    <span class="header-icon">🔍</span>
                    Duplicate File Manager
                </h1>
                <div class="scan-info">
                    <div class="scan-path">{{ scan_directory }}</div>
                    <button id="scanBtn" onclick="manualScan()" class="btn btn-primary">
                        ↻ Manual Scan
                    </button>
                </div>
            </div>
            <div style="font-size: 0.875rem; color: var(--text-secondary);">
                Auto-scan every {{ scan_interval_minutes }} minutes • Email notifications to {{ email_recipient }}
            </div>
        </div>

        <div id="status" class="status">
            {% if results.scanning %}
                <div class="scanning">
                    <div style="font-weight: 600;">🔄 <span id="scan-stage">Scanning in progress...</span></div>
                    <div class="progress-bar">
                        <div id="progress-bar-fill" class="progress-bar-fill" style="width: 0%"></div>
                    </div>
                    <div id="progress-text" class="progress-text"></div>
                </div>
            {% else %}
                <div class="complete">
                    <strong>✅ Last scan:</strong> {{ results.last_scan or 'Never' }}
                </div>
            {% endif %}
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="duplicate-count">{{ results.duplicates|length }}</div>
                <div class="stat-label">Duplicate Groups</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="underscore-count">{{ results.underscore_files|length }}</div>
                <div class="stat-label">Underscore Groups</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="space-count">{{ results.space_files|length }}</div>
                <div class="stat-label">Files with Spaces</div>
            </div>
        </div>

        <!-- Duplicates Section -->
        <div class="section">
            <div class="section-header">
                <span class="section-icon">📁</span>
                <h2>Duplicate Files</h2>
            </div>
            <div id="duplicates-content">
                {% if results.duplicates %}
                    {% for group_name, dup_data in results.duplicates.items() %}
                    <div class="file-group">
                        <div class="file-group-header">
                            <div class="group-title">
                                <span>{{ group_name }}</span>
                                <span class="group-info">({{ dup_data.files|length }} files)</span>
                            </div>
                            {% if dup_data.suggested_keep %}
                            <div class="suggestion-badge">
                                💡 Smart suggestion available
                            </div>
                            {% endif %}
                        </div>
                        {% for file in dup_data.files %}
                        <div class="file-item {% if dup_data.suggested_keep == file.path %}suggested{% endif %}" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">
                                    {{ file.path }}
                                    {% if dup_data.suggested_keep == file.path %}
                                    <span style="color: var(--success-color); font-weight: 600; margin-left: 0.5rem;">✓ RECOMMENDED</span>
                                    {% endif %}
                                </div>
                                <div class="file-meta">
                                    <div class="file-meta-item">
                                        <span>📦</span>
                                        <span>{{ "%.2f"|format(file.size / 1024) }} KB</span>
                                    </div>
                                    <div class="file-meta-item">
                                        <span>🕒</span>
                                        <span>{{ file.modified }}</span>
                                    </div>
                                    <div class="file-meta-item">
                                        <span>📄</span>
                                        <span>{{ file.extension }}</span>
                                    </div>
                                </div>
                            </div>
                            <div class="file-actions">
                                <button class="btn btn-warning" onclick="showRename('{{ file.path }}')">Rename</button>
                                <button class="btn btn-danger" onclick="deleteFile('{{ file.path }}')">Delete</button>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-state">
                        <div class="empty-state-icon">✨</div>
                        <p>No duplicate files found. Your media library is clean!</p>
                    </div>
                {% endif %}
            </div>
        </div>

        <!-- Underscore Files Section -->
        <div class="section">
            <div class="section-header">
                <span class="section-icon">📝</span>
                <h2>Files with Similar Prefixes</h2>
            </div>
            <div id="underscore-content">
                {% if results.underscore_files %}
                    {% for group_name, group_data in results.underscore_files.items() %}
                    <div class="file-group">
                        <div class="file-group-header">
                            <div class="group-title">
                                <span>{{ group_name }}</span>
                                <span class="group-info">({{ group_data.files|length }} files)</span>
                            </div>
                            {% if group_data.variants %}
                            <button class="btn btn-danger"
                                    onclick="deleteVariants('{{ group_name }}', {{ group_data.variants|map(attribute='path')|list|tojson|safe }})">
                                🗑️ Delete All {% if group_data.has_base %}Variants{% else %}Files{% endif %}
                            </button>
                            {% endif %}
                        </div>

                        {% for file in group_data.files %}
                        <div class="file-item" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">
                                    {{ file.path }}
                                    {% if group_data.base_file and file.path == group_data.base_file.path %}
                                    <span style="color: var(--success-color); font-weight: 600; margin-left: 0.5rem;">[BASE FILE]</span>
                                    {% elif group_data.has_base %}
                                    <span style="color: var(--warning-color); margin-left: 0.5rem;">[VARIANT]</span>
                                    {% endif %}
                                </div>
                                <div class="file-meta">
                                    <div class="file-meta-item">
                                        <span>📦</span>
                                        <span>{{ "%.2f"|format(file.size / 1024) }} KB</span>
                                    </div>
                                    <div class="file-meta-item">
                                        <span>🕒</span>
                                        <span>{{ file.modified }}</span>
                                    </div>
                                </div>
                            </div>
                            <div class="file-actions">
                                <button class="btn btn-warning" onclick="showRename('{{ file.path }}')">Rename</button>
                                <button class="btn btn-danger" onclick="deleteFile('{{ file.path }}')">Delete</button>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-state">
                        <div class="empty-state-icon">✨</div>
                        <p>No files with similar prefixes found.</p>
                    </div>
                {% endif %}
            </div>
        </div>

        <!-- Space Files Section -->
        <div class="section">
            <div class="section-header">
                <span class="section-icon">⚠️</span>
                <h2>Files with Spaces</h2>
            </div>
            <div id="space-content">
                {% if results.space_files %}
                    <div class="file-group">
                        <div class="file-group-header">
                            <div class="group-title">
                                <span>Files with Spaces</span>
                                <span class="group-info">({{ results.space_files|length }} files)</span>
                            </div>
                        </div>
                        {% for file in results.space_files %}
                        <div class="file-item" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">{{ file.path }}</div>
                                <div class="file-meta">
                                    <div class="file-meta-item">
                                        <span>📦</span>
                                        <span>{{ "%.2f"|format(file.size / 1024) }} KB</span>
                                    </div>
                                    <div class="file-meta-item">
                                        <span>🕒</span>
                                        <span>{{ file.modified }}</span>
                                    </div>
                                </div>
                            </div>
                            <div class="file-actions">
                                <button class="btn btn-warning" onclick="showRename('{{ file.path }}')">Rename</button>
                                <button class="btn btn-danger" onclick="deleteFile('{{ file.path }}')">Delete</button>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                {% else %}
                    <div class="empty-state">
                        <div class="empty-state-icon">✨</div>
                        <p>No files with spaces found.</p>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Toast notification container -->
    <div id="toast" class="toast"></div>

    <script>
        // Auto-refresh status every 2 seconds during scanning, 10 seconds otherwise
        let updateInterval = 10000;
        let intervalId = setInterval(updateStatus, updateInterval);

        function updateStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    const statusDiv = document.getElementById('status');
                    const scanBtn = document.getElementById('scanBtn');

                    // Update stats
                    document.getElementById('duplicate-count').textContent = data.duplicate_count;
                    document.getElementById('underscore-count').textContent = data.underscore_count;
                    document.getElementById('space-count').textContent = data.space_count;

                    if (data.scanning) {
                        // Update scan button
                        scanBtn.textContent = '⏳ Scanning...';
                        scanBtn.disabled = true;

                        // Update progress
                        const progress = data.progress;
                        const percentage = progress.total > 0 ? (progress.processed / progress.total * 100) : 0;

                        statusDiv.innerHTML = `
                            <div class="scanning">
                                <div style="font-weight: 600;">🔄 <span id="scan-stage">${progress.stage}</span></div>
                                <div class="progress-bar">
                                    <div class="progress-bar-fill" style="width: ${percentage}%"></div>
                                </div>
                                <div class="progress-text">
                                    ${progress.processed}/${progress.total} files processed
                                    ${progress.current_file ? '<br>Current: ' + progress.current_file : ''}
                                </div>
                            </div>
                        `;

                        // Update more frequently during scan
                        if (updateInterval !== 2000) {
                            clearInterval(intervalId);
                            updateInterval = 2000;
                            intervalId = setInterval(updateStatus, updateInterval);
                        }
                    } else {
                        // Reset scan button
                        scanBtn.textContent = '↻ Manual Scan';
                        scanBtn.disabled = false;

                        statusDiv.innerHTML = `<div class="complete"><strong>✅ Last scan:</strong> ${data.last_scan || 'Never'}</div>`;

                        // Update less frequently when not scanning
                        if (updateInterval !== 10000) {
                            clearInterval(intervalId);
                            updateInterval = 10000;
                            intervalId = setInterval(updateStatus, updateInterval);
                        }
                    }
                })
                .catch(error => {
                    console.error('Status update error:', error);
                });
        }

        function manualScan() {
            const scanBtn = document.getElementById('scanBtn');
            if (!scanBtn.disabled) {
                window.location.href = '/scan';
            }
        }

        function deleteFile(filepath) {
            if (confirm(`Are you sure you want to delete this file?\\n\\n${filepath.split('/').pop()}`)) {
                fetch('/api/delete', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({filepath: filepath})
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        const filename = filepath.split('/').pop();
                        showToast(`✓ Deleted: ${filename}`, 'success');

                        if (data.remove_from_ui) {
                            // Find and remove the file item from DOM
                            const fileItem = document.querySelector(`.file-item[data-filepath="${filepath}"]`);
                            if (fileItem) {
                                const fileGroup = fileItem.closest('.file-group');
                                fileItem.remove();

                                // Check if group should be removed
                                const remainingFiles = fileGroup.querySelectorAll('.file-item');
                                if (remainingFiles.length <= 1) {
                                    fileGroup.remove();
                                    updateEmptyStateMessages();
                                } else {
                                    // Update count
                                    const groupInfo = fileGroup.querySelector('.group-info');
                                    if (groupInfo) {
                                        groupInfo.textContent = `(${remainingFiles.length} files)`;
                                    }
                                }

                                updateStatsAfterDelete();
                            }
                        }
                    } else {
                        showToast(`Error: ${data.error}`, 'error');
                    }
                })
                .catch(error => {
                    console.error('Delete error:', error);
                    showToast('Error deleting file', 'error');
                });
            }
        }

        function updateEmptyStateMessages() {
            const sections = [
                { id: 'duplicates-content', message: 'No duplicate files found. Your media library is clean!' },
                { id: 'underscore-content', message: 'No files with similar prefixes found.' },
                { id: 'space-content', message: 'No files with spaces found.' }
            ];

            sections.forEach(section => {
                const container = document.getElementById(section.id);
                const groups = container.querySelectorAll('.file-group');

                if (groups.length === 0) {
                    const existingMessage = container.querySelector('.empty-state');
                    if (!existingMessage) {
                        container.innerHTML = `
                            <div class="empty-state">
                                <div class="empty-state-icon">✨</div>
                                <p>${section.message}</p>
                            </div>
                        `;
                    }
                }
            });
        }

        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = `toast ${type}`;

            setTimeout(() => {
                toast.classList.add('show');
            }, 100);

            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        function updateStatsAfterDelete() {
            const duplicateGroups = document.querySelectorAll('#duplicates-content .file-group').length;
            const underscoreGroups = document.querySelectorAll('#underscore-content .file-group').length;
            const spaceFiles = document.querySelectorAll('#space-content .file-item').length;

            document.getElementById('duplicate-count').textContent = duplicateGroups;
            document.getElementById('underscore-count').textContent = underscoreGroups;
            document.getElementById('space-count').textContent = spaceFiles;
        }

        function showRename(filepath) {
            const currentName = filepath.split('/').pop();
            const newName = prompt('Enter new filename:', currentName);
            if (newName && newName !== currentName) {
                renameFile(filepath, newName);
            }
        }

        function renameFile(oldPath, newName) {
            fetch('/api/rename', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({old_path: oldPath, new_name: newName})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showToast(`✓ Renamed to: ${newName}`, 'success');
                    if (data.refresh) {
                        setTimeout(() => location.reload(), 1000);
                    }
                } else {
                    showToast(`Error: ${data.error}`, 'error');
                }
            })
            .catch(error => {
                console.error('Rename error:', error);
                showToast('Error renaming file', 'error');
            });
        }

        function deleteVariants(groupName, variantPaths) {
            if (!variantPaths || variantPaths.length === 0) {
                showToast('No files to delete', 'error');
                return;
            }

            const variantCount = variantPaths.length;
            const fileNames = variantPaths.map(p => p.split('/').pop()).join('\\n');

            const confirmMessage = `Are you sure you want to delete ${variantCount} files for "${groupName}"?\\n\\nFiles to delete:\\n${fileNames}`;

            if (confirm(confirmMessage)) {
                fetch('/api/delete_variants', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({filepaths: variantPaths})
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(`✓ Deleted ${data.deleted_files.length} files`, 'success');

                        if (data.remove_from_ui) {
                            data.deleted_files.forEach(filepath => {
                                const fileItem = document.querySelector(`.file-item[data-filepath="${filepath}"]`);
                                if (fileItem) {
                                    fileItem.remove();
                                }
                            });

                            const groupHeaders = document.querySelectorAll('.file-group-header');
                            groupHeaders.forEach(header => {
                                if (header.textContent.includes(groupName)) {
                                    const fileGroup = header.closest('.file-group');
                                    const remainingFiles = fileGroup.querySelectorAll('.file-item');

                                    if (remainingFiles.length <= 1) {
                                        fileGroup.remove();
                                        updateEmptyStateMessages();
                                    } else {
                                        const deleteBtn = header.querySelector('.btn-danger');
                                        if (deleteBtn) deleteBtn.remove();

                                        const groupInfo = header.querySelector('.group-info');
                                        if (groupInfo) {
                                            groupInfo.textContent = `(${remainingFiles.length} files)`;
                                        }
                                    }
                                }
                            });

                            updateStatsAfterDelete();
                        }

                        if (data.errors && data.errors.length > 0) {
                            showToast(`Some errors occurred: ${data.errors.length} files had issues`, 'error');
                        }
                    } else {
                        showToast(`Error: ${data.error}`, 'error');
                    }
                })
                .catch(error => {
                    console.error('Delete variants error:', error);
                    showToast('Error deleting files', 'error');
                });
            }
        }
    </script>
</body>
</html>
'''

# Create templates directory and save template
os.makedirs('templates', exist_ok=True)
with open('templates/index.html', 'w') as f:
    f.write(template_html.replace('{{ email_recipient }}', EMAIL_RECIPIENT))

if __name__ == '__main__':
    # Create scan directory if it doesn't exist
    os.makedirs(SCAN_DIRECTORY, exist_ok=True)

    print(f"Starting Duplicate File Manager")
    print(f"Scan Directory: {SCAN_DIRECTORY}")
    print(f"Scan Interval: {SCAN_INTERVAL // 60} minutes")
    print(f"Email Notifications: {EMAIL_RECIPIENT}")
    print(f"Dashboard: http://localhost:5590")

    app.run(debug=True, host='0.0.0.0', port=5590)
