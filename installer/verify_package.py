import argparse
import tempfile
from pathlib import Path
from launcher import extract_package, REQUIRED_FILES

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='efir-verify-') as temp:
    extract_package(args.archive, temp)
    for item in REQUIRED_FILES:
        if not (Path(temp) / item).stat().st_size:
            raise ValueError('Пустой файл: ' + item)
print('Пакет проверен загрузчиком.')
