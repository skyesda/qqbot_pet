"""Preview Nginx image delivery changes; --apply requires deployment approval."""
import argparse
import difflib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

BLOCK = '''
    # PetPark: public artwork only, independent of the game event loop.
    location ^~ /custom_images/ {
        root /var/www/petpark/qq_images;
        try_files $uri @petpark_image_backend;
        add_header Cache-Control "public, max-age=604800";
        add_header X-Petpark-Image-Backend "nginx";
    }
    location @petpark_image_backend {
        proxy_pass http://127.0.0.1:7799;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        add_header X-Petpark-Image-Backend "plugin";
    }
'''


def configure(apply=False, path=Path('/etc/nginx/sites-enabled/bot.flyyye.cn')):
    old = path.read_text()
    if 'location ^~ /custom_images/' in old:
        raise RuntimeError('An image location already exists; inspect before changing it.')
    anchor = '    server_name bot.flyyye.cn;'
    if anchor not in old or 'listen 443 ssl' not in old:
        raise RuntimeError('Unexpected Nginx site configuration.')
    new = old.replace(anchor, anchor + '\n' + BLOCK, 1)
    print(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                     fromfile=str(path), tofile=str(path))))
    if not apply:
        return
    backup = Path('/root') / ('bot-nginx-image-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.conf')
    shutil.copy2(path, backup)
    public = Path('/var/www/petpark/qq_images/custom_images')
    public.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(new)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        shutil.copy2(backup, path)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise
    print('Applied; backup:', backup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    configure(args.apply)
