#!/usr/bin/env python3
"""
Duplicate File Scanner Flask Application
Optimized version with progress tracking and chunked scanning
"""

import os
import json
import hashlib
import threading
import time
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
SCAN_INTERVAL = 300  # 5 minutes in seconds
CHUNK_SIZE = 50  # Process files in chunks for better responsiveness

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
                'hash': None  # Skip hash initially for speed
            }
        except Exception as e:
            print(f"Error getting info for {filepath}: {e}")
            return None
    
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
                duplicates[name] = file_list
        
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
        
        # Skip the complex similarity matching for underscore files since we now have a cleaner grouping
        # The first-part-before-underscore approach should give us better, more relevant groups
        
        self.update_progress('Complete')
        
        # Debug: Print the structure we're creating
        print(f"Debug - Duplicates: {len(duplicates)} groups")
        print(f"Debug - Underscore groups: {len(underscore_groups)} groups")
        if underscore_groups:
            first_key = list(underscore_groups.keys())[0]
            print(f"Debug - First underscore group structure: {type(underscore_groups[first_key])}")
            print(f"Debug - First underscore group keys: {underscore_groups[first_key].keys() if isinstance(underscore_groups[first_key], dict) else 'Not a dict'}")
        
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
                         scan_directory=SCAN_DIRECTORY)

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
        if file_path.exists() and file_path.is_file():
            # Security check - ensure file is within scan directory
            if not str(file_path.resolve()).startswith(str(Path(SCAN_DIRECTORY).resolve())):
                return jsonify({'error': 'File outside scan directory'}), 403
            
            file_path.unlink()
            
            # Don't rescan - just return success so the UI can remove it from the DOM
            return jsonify({'success': True, 'message': f'Deleted {filepath}', 'remove_from_ui': True})
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
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
        
        # Trigger a quick rescan to update the dashboard
        trigger_quick_rescan()
        
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
    # This is a simple approach - in a production app you'd want more sophisticated control
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
    """Trigger a quick background rescan after file operations"""
    global scan_results
    if not scan_results['scanning']:
        def run_quick_scan():
            global scan_results
            scanner = FileScanner(SCAN_DIRECTORY)
            scan_results['scanning'] = True
            try:
                results = scanner.scan()
                scan_results.update(results)
            except Exception as e:
                print(f"Quick rescan error: {e}")
            finally:
                scan_results['scanning'] = False
                scan_results['progress']['stage'] = 'idle'
        
        # Run scan in background thread
        scan_thread = threading.Thread(target=run_quick_scan, daemon=True)
        scan_thread.start()

# HTML Template (embedded with progress tracking)
template_html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Duplicate File Scanner</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #eee;
        }
        .btn {
            padding: 10px 20px;
            background: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            border: none;
            cursor: pointer;
            margin-left: 10px;
        }
        .btn:hover {
            background: #0056b3;
        }
        .btn:disabled {
            background: #6c757d;
            cursor: not-allowed;
        }
        .btn-danger {
            background: #dc3545;
        }
        .btn-danger:hover {
            background: #c82333;
        }
        .btn-warning {
            background: #ffc107;
            color: #212529;
        }
        .status {
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
        }
        .status.scanning {
            background: #fff3cd;
            border: 1px solid #ffeaa7;
        }
        .status.complete {
            background: #d4edda;
            border: 1px solid #c3e6cb;
        }
        .progress-bar {
            width: 100%;
            background-color: #e9ecef;
            border-radius: 4px;
            margin-top: 10px;
        }
        .progress-bar-fill {
            height: 20px;
            background-color: #007bff;
            border-radius: 4px;
            transition: width 0.3s ease;
        }
        .progress-text {
            font-size: 0.9em;
            margin-top: 5px;
            color: #666;
        }
        .section {
            margin-bottom: 40px;
        }
        .section h2 {
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }
        .file-group {
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-bottom: 20px;
            overflow: hidden;
        }
        .file-group-header {
            background: #f8f9fa;
            padding: 15px;
            font-weight: bold;
            border-bottom: 1px solid #ddd;
        }
        .file-item {
            padding: 15px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .file-item:last-child {
            border-bottom: none;
        }
        .file-info {
            flex-grow: 1;
        }
        .file-path {
            font-family: monospace;
            color: #666;
            font-size: 0.9em;
            word-break: break-all;
        }
        .file-meta {
            color: #888;
            font-size: 0.8em;
            margin-top: 5px;
        }
        .file-actions {
            display: flex;
            gap: 10px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 4px;
            text-align: center;
        }
        .stat-number {
            font-size: 2em;
            font-weight: bold;
            color: #007bff;
        }
        .scan-controls {
            display: flex;
            align-items: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Duplicate File Scanner</h1>
            <div class="scan-controls">
                <span>Scanning: {{ scan_directory }}</span>
                <button id="scanBtn" onclick="manualScan()" class="btn">Manual Scan</button>
            </div>
        </div>
        
        <div id="status" class="status">
            {% if results.scanning %}
                <div class="scanning">
                    <div>🔄 <span id="scan-stage">Scanning in progress...</span></div>
                    <div class="progress-bar">
                        <div id="progress-bar-fill" class="progress-bar-fill" style="width: 0%"></div>
                    </div>
                    <div id="progress-text" class="progress-text"></div>
                </div>
            {% else %}
                <div class="complete">
                    ✅ Last scan: {{ results.last_scan or 'Never' }}
                </div>
            {% endif %}
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="duplicate-count">{{ results.duplicates|length }}</div>
                <div>Duplicate Groups</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="underscore-count">{{ results.underscore_files|length }}</div>
                <div>Underscore Groups</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="space-count">{{ results.space_files|length }}</div>
                <div>Files with Spaces</div>
            </div>
        </div>
        
        <!-- Duplicates Section -->
        <div class="section">
            <h2>Duplicate Files (Same Name, Different Extensions/Paths)</h2>
            <div id="duplicates-content">
                {% if results.duplicates %}
                    {% for group_name, files in results.duplicates.items() %}
                    <div class="file-group">
                        <div class="file-group-header">{{ group_name }} <span class="group-info">({{ files|length }} files)</span></div>
                        {% for file in files %}
                        <div class="file-item" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">{{ file.path }}</div>
                                <div class="file-meta">
                                    Size: {{ "%.2f"|format(file.size / 1024) }} KB | 
                                    Modified: {{ file.modified }}
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
                    <p>No duplicate files found.</p>
                {% endif %}
            </div>
        </div>
        
        <!-- Underscore Files Section -->
        <div class="section">
            <h2>Files with Similar Prefixes (Before First Underscore)</h2>
            <div id="underscore-content">
                {% if results.underscore_files %}
                    {% for group_name, group_data in results.underscore_files.items() %}
                    <div class="file-group">
                        <div class="file-group-header">
                            {{ group_name }} 
                            <span class="group-info">({{ group_data.files|length if group_data.files else group_data|length }} files)</span>
                            <!-- Debug info -->
                            <small style="color: #666; margin-left: 10px;">[Type: {{ group_data.__class__.__name__ if group_data.__class__ else 'unknown' }}]</small>
                            
                            {% if group_data.variants %}
                            <button class="btn btn-danger" style="float: right; margin-left: 10px;" 
                                    onclick="deleteVariants('{{ group_name }}', {{ group_data.variants|map(attribute='path')|list|tojson|safe }})">
                                🗑️ Delete All {% if group_data.has_base %}Variants (Keep Base){% else %}Files{% endif %}
                            </button>
                            {% elif group_data is iterable and group_data is not string %}
                            <button class="btn btn-danger" style="float: right; margin-left: 10px;" 
                                    onclick="deleteVariants('{{ group_name }}', {{ group_data|map(attribute='path')|list|tojson|safe }})">
                                🗑️ Delete All Files
                            </button>
                            {% endif %}
                        </div>
                        
                        {% if group_data.files %}
                            {% set files = group_data.files %}
                        {% else %}
                            {% set files = group_data %}
                        {% endif %}
                        
                        {% for file in files %}
                        <div class="file-item" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">
                                    {{ file.path }}
                                    {% if group_data.base_file and file.path == group_data.base_file.path %}
                                    <span style="color: #28a745; font-weight: bold; margin-left: 10px;">[BASE FILE]</span>
                                    {% elif group_data.has_base %}
                                    <span style="color: #ffc107; margin-left: 10px;">[VARIANT]</span>
                                    {% endif %}
                                </div>
                                <div class="file-meta">
                                    Size: {{ "%.2f"|format(file.size / 1024) }} KB | 
                                    Modified: {{ file.modified }}
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
                    <p>No files with similar prefixes found.</p>
                {% endif %}
            </div>
        </div>
        
        <!-- Space Files Section -->
        <div class="section">
            <h2>Files with Spaces</h2>
            <div id="space-content">
                {% if results.space_files %}
                    <div class="file-group">
                        <div class="file-group-header">Files with Spaces <span class="group-info">({{ results.space_files|length }} files)</span></div>
                        {% for file in results.space_files %}
                        <div class="file-item" data-filepath="{{ file.path }}">
                            <div class="file-info">
                                <div class="file-path">{{ file.path }}</div>
                                <div class="file-meta">
                                    Size: {{ "%.2f"|format(file.size / 1024) }} KB | 
                                    Modified: {{ file.modified }}
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
                    <p>No files with spaces found.</p>
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
                        scanBtn.textContent = 'Scanning...';
                        scanBtn.disabled = true;
                        
                        // Update progress
                        const progress = data.progress;
                        const percentage = progress.total > 0 ? (progress.processed / progress.total * 100) : 0;
                        
                        statusDiv.innerHTML = `
                            <div class="scanning">
                                <div>🔄 <span id="scan-stage">${progress.stage}</span></div>
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
                        scanBtn.textContent = 'Manual Scan';
                        scanBtn.disabled = false;
                        
                        statusDiv.innerHTML = `<div class="complete">✅ Last scan: ${data.last_scan || 'Never'}</div>`;
                        
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
            if (confirm(`Are you sure you want to delete this file?\\n${filepath}`)) {
                // Find the file item element to remove from UI
                const fileItems = document.querySelectorAll('.file-item');
                let targetFileItem = null;
                
                fileItems.forEach(item => {
                    const pathElement = item.querySelector('.file-path');
                    if (pathElement && pathElement.textContent.trim() === filepath) {
                        targetFileItem = item;
                    }
                });
                
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
                        // Show success toast instead of alert
                        const filename = filepath.split('/').pop();
                        showToast(`✓ Deleted: ${filename}`, 'success');
                        
                        if (data.remove_from_ui && targetFileItem) {
                            // Remove the file item from the DOM
                            const fileGroup = targetFileItem.closest('.file-group');
                            targetFileItem.remove();
                            
                            // Check if this was the last file in the group OR only one file remains (no longer a conflict)
                            const remainingFiles = fileGroup.querySelectorAll('.file-item');
                            if (remainingFiles.length <= 1) {
                                // Remove the entire group if no files left or only one file remains
                                fileGroup.remove();
                                
                                // If it was the last group in a section, show the "no files" message
                                updateEmptyStateMessages();
                            } else {
                                // Update the group header count
                                const groupHeader = fileGroup.querySelector('.file-group-header');
                                const groupInfo = groupHeader.querySelector('.group-info');
                                if (groupInfo) {
                                    groupInfo.textContent = `(${remainingFiles.length} files)`;
                                }
                            }
                            
                            // Update statistics immediately
                            updateStatsAfterDelete();
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
            // Check each section and add "no files found" message if empty
            const sections = [
                { id: 'duplicates-content', message: 'No duplicate files found.' },
                { id: 'underscore-content', message: 'No files with underscores found.' },
                { id: 'space-content', message: 'No files with spaces found.' }
            ];
            
            sections.forEach(section => {
                const container = document.getElementById(section.id);
                const groups = container.querySelectorAll('.file-group');
                
                if (groups.length === 0) {
                    // Remove any existing empty message first
                    const existingMessage = container.querySelector('p');
                    if (existingMessage) existingMessage.remove();
                    
                    // Add the empty state message
                    const emptyMessage = document.createElement('p');
                    emptyMessage.textContent = section.message;
                    container.appendChild(emptyMessage);
                }
            });
        }
        
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = `toast ${type}`;
            
            // Show the toast
            setTimeout(() => {
                toast.classList.add('show');
            }, 100);
            
            // Hide the toast after 3 seconds
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }
        
        function updateStatsAfterDelete() {
            // Recount the remaining items in the DOM
            const duplicateGroups = document.querySelectorAll('#duplicates-content .file-group').length;
            const underscoreGroups = document.querySelectorAll('#underscore-content .file-group').length;
            const spaceFiles = document.querySelectorAll('#space-content .file-item').length;
            
            // Update the stat displays
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
                        // Show a brief message and start monitoring for scan completion
                        showAutoRefreshMessage();
                        // Force immediate status update to reflect the rescan
                        setTimeout(updateStatus, 500);
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
            console.log('deleteVariants called with:', groupName, variantPaths);
            
            if (!variantPaths || variantPaths.length === 0) {
                showToast('No files to delete', 'error');
                return;
            }
            
            const variantCount = variantPaths.length;
            const fileNames = variantPaths.map(p => p.split('/').pop()).join('\n');
            
            const confirmMessage = `Are you sure you want to delete ${variantCount} files for "${groupName}"?\n\nFiles to delete:\n${fileNames}`;
            
            if (confirm(confirmMessage)) {
                console.log('Sending delete request to /api/delete_variants');
                
                fetch('/api/delete_variants', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({filepaths: variantPaths})
                })
                .then(response => {
                    console.log('Response received:', response.status);
                    return response.json();
                })
                .then(data => {
                    console.log('Response data:', data);
                    
                    if (data.success) {
                        showToast(`✓ Deleted ${data.deleted_files.length} files`, 'success');
                        
                        if (data.remove_from_ui) {
                            // Remove deleted files from the DOM
                            data.deleted_files.forEach(filepath => {
                                const fileItem = document.querySelector(`.file-item[data-filepath="${filepath}"]`);
                                if (fileItem) {
                                    console.log('Removing file item:', filepath);
                                    fileItem.remove();
                                } else {
                                    console.log('File item not found in DOM:', filepath);
                                }
                            });
                            
                            // Update the group
                            const groupHeaders = document.querySelectorAll('.file-group-header');
                            groupHeaders.forEach(header => {
                                if (header.textContent.includes(groupName)) {
                                    const fileGroup = header.closest('.file-group');
                                    const remainingFiles = fileGroup.querySelectorAll('.file-item');
                                    
                                    console.log(`Group ${groupName} has ${remainingFiles.length} remaining files`);
                                    
                                    if (remainingFiles.length <= 1) {
                                        console.log('Removing entire group');
                                        fileGroup.remove();
                                        updateEmptyStateMessages();
                                    } else {
                                        // Remove the bulk delete button
                                        const deleteBtn = header.querySelector('.btn-danger');
                                        if (deleteBtn) deleteBtn.remove();
                                        
                                        // Update file count
                                        const groupInfo = header.querySelector('.group-info');
                                        if (groupInfo) {
                                            groupInfo.textContent = `(${remainingFiles.length} files)`;
                                        }
                                    }
                                }
                            });
                            
                            // Update statistics
                            updateStatsAfterDelete();
                        }
                        
                        // Show any errors that occurred
                        if (data.errors && data.errors.length > 0) {
                            console.log('Errors occurred:', data.errors);
                            showToast(`Some errors occurred: ${data.errors.length} files had issues`, 'error');
                        }
                    } else {
                        console.log('Delete failed:', data.error);
                        showToast(`Error: ${data.error}`, 'error');
                    }
                })
                .catch(error => {
                    console.error('Delete variants error:', error);
                    showToast('Error deleting files', 'error');
                });
            }
        }
            const statusDiv = document.getElementById('status');
            statusDiv.innerHTML = `
                <div class="scanning">
                    <div>🔄 Updating results after file operation...</div>
                    <div class="progress-text">The dashboard will refresh automatically when complete</div>
                </div>
            `;
        }
        
        // Track when scan completes after file operation to auto-refresh page
        let wasScanning = false;
        let originalUpdateStatus = updateStatus;
        updateStatus = function() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    // Check if scan just completed
                    if (wasScanning && !data.scanning) {
                        // Scan just finished, reload the page to show updated results
                        setTimeout(() => {
                            location.reload();
                        }, 1000); // Small delay to ensure scan results are saved
                    }
                    wasScanning = data.scanning;
                    
                    // Continue with normal status update
                    const statusDiv = document.getElementById('status');
                    const scanBtn = document.getElementById('scanBtn');
                    
                    // Update stats
                    document.getElementById('duplicate-count').textContent = data.duplicate_count;
                    document.getElementById('underscore-count').textContent = data.underscore_count;
                    document.getElementById('space-count').textContent = data.space_count;
                    
                    if (data.scanning) {
                        // Update scan button
                        scanBtn.textContent = 'Scanning...';
                        scanBtn.disabled = true;
                        
                        // Update progress
                        const progress = data.progress;
                        const percentage = progress.total > 0 ? (progress.processed / progress.total * 100) : 0;
                        
                        statusDiv.innerHTML = `
                            <div class="scanning">
                                <div>🔄 <span id="scan-stage">${progress.stage}</span></div>
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
                        scanBtn.textContent = 'Manual Scan';
                        scanBtn.disabled = false;
                        
                        statusDiv.innerHTML = `<div class="complete">✅ Last scan: ${data.last_scan || 'Never'}</div>`;
                        
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
        };
    </script>
</body>
</html>
'''

# Create templates directory and save template
os.makedirs('templates', exist_ok=True)
with open('templates/index.html', 'w') as f:
    f.write(template_html)

if __name__ == '__main__':
    # Create scan directory if it doesn't exist
    os.makedirs(SCAN_DIRECTORY, exist_ok=True)
    
    print(f"Starting Duplicate File Scanner")
    print(f"Scan Directory: {SCAN_DIRECTORY}")
    print(f"Scan Interval: {SCAN_INTERVAL} seconds")
    print(f"Dashboard: http://localhost:5590")
    
    app.run(debug=True, host='0.0.0.0', port=5590)
