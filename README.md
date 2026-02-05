# ChromaDB Pruning Tool for Open WebUI

A standalone Python script that cleans up orphaned ChromaDB data from an Open WebUI installation, reclaiming significant disk space without affecting active data.

## The Problem

When Open WebUI deletes files or knowledge bases, ChromaDB's `delete_collection()` method doesn't properly cascade deletions. This leaves behind:

- **Orphaned embeddings** - Vector data pointing to deleted collections
- **Orphaned metadata** - Metadata records for deleted embeddings
- **Orphaned directories** - Physical segment directories on disk

Over time, this orphaned data accumulates and prevents `VACUUM` from reclaiming space, causing the database to grow indefinitely.

## What This Tool Does

1. **Analyzes** your ChromaDB database to identify orphaned records
2. **Deletes** orphaned embeddings, metadata, and max_seq_id records
3. **Rebuilds** the FTS (full-text search) index
4. **Removes** orphaned physical directories
5. **Vacuums** the database to reclaim disk space

## Requirements

- Python 3.7+
- No external dependencies (uses only Python standard library)

## Usage

### 1. Stop Open WebUI

**Important:** Stop your Open WebUI instance before running this tool to avoid database corruption.

```bash
# If using Docker
docker stop open-webui

# If using systemd
sudo systemctl stop open-webui
```

### 2. Copy Your Data Directory (Recommended)

Create a backup or work on a copy:

```bash
# Example: Copy the data directory to a working location
cp -r /path/to/open-webui/backend/data /path/to/backup/data
```

### 3. Run the Tool

```bash
# Preview what would be deleted (safe, no changes made)
python prune_chromadb.py --data-dir /path/to/data

# Execute cleanup with disk space reclamation
python prune_chromadb.py --data-dir /path/to/data --execute --vacuum

# Skip confirmation prompt
python prune_chromadb.py --data-dir /path/to/data --execute --vacuum --yes
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `--data-dir PATH` | Open WebUI data directory (default: `./backend/data`) |
| `--execute` | Execute cleanup (default is dry-run preview) |
| `--vacuum` | Run VACUUM after cleanup to reclaim disk space |
| `--yes`, `-y` | Skip confirmation prompt |

## Example Output

### Dry-Run (Preview)

```
ChromaDB Pruning Tool for Open WebUI
========================================

Data directory: D:\dev\openwebuifix\backend\data
chroma.sqlite3: 448.58 MB

Analyzing ChromaDB...
  Total embeddings: 64,365
  Total segments: 51,794
  Total collections: 25,897
  Total metadata records: 581,490

Orphaned data found:
  Orphaned embeddings: 19,805 (30.8%)
  Orphaned metadata records: 195,358
  Orphaned max_seq_id records: 11
  Orphaned directories: 9 (74.19 MB)

DRY RUN - No changes made
To execute cleanup, run with --execute flag
```

### After Execution

```
Executing cleanup...
  Deleted 195,358 orphaned embedding_metadata records
  Deleted 19,805 orphaned embeddings
  Deleted 11 orphaned max_seq_id records
  Rebuilt FTS index
  Deleted 9 orphaned directories (74.19 MB)
  Running VACUUM...

Cleanup complete!
  chroma.sqlite3: 448.58 MB -> 298.00 MB (saved 150.58 MB)
```

## Restoring to Your Original Instance

After running the cleanup on a copy of your data, you need to copy the cleaned files back to your original Open WebUI installation.

### Files to Copy Back

Only the `vector_db` directory needs to be replaced:

```
backend/data/vector_db/
├── chroma.sqlite3      # The cleaned database (REQUIRED)
└── [segment-uuid]/     # Remaining valid segment directories
```

### Step-by-Step Restore

1. **Ensure Open WebUI is stopped**

2. **Backup your original vector_db** (just in case)
   ```bash
   # Linux/Mac
   mv /path/to/open-webui/backend/data/vector_db /path/to/open-webui/backend/data/vector_db.bak

   # Windows
   move C:\path\to\open-webui\backend\data\vector_db C:\path\to\open-webui\backend\data\vector_db.bak
   ```

3. **Copy the cleaned vector_db directory**
   ```bash
   # Linux/Mac
   cp -r /path/to/cleaned/backend/data/vector_db /path/to/open-webui/backend/data/

   # Windows
   xcopy /E /I D:\dev\openwebuifix\backend\data\vector_db C:\path\to\open-webui\backend\data\vector_db
   ```

4. **Start Open WebUI**
   ```bash
   # Docker
   docker start open-webui

   # systemd
   sudo systemctl start open-webui
   ```

5. **Verify functionality**
   - Open the web UI
   - Test a RAG query against an existing knowledge base
   - Check logs for any errors

6. **Remove the backup** (once verified)
   ```bash
   rm -rf /path/to/open-webui/backend/data/vector_db.bak
   ```

### What NOT to Copy

- `webui.db` - The main SQLite database (user data, settings, file records) - this was not modified
- `uploads/` - Uploaded files - not modified
- `cache/` - Cache files - not modified

## Safety Features

- **Dry-run by default** - Must explicitly use `--execute` to make changes
- **Confirmation prompt** - Asks for confirmation before destructive operations
- **Atomic transactions** - All database deletions in a single transaction (all-or-nothing)
- **Conservative deletion** - Only deletes data provably orphaned (segment_id not in segments table)

## Technical Details

### What Gets Deleted

The tool identifies orphaned data by checking for embeddings whose `segment_id` doesn't exist in the `segments` table:

```sql
-- Orphaned embeddings
SELECT * FROM embeddings WHERE segment_id NOT IN (SELECT id FROM segments);

-- Orphaned metadata (child records of orphaned embeddings)
SELECT * FROM embedding_metadata WHERE id IN (
    SELECT id FROM embeddings WHERE segment_id NOT IN (SELECT id FROM segments)
);

-- Orphaned max_seq_id
SELECT * FROM max_seq_id WHERE segment_id NOT IN (SELECT id FROM segments);
```

### Orphaned Directories

Physical directories in `vector_db/` are considered orphaned if:
1. The directory name is a valid UUID format
2. The UUID doesn't match any segment ID in the database

## Troubleshooting

### "No orphaned data found"

Your database is already clean. No action needed.

### "Error: ChromaDB database not found"

Verify the `--data-dir` path points to your Open WebUI data directory containing `vector_db/chroma.sqlite3`.

### RAG queries fail after cleanup

This shouldn't happen since only orphaned data is removed. If it does:
1. Restore from your backup (`vector_db.bak`)
2. Open an issue with the error details

Example of the script in progress:
<img width="808" height="840" alt="image" src="https://github.com/user-attachments/assets/1b941ee6-c266-4801-b683-90e392892985" />


## License

MIT License - Use at your own risk. Always backup your data before running cleanup operations.
