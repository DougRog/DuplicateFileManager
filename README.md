# Duplicate File Manager

A modern, intelligent file duplicate detection and management system with automated email notifications and smart resolution suggestions.

## Features

### 🎨 Modern UI
- Clean, professional interface with modern design
- Responsive layout that works on all devices
- Real-time progress tracking with visual feedback
- Toast notifications for user actions
- Color-coded file recommendations

### 🧠 Smart Duplicate Detection
- **Automatic Resolution Suggestions**: The system automatically suggests which file to keep based on:
  1. **Newest file first**: Prefers files with the most recent modification time
  2. **MOV preference**: If timestamps are equal, prefers `.MOV` files
- **Related File Filtering**: Doesn't flag TS/XML or MXF/XML pairs as duplicates (these are related sidecar files)
- Visual indicators showing recommended files with green highlighting

### 📧 Email Notifications
- Automatic email alerts when **new** duplicates are detected
- Sends notifications to: `toc@lillybroadcasting.com`
- Tracks previously seen duplicates to avoid spam
- Only notifies about newly discovered duplicate groups
- Detailed email reports with file paths, sizes, and timestamps

### ⚙️ Automated Scanning
- Scans every **15 minutes** automatically
- Manual scan option available
- Progress tracking with file count and current file
- Non-blocking background scans

### 🗑️ File Management
- **Fixed deletion**: Files are now properly deleted with verification
- Bulk delete options for file variants
- File renaming capability
- Security checks to prevent accidental deletions outside scan directory

### 📊 Three Detection Categories
1. **Duplicate Files**: Same filename, different extensions or paths (with smart suggestions)
2. **Underscore Groups**: Files with similar prefixes (variants with underscores)
3. **Files with Spaces**: Files containing spaces in their names

## Configuration

### Environment Variables

```bash
# Directory to scan for duplicates
SCAN_DIRECTORY=/mnt/mc_media/

# Email server settings
SMTP_SERVER=localhost
SMTP_PORT=25
```

### Email Configuration

The system sends notifications to `toc@lillybroadcasting.com` when new duplicates are found.

To customize the email recipient, edit the `EMAIL_RECIPIENT` variable in `DuplicateFileManager.py`:

```python
EMAIL_RECIPIENT = 'your-email@domain.com'
```

### Scan Interval

The system scans every 15 minutes by default. To change this, modify the `SCAN_INTERVAL` variable:

```python
SCAN_INTERVAL = 900  # 15 minutes in seconds
```

## Installation

1. Install dependencies:
```bash
pip install flask
```

2. Run the application:
```bash
python DuplicateFileManager.py
```

3. Access the dashboard:
```
http://localhost:5590
```

## How It Works

### Smart Duplicate Resolution

When duplicates are detected, the system automatically suggests which file to keep:

1. **Compare timestamps**: The file with the newest modification time is preferred
2. **Check for MOV files**: If multiple files have the same timestamp, `.MOV` files are preferred
3. **Visual indicators**: Recommended files are highlighted in green with a ✓ RECOMMENDED tag

### Related File Filtering

The system intelligently recognizes related file pairs:
- `.TS` and `.XML` files with the same name (transport stream and metadata)
- `.MXF` and `.XML` files with the same name (media exchange format and metadata)

These pairs are **not** flagged as duplicates since they work together.

### Email Notification System

1. **First scan**: Discovers all existing duplicates but doesn't send notifications
2. **Tracking**: Records all seen duplicate groups in `seen_duplicates.json`
3. **Subsequent scans**: Only sends email for **new** duplicate groups
4. **Persistence**: Seen duplicates are saved to disk to survive restarts

### File Deletion

The improved deletion system:
1. Performs security checks to ensure files are within the scan directory
2. Verifies the file exists before attempting deletion
3. Actually deletes the file using `Path.unlink()`
4. Confirms deletion by checking if file still exists
5. Updates the UI immediately without requiring a rescan

## File Structure

```
DuplicateFileManager/
├── DuplicateFileManager.py    # Main application
├── templates/
│   └── index.html             # Web interface template
├── seen_duplicates.json       # Tracked duplicate groups (auto-generated)
└── README.md                  # This file
```

## Security Features

- Path validation to prevent directory traversal attacks
- Scan directory boundary enforcement
- Permission error handling
- Filename sanitization for renames

## UI Features

### Dashboard
- Real-time statistics cards showing counts for each category
- Color-coded status indicators
- Progress bars during scanning
- Auto-refresh every 2 seconds during scans, 10 seconds otherwise

### File Groups
- Expandable/collapsible groups
- File metadata (size, modification time, extension)
- Individual file actions (rename, delete)
- Bulk actions for variant files
- Visual badges for smart suggestions

### Toast Notifications
- Success messages for completed actions
- Error messages with details
- Auto-dismiss after 3 seconds
- Non-intrusive positioning

## Troubleshooting

### Emails not sending
- Check SMTP server settings in environment variables
- Verify SMTP server is accessible from the host
- Check application logs for email errors

### Files not deleting
- Verify file permissions
- Check that files are within the scan directory
- Look for permission errors in the console

### Scans taking too long
- The system processes files in chunks to remain responsive
- Large directories may take several minutes
- Progress is shown in real-time

## Technical Details

- **Framework**: Flask (Python web framework)
- **File Processing**: Chunked processing with 50-file batches
- **Hashing**: MD5 hash of first 1MB for quick comparison
- **Threading**: Background scanner runs in separate thread
- **State Management**: JSON file for persistent duplicate tracking
