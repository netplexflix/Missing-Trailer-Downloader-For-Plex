from datetime import datetime, timedelta
import os
import logging
import logging.handlers

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore

import MTDfP

# Configure the logging to log to the console
print("Configuring logging...")
_log_format = "%(asctime)s - [%(levelname)s|%(name)s|%(lineno)s]: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
)

# Create a logger for the tasks
logger = logging.getLogger("tasks")

# Add a rotating file handler to the logger
file_handler = logging.handlers.RotatingFileHandler(
    "/config/logs/mtdp.log", maxBytes=1 * 1024 * 1024, backupCount=10
)
file_handler.setFormatter(logging.Formatter(_log_format))
logger.addHandler(file_handler)

# Get the timezone from the environment variable
timezone = os.getenv("TZ", "UTC")

# Initialize a MemeoryJobStore for the scheduler
jobstores = {"default": MemoryJobStore()}

# Create a scheduler instance and start it in FastAPI's lifespan context
scheduler = BackgroundScheduler(
    jobstores=jobstores, timezone=timezone, logger=logger
)


def download_trailers_job():
    """
    Schedules a background job to download trailers by syncing Plex data. \n
        - Runs once an hour, first run in 10 seconds. \n
    Returns:
        None
    """
    scheduler.add_job(
        func=MTDfP.main,
        trigger="interval",
        minutes=60,
        id="hourly_download_job",
        name="Download Trailers",
        next_run_time=datetime.now() + timedelta(seconds=10),
        max_instances=1,
    )
    logger.info("Trailers download job scheduled!")
    return
