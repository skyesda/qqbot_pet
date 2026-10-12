"""Publish immutable game images for Nginx without copying image bytes."""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)


def publish_directory(config):
    configured = config.get('image_publish_dir')
    if configured is None:
        configured = '/var/www/petpark/qq_images/custom_images' if os.name == 'posix' else ''
    return Path(configured) if configured else None


def publish_image(source, config):
    """Hardlink atomically; keep the existing HTTP backend available on failure."""
    source = Path(source)
    directory = publish_directory(config)
    if directory is None or not source.is_file():
        return False
    temp = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / source.name
        if target.is_file() and os.path.samefile(source, target):
            return True
        temp = directory / ('.' + source.name + '.' + uuid.uuid4().hex)
        try:
            os.link(source, temp)
        except OSError:
            shutil.copyfile(source, temp)
        # Public artwork only, never expose the source data directory.
        temp.chmod(0o644)
        os.replace(temp, target)
        return True
    except OSError:
        log.warning('[petpark] 图片静态发布失败，回退网站下载: %s', source.name, exc_info=True)
        return False
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def unpublish_image(filename, config):
    directory = publish_directory(config)
    if directory is not None:
        (directory / Path(filename).name).unlink(missing_ok=True)


def image_url(filename, config):
    base = str(config.get('image_public_base_url') or 'https://bot.flyyye.cn').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('image_public_base_url must be an HTTP(S) base URL')
    return base + '/custom_images/' + quote(filename, safe='')
