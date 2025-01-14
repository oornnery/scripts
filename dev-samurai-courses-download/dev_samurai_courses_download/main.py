import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List
from concurrent.futures import ThreadPoolExecutor

from parsel import Selector
from httpx import (
    Client,
    HTTPError,
    ReadTimeout,
)
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)
import rich_click as click


# Metadata
__author__ = "Fabio Souza"
__version__ = "0.0.1"


# Setup logging
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(
        console=console,
        omit_repeated_times=False,
    )]
)

@dataclass
class Config:
    URL: str = 'https://class.devsamurai.com.br/'
    PATH_TO_DOWNLOAD: str = 'downloads'
    MAX_WORKER: int = 5
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 5
    CHUNK_SIZE: int = 8192
    TIMEOUT: int = 30

@dataclass(slots=True, frozen=True)
class Course:
    seq: int
    title: str
    link: str


# Functions
def fetch_courses(cfg: Config = None, client: Client = None) -> List[Course]:
    _cfg = cfg or Config()
    _client = client or Client()
    try:
        response = _client.get(_cfg.URL)
        response.raise_for_status()
        selector = Selector(response.text)
        courses = selector.css('body > div > ul > li')
        return [
            Course(
                seq,
                course.css('a::text').get(),
                course.css('a::attr(href)').get()
                )
            for seq, course in enumerate(courses, 1)
        ]
    except Exception as e:
        logging.error(f"Failed to fetch courses: {e}")
        return []

def get_headers(file_path: Path) -> dict:
    if file_path.exists():
        return {'Range': f'bytes={file_path.stat().st_size}-'}
    return {}

def download_course(
            course: Course,
            file_path: Path,
            cfg: Config = None,
            client: Client = Client(),
            progress_bar: Progress = None
        ) -> None:
    _cfg = cfg or Config()
    # Download course
    headers = get_headers(file_path)
    if headers:
        logging.info(f"Resuming download: {file_path}")
    for attempt in range(_cfg.MAX_RETRIES):
        try:
            with open(
                        file_path,
                        'ab'
                    ) as file, client.stream(
                        'GET',
                        course.link,
                        headers=headers,
                        timeout=_cfg.TIMEOUT
                    ) as response:
                # Check response
                response.raise_for_status()
                # Progress bar
                if progress_bar:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = file_path.stat().st_size if file_path.exists() else 0
                    task = progress_bar.add_task(
                        f'Downloading: {course.seq} - {course.title}', total=total_size + downloaded
                        )
                    progress_bar.update(task, total=total_size + downloaded)
                    progress_bar.update(task, completed=downloaded)
                # Write file
                for chunk in response.iter_bytes(Config.CHUNK_SIZE):
                    file.write(chunk)
                    if progress_bar:
                        downloaded += len(chunk)
                        progress_bar.update(task, completed=downloaded)
        except (HTTPError, ReadTimeout):
            if attempt == Config.MAX_RETRIES - 1:
                raise
            logging.warning(
                f"Download failed. Retrying in {_cfg.RETRY_DELAY} seconds... \
                    (Attempt {attempt + 1}/{_cfg.MAX_RETRIES})"
                )
            time.sleep(_cfg.RETRY_DELAY)
        else:
            logging.info(f"Downloaded: {file_path}")
            break

# MAX_RETRIES = 3
# RETRY_DELAY = 5
# CHUNK_SIZE = 8192
# TIMEOUT = 30
# MAX_WORKER = 5
# PATH_TO_DOWNLOAD = 'courses'

@click.command(help="Download all courses from Dev Samurai")
@click.option(
    "--download-path", "-d",
    default=Config.PATH_TO_DOWNLOAD,
    show_default=True,
    help="Path to download courses"
)
@click.option(
    '--threads', '-t',
    default=Config.MAX_WORKER,
    show_default=True,
    help="Number of threads to use"
)
@click.option(
    '--timeout', '-T',
    default=Config.TIMEOUT,
    show_default=True,
    help="Timeout for requests"
)
@click.option(
    '--chunk-size', '-c',
    default=Config.CHUNK_SIZE,
    show_default=True,
    help="Chunk size for download"
)
@click.option(
    '--max-retries', '-r',
    default=Config.MAX_RETRIES,
    show_default=True,
    help="Number of retries"
)
@click.option(
    '--retry-delay', '-R',
    default=Config.RETRY_DELAY,
    show_default=True,
    help="Delay between retries"
)
@click.version_option(version=__version__)
def main(
        download_path: str,
        threads: int,
        timeout: int,
        chunk_size: int,
        max_retries: int,
        retry_delay: int
    ) -> None:

    cfg = Config(
        PATH_TO_DOWNLOAD=download_path,
        MAX_WORKER=threads,
        TIMEOUT=timeout,
        CHUNK_SIZE=chunk_size,
        MAX_RETRIES=max_retries,
        RETRY_DELAY=retry_delay
    )
    console.rule("Download Dev Samurai Courses")
    courses = fetch_courses()
    
    if not courses:
        logging.error("No courses found.")
        return
    with Progress(
            "[progress.percentage]{task.description}{task.percentage:>3.0f}%",
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            expand=True,
            refresh_per_second=3
        ) as progress:
        task = progress.add_task("Downloading all...", total=len(courses))
        
        def download_thread(course: Course):
            file_path = Path(download_path) / f"{course.seq:02d} - {course.title}.zip"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            logging.info(f"Downloading: {course.seq} - {course.title}")
            logging.info(f'Path: {file_path}')
            download_course(course, file_path, progress_bar=progress)
            progress.update(task, advance=1)
        
        with ThreadPoolExecutor(cfg.MAX_WORKER) as executor:
            executor.map(download_thread, courses)
    logging.info("All courses downloaded.")

main()