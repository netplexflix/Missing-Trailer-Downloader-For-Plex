import time
import logging
from Modules import schedules


def run_schedules():
    """
    Schedules all jobs for the application and starts the scheduler. \n
    Returns:
        None
    """
    # Schedule the jobs
    schedules.download_trailers_job()
    schedules.scheduler.start()
    logging.info("Scheduler started!")
    # Run this indefinitely or until kill signal is received
    while True:
        time.sleep(10)
    return


if __name__ == "__main__":
    run_schedules()
