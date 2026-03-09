import os
import sys
from pathlib import Path
import json
import logging

logging.basicConfig(format='%(asctime)s [%(name)-12s] %(levelname)-8s %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv('LOG_LEVEL', logging.INFO))

ASTRODASH_DATA_DIR = os.getenv('ASTRODASH_DATA_DIR', '/mnt/astrodash-data')


def verify_data_integrity(download=False):
    '''Verify integrity of Astrodash data files against manifest'''

    manifest_name = 'astrodash-data.json'
    manifest_path = os.path.join(Path(__file__).resolve().parent, manifest_name)

    if not os.path.isfile(manifest_path):
        logger.warning(f'Data manifest not found: {manifest_path}')
        return

    with open(manifest_path, 'r') as fh:
        data_objects = json.load(fh)

    for data_object in data_objects:
        bucket_path = data_object['path']
        file_path = os.path.join(ASTRODASH_DATA_DIR, bucket_path)
        if not os.path.isfile(file_path):
            logger.error(f'Missing file: {bucket_path}')
            if not download:
                sys.exit(1)
            logger.info(f'File "{bucket_path}" not found locally. Manual download required.')


if __name__ == '__main__':

    cmd = 'download'
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
    logger.debug(f'initialize_data.py command: {cmd}')
    if cmd == 'verify':
        verify_data_integrity(download=False)
    if cmd == 'download':
        verify_data_integrity(download=True)
