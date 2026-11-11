# Build only on Windows: python -m PyInstaller packaging/coc-auto.spec --clean --noconfirm
from pathlib import Path
import json
import shutil

root = Path(SPECPATH).parent
stage = root / 'build/resources'
if stage.exists(): shutil.rmtree(stage)
shutil.copytree(root / 'src/coc_auto/resources', stage)
# The legacy calibration only serves a hand-picked --config in a source checkout.
shutil.rmtree(stage / 'calibration-legacy')
(stage / 'config.legacy.json').unlink()
# Per-machine settings are never release inputs. The first run detects them.
config_path = stage / 'config.json'
config = json.loads(config_path.read_text(encoding='utf-8'))
config.update(adb='adb.exe', ldconsole='ldconsole.exe', tesseract='tesseract.exe',
              serial='emulator-5554', ld_index=0, touch_device='/dev/input/event0', language='en')
if isinstance(config.get('zoom_out'), dict): config['zoom_out']['device'] = '/dev/input/event0'
# The relay token is public by design (docs/user/dashboard.md), so every build keeps the online relay.
config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
for candidate in stage.rglob('*'):
    if candidate.is_file() and ('.local.' in candidate.name or candidate.suffix in ('.bak','.tmp')):
        raise RuntimeError(f'Personal or temporary configuration in resources: {candidate.name}')

datas = [(str(stage), 'coc_auto/resources'), (str(root/'web'), 'web'),
         (str(root/'packaging/THIRD-PARTY-NOTICES.md'), '.')]
# Packaging, testing and debugging tools, and Tk (Windows' own folder picker replaces it); the app never runs them.
excludes = ['tkinter', '_tkinter', 'setuptools', '_distutils_hack', 'packaging', 'unittest', 'doctest',
            'pydoc', 'pdb', 'numpy.f2py', 'numpy.testing']
a = Analysis([str(root/'packaging/entrypoint.py')], pathex=[str(root/'src')],
             binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=excludes, noarchive=False)
# Analysis can discover package-data from the editable install; use only staged resources.
a.datas = [entry for entry in a.datas if not entry[0].startswith('coc_auto/resources/')]
a.datas += [(f'coc_auto/resources/{file.relative_to(stage).as_posix()}', str(file), 'DATA')
            for file in stage.rglob('*') if file.is_file()]
# OCR is added after Analysis so PyInstaller does not copy Tesseract's DLLs into the app root;
# tesseract.exe loads them from ocr/bin (see paths.ocr_process_options).
ocr = root / 'build/ocr'
a.datas += [(f'ocr/{file.relative_to(ocr).as_posix()}', str(file), 'DATA')
            for file in ocr.rglob('*') if file.is_file()]
# Never loaded: OpenCV's FFmpeg video backend (~28 MB; the bot never opens video), and app-local Universal CRT
# copies from the build machine's Python (Windows 10 and later always use their own).
def unused(name):
    name = Path(name).name.lower()
    return name.startswith(('opencv_videoio_ffmpeg', 'api-ms-win-')) or name == 'ucrtbase.dll'
a.binaries = [entry for entry in a.binaries if not unused(entry[0])]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CoC Auto', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CoC Auto')
