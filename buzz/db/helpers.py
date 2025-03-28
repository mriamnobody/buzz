import os
from datetime import datetime
from sqlite3 import Connection

from buzz.assets import get_path
from buzz.cache import TasksCache
from buzz.db.migrator import dumb_migrate_db


def copy_transcriptions_from_json_to_sqlite(conn: Connection):
    cache = TasksCache()
    if os.path.exists(cache.tasks_list_file_path):
        tasks = cache.load()
        cursor = conn.cursor()
        for task in tasks:
            # Determine title for older data being migrated
            if task.url:
                title = task.url  # Cannot fetch title retrospectively easily
            else:
                title = os.path.basename(task.file_path) if task.file_path else "Unknown"

            cursor.execute(
                """
                INSERT INTO transcription (id, title, error_message, export_formats, file, output_folder, progress, language, model_type, source, status, task, time_ended, time_queued, time_started, url, whisper_model_size, hugging_face_model_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, ?), ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING; -- Prevent duplicates if migration runs multiple times
                """,
                (
                    str(task.uid),
                    title,  # Added title
                    task.error,
                    ", ".join(
                        [
                            format.value
                            for format in task.file_transcription_options.output_formats
                        ]
                    ),
                    task.file_path,
                    task.output_directory,
                    task.fraction_completed,
                    task.transcription_options.language,
                    task.transcription_options.model.model_type.value,
                    task.source.value,
                    task.status.value,
                    task.transcription_options.task.value,
                    task.completed_at,
                    task.queued_at, datetime.now().isoformat(),  # time_queued, use now as fallback
                    task.started_at,
                    task.url,
                    task.transcription_options.model.whisper_model_size.value
                    if task.transcription_options.model.whisper_model_size
                    else None,
                    task.transcription_options.model.hugging_face_model_id
                    if task.transcription_options.model.hugging_face_model_id
                    else None,
                ),
            )

            # Check if the insertion was successful before trying to insert segments
            if cursor.rowcount > 0:
                transcription_id = str(task.uid)  # Use the original task ID
                for segment in task.segments:
                    cursor.execute(
                        """
                        INSERT INTO transcription_segment (end_time, start_time, text, translation, transcription_id)
                        VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            segment.end,
                            segment.start,
                            segment.text,
                            segment.translation,
                            transcription_id,
                        ),
                    )
        # Remove the old cache file after successful migration
        os.remove(cache.tasks_list_file_path)
        for task in tasks:  # remove individual task files too
            task_file_path = cache.get_task_path(task_id=task.id)  # assuming task.id was the old key
            if os.path.exists(task_file_path):
                os.remove(task_file_path)

        conn.commit()


def run_sqlite_migrations(db: Connection):
    schema_path = get_path("schema.sql")

    with open(schema_path) as schema_file:
        schema = schema_file.read()
        dumb_migrate_db(db=db, schema=schema)


def mark_in_progress_and_queued_transcriptions_as_canceled(conn: Connection):
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE transcription
        SET status = 'canceled', time_ended = ?
        WHERE status = 'in_progress' OR status = 'queued';
        """,
        (datetime.now().isoformat(),),
    )
    conn.commit()
