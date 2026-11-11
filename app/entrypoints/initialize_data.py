import os
import sys
from pathlib import Path
import json

sys.path.append(os.path.join(str(Path(__file__).resolve().parent.parent)))
from astrodash.shared.object_store import ObjectStore
from astrodash.shared.log import get_logger

logger = get_logger(__name__)

ASTRODASH_DATA_DIR = os.getenv('ASTRODASH_DATA_DIR', '/mnt/astrodash-data')

DATA_INIT_S3_CONF = {
    'endpoint-url': os.getenv("ASTRODASH_S3_ENDPOINT_URL", 'https://js2.jetstream-cloud.org:8001'),
    'region-name': os.getenv("ASTRODASH_S3_REGION_NAME", ''),
    'aws_access_key_id': os.getenv("ASTRODASH_S3_ACCESS_KEY_ID", ''),
    'aws_secret_access_key': os.getenv("ASTRODASH_S3_SECRET_ACCESS_KEY", ''),
    'bucket': os.getenv("ASTRODASH_S3_BUCKET", 'astrodash'),
}


def generate_file_manifest():
    '''Collect metadata for the latest versions of the objects in a JSON file'''
    s3init = ObjectStore(conf=DATA_INIT_S3_CONF)
    root_path = 'init/data/'
    objs = s3init.get_directory_objects(root_path)
    file_info = []
    for obj in objs:
        info = {
            'path': obj.object_name.replace(root_path, ''),
            'version_id': obj.version_id,
            'etag': obj.etag,
            'size': obj.size,
        }
        if obj.is_latest.lower() == "true":
            file_info.append(info)
        else:
            logger.debug(info)
    manifest_name = 'astrodash-data.json'
    with open(os.path.join(Path(__file__).resolve().parent, manifest_name), 'w') as fh:
        json.dump(file_info, fh, indent=2)


def verify_data_integrity(download=False):
    '''Verify integrity of Astrodash data files against manifest'''

    manifest_name = 'astrodash-data.json'
    manifest_path = os.path.join(Path(__file__).resolve().parent, manifest_name)

    if not os.path.isfile(manifest_path):
        logger.warning(f'Data manifest not found: {manifest_path}')
        return

    with open(manifest_path, 'r') as fh:
        data_objects = json.load(fh)

    s3_init = None
    if download:
        if not DATA_INIT_S3_CONF['aws_access_key_id'] or not DATA_INIT_S3_CONF['aws_secret_access_key']:
            logger.info('S3 credentials not configured. Using anonymous access for read operations.')
        s3_init = ObjectStore(conf=DATA_INIT_S3_CONF)
        if not s3_init.client:
            logger.error('Failed to initialize S3 client. Check ASTRODASH_S3_* environment variables.')
            return

    total_files = len(data_objects)
    downloaded_count = 0

    for idx, data_object in enumerate(data_objects):
        bucket_path = data_object['path']
        file_path = os.path.join(ASTRODASH_DATA_DIR, bucket_path)
        etag = data_object['etag']
        size = data_object['size']

        if not os.path.isfile(file_path):
            if not download:
                logger.error(f'Missing file: {bucket_path}')
                sys.exit(1)
            logger.info(f'Downloading file "{bucket_path}"... ({idx + 1}/{total_files})')
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            s3_init.download_object(
                path=os.path.join('init/data', bucket_path),
                file_path=file_path,
                version_id=data_object['version_id'])
            downloaded_count += 1

        checksum_match = s3_init.etag_compare(file_path, etag, size) if s3_init else True
        log_msg = f'Comparing "{file_path}"... {checksum_match}'
        if checksum_match:
            logger.debug(log_msg)
        elif not download:
            logger.error(log_msg)
            sys.exit(1)
        else:
            logger.warning(f'Local copy of "{bucket_path}" is out of date; re-downloading it.')
            logger.info(f'Re-downloading file "{bucket_path}"... ({idx + 1}/{total_files})')
            s3_init.download_object(
                path=os.path.join('init/data', bucket_path),
                file_path=file_path,
                version_id=data_object['version_id'])
            downloaded_count += 1
            checksum_match = s3_init.etag_compare(file_path, etag, size)
            if not checksum_match:
                logger.error(f'Downloaded file "{bucket_path}" fails integrity check.')
                sys.exit(1)
            else:
                logger.info(f'Downloaded file "{bucket_path}" passes integrity check.')

    if downloaded_count > 0:
        logger.info(f'Downloaded {downloaded_count} files.')
    logger.info('Data integrity check complete.')


if __name__ == '__main__':

    cmd = 'download'
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
    logger.debug(f'initialize_data.py command: {cmd}')
    if cmd == 'verify':
        verify_data_integrity(download=False)
    if cmd == 'download':
        verify_data_integrity(download=True)
    elif cmd == 'manifest':
        generate_file_manifest()
