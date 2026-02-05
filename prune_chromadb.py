#!/usr/bin/env python3
"""
ChromaDB Pruning Tool for Open WebUI

A standalone script that cleans up orphaned ChromaDB data from an Open WebUI
data directory. Removes orphaned embeddings, metadata, and directories that
are left behind when ChromaDB's delete_collection() doesn't cascade properly.

Usage:
    python prune_chromadb.py                           # Dry-run (preview only)
    python prune_chromadb.py --data-dir ./backend/data # Specify data directory
    python prune_chromadb.py --execute                 # Execute cleanup
    python prune_chromadb.py --execute --vacuum        # Execute and reclaim space
    python prune_chromadb.py --execute --vacuum --yes  # Skip confirmation
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def get_file_size(path: Path) -> int:
    """Get file size in bytes."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def analyze_chromadb(db_path: Path, chroma_dir: Path) -> dict:
    """
    Analyze ChromaDB to find orphaned records and directories.

    Returns a dict with counts and lists of orphaned items.
    """
    result = {
        'total_embeddings': 0,
        'total_segments': 0,
        'total_collections': 0,
        'total_metadata': 0,
        'total_max_seq_id': 0,
        'orphaned_embeddings': 0,
        'orphaned_metadata': 0,
        'orphaned_max_seq_id': 0,
        'orphaned_directories': [],
        'orphaned_dir_size': 0,
        'valid_segment_ids': set(),
    }

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Get total counts
        cursor.execute("SELECT COUNT(*) FROM embeddings")
        result['total_embeddings'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM segments")
        result['total_segments'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM collections")
        result['total_collections'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM embedding_metadata")
        result['total_metadata'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM max_seq_id")
        result['total_max_seq_id'] = cursor.fetchone()[0]

        # Get valid segment IDs
        cursor.execute("SELECT id FROM segments")
        result['valid_segment_ids'] = {row[0] for row in cursor.fetchall()}

        # Count orphaned embeddings (pointing to non-existent segments)
        cursor.execute("""
            SELECT COUNT(*) FROM embeddings
            WHERE segment_id NOT IN (SELECT id FROM segments)
        """)
        result['orphaned_embeddings'] = cursor.fetchone()[0]

        # Count orphaned metadata
        cursor.execute("""
            SELECT COUNT(*) FROM embedding_metadata
            WHERE id IN (
                SELECT id FROM embeddings
                WHERE segment_id NOT IN (SELECT id FROM segments)
            )
        """)
        result['orphaned_metadata'] = cursor.fetchone()[0]

        # Count orphaned max_seq_id
        cursor.execute("""
            SELECT COUNT(*) FROM max_seq_id
            WHERE segment_id NOT IN (SELECT id FROM segments)
        """)
        result['orphaned_max_seq_id'] = cursor.fetchone()[0]

    finally:
        conn.close()

    # Find orphaned directories
    if chroma_dir.exists():
        for entry in chroma_dir.iterdir():
            if entry.is_dir() and entry.name not in result['valid_segment_ids']:
                # Skip non-UUID directories (like system dirs)
                if len(entry.name) == 36 and entry.name.count('-') == 4:
                    dir_size = get_dir_size(entry)
                    result['orphaned_directories'].append((entry, dir_size))
                    result['orphaned_dir_size'] += dir_size

    return result


def cleanup_orphaned_data(db_path: Path, dry_run: bool = True) -> dict:
    """
    Delete orphaned embeddings, metadata, and max_seq_id records.

    Returns a dict with counts of deleted records.
    """
    result = {
        'deleted_embeddings': 0,
        'deleted_metadata': 0,
        'deleted_max_seq_id': 0,
        'fts_rebuilt': False,
    }

    if dry_run:
        return result

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Use a transaction for atomicity
        cursor.execute("BEGIN TRANSACTION")

        # Delete orphaned embedding_metadata first (references embeddings.id)
        cursor.execute("""
            DELETE FROM embedding_metadata
            WHERE id IN (
                SELECT id FROM embeddings
                WHERE segment_id NOT IN (SELECT id FROM segments)
            )
        """)
        result['deleted_metadata'] = cursor.rowcount

        # Delete orphaned embeddings
        cursor.execute("""
            DELETE FROM embeddings
            WHERE segment_id NOT IN (SELECT id FROM segments)
        """)
        result['deleted_embeddings'] = cursor.rowcount

        # Delete orphaned max_seq_id
        cursor.execute("""
            DELETE FROM max_seq_id
            WHERE segment_id NOT IN (SELECT id FROM segments)
        """)
        result['deleted_max_seq_id'] = cursor.rowcount

        # Rebuild FTS index
        try:
            cursor.execute("""
                INSERT INTO embedding_fulltext_search(embedding_fulltext_search)
                VALUES('rebuild')
            """)
            result['fts_rebuilt'] = True
        except sqlite3.OperationalError:
            # FTS table might not exist or be empty
            pass

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return result


def cleanup_directories(orphaned_dirs: list, dry_run: bool = True) -> dict:
    """
    Delete orphaned directories.

    Returns a dict with count and size of deleted directories.
    """
    result = {
        'deleted_count': 0,
        'deleted_size': 0,
    }

    if dry_run:
        return result

    for dir_path, dir_size in orphaned_dirs:
        try:
            shutil.rmtree(dir_path)
            result['deleted_count'] += 1
            result['deleted_size'] += dir_size
        except OSError as e:
            print(f"  Warning: Could not delete {dir_path}: {e}")

    return result


def vacuum_database(db_path: Path) -> tuple:
    """
    Run VACUUM on the database to reclaim space.

    Returns (size_before, size_after).
    """
    size_before = get_file_size(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()

    size_after = get_file_size(db_path)
    return size_before, size_after


def main():
    parser = argparse.ArgumentParser(
        description="ChromaDB Pruning Tool for Open WebUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python prune_chromadb.py                           # Dry-run (preview only)
  python prune_chromadb.py --data-dir ./backend/data # Specify data directory
  python prune_chromadb.py --execute                 # Execute cleanup
  python prune_chromadb.py --execute --vacuum        # Execute and reclaim space
  python prune_chromadb.py --execute --vacuum --yes  # Skip confirmation
        """
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('./backend/data'),
        help='Open WebUI data directory (default: ./backend/data)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute cleanup (default is dry-run)'
    )
    parser.add_argument(
        '--vacuum',
        action='store_true',
        help='Run VACUUM after cleanup to reclaim disk space'
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='Skip confirmation prompt'
    )

    args = parser.parse_args()

    # Resolve paths
    data_dir = args.data_dir.resolve()
    db_path = data_dir / 'vector_db' / 'chroma.sqlite3'
    chroma_dir = data_dir / 'vector_db'

    print("ChromaDB Pruning Tool for Open WebUI")
    print("=" * 40)
    print()

    # Pre-flight checks
    if not data_dir.exists():
        print(f"Error: Data directory not found: {data_dir}")
        sys.exit(1)

    if not db_path.exists():
        print(f"Error: ChromaDB database not found: {db_path}")
        sys.exit(1)

    db_size = get_file_size(db_path)
    print(f"Data directory: {data_dir}")
    print(f"chroma.sqlite3: {format_size(db_size)}")
    print()

    # Analyze
    print("Analyzing ChromaDB...")
    analysis = analyze_chromadb(db_path, chroma_dir)

    print(f"  Total embeddings: {analysis['total_embeddings']:,}")
    print(f"  Total segments: {analysis['total_segments']:,}")
    print(f"  Total collections: {analysis['total_collections']:,}")
    print(f"  Total metadata records: {analysis['total_metadata']:,}")
    print()

    # Check if there's anything to clean
    has_orphans = (
        analysis['orphaned_embeddings'] > 0 or
        analysis['orphaned_metadata'] > 0 or
        analysis['orphaned_max_seq_id'] > 0 or
        len(analysis['orphaned_directories']) > 0
    )

    if not has_orphans:
        print("No orphaned data found. Database is clean!")
        sys.exit(0)

    # Report orphaned data
    print("Orphaned data found:")
    if analysis['orphaned_embeddings'] > 0:
        pct = 100 * analysis['orphaned_embeddings'] / max(1, analysis['total_embeddings'])
        print(f"  Orphaned embeddings: {analysis['orphaned_embeddings']:,} ({pct:.1f}%)")
    if analysis['orphaned_metadata'] > 0:
        print(f"  Orphaned metadata records: {analysis['orphaned_metadata']:,}")
    if analysis['orphaned_max_seq_id'] > 0:
        print(f"  Orphaned max_seq_id records: {analysis['orphaned_max_seq_id']:,}")
    if analysis['orphaned_directories']:
        print(f"  Orphaned directories: {len(analysis['orphaned_directories'])} ({format_size(analysis['orphaned_dir_size'])})")
    print()

    # Dry-run mode
    if not args.execute:
        print("DRY RUN - No changes made")
        print("To execute cleanup, run with --execute flag")
        sys.exit(0)

    # Confirmation
    if not args.yes:
        print("WARNING: This will permanently delete orphaned data.")
        print("Make sure you have a backup of your data directory!")
        print()
        response = input("Are you sure you want to proceed? [y/N] ")
        if response.lower() not in ('y', 'yes'):
            print("Aborted.")
            sys.exit(0)
        print()

    # Execute cleanup
    print("Executing cleanup...")

    # Clean database records
    db_result = cleanup_orphaned_data(db_path, dry_run=False)
    if db_result['deleted_metadata'] > 0:
        print(f"  Deleted {db_result['deleted_metadata']:,} orphaned embedding_metadata records")
    if db_result['deleted_embeddings'] > 0:
        print(f"  Deleted {db_result['deleted_embeddings']:,} orphaned embeddings")
    if db_result['deleted_max_seq_id'] > 0:
        print(f"  Deleted {db_result['deleted_max_seq_id']:,} orphaned max_seq_id records")
    if db_result['fts_rebuilt']:
        print("  Rebuilt FTS index")

    # Clean directories
    if analysis['orphaned_directories']:
        dir_result = cleanup_directories(analysis['orphaned_directories'], dry_run=False)
        print(f"  Deleted {dir_result['deleted_count']} orphaned directories ({format_size(dir_result['deleted_size'])})")

    # Vacuum
    if args.vacuum:
        print("  Running VACUUM...")
        size_before, size_after = vacuum_database(db_path)
        saved = size_before - size_after
        print()
        print("Cleanup complete!")
        print(f"  chroma.sqlite3: {format_size(size_before)} -> {format_size(size_after)} (saved {format_size(saved)})")
    else:
        print()
        print("Cleanup complete!")
        print("Run with --vacuum to reclaim disk space.")


if __name__ == '__main__':
    main()
