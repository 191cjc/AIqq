"""Synthetic-only A/B launcher; no provider credentials or QQ calls."""
import runpy
import sys
import tempfile
from pathlib import Path
from aiqq.services.ai import chat_isolation

source = Path(chat_isolation.__file__).read_text()
# Reproduce the pre-fix child mask without changing the production launcher.
source = source.replace("        os.umask(0o022)\n", "")
mode = sys.argv[1]
if mode == 'identify':
    source = source.replace('0x00050000 | errno.EPERM,', '0x00050000 | {"chmod": 117, "fchmod": 118, "fchmodat": 119, "fchmodat2": 120}.get(name, errno.EPERM),')
elif mode == 'identify_mode':
    source = source.replace('0x00050000 | errno.EPERM,', '0x00050000 | (131 if name == \"fchmod\" else errno.EPERM),')
    source = source.replace('            deny(name)\n', '            deny(name, Compare(1, 4, 0o644, 0)) if name == \"fchmod\" else deny(name)\n')
elif mode == 'umask022':
    source = source.replace('os.execve(config["cli"]', 'os.umask(0o022)\n        os.execve(config["cli"]')
elif mode == 'allow_chmod':
    source = source.replace('"chmod", "fchmod", "fchmodat", "fchmodat2",', '')
else:
    raise ValueError(mode)
with tempfile.TemporaryDirectory(prefix='chat-umask-probe-', dir='/var/lib/aiqq') as folder:
    patched = Path(folder, 'isolation.py')
    patched.write_text(source)
    chat_isolation.__file__ = str(patched)
    runpy.run_path(str(Path(__file__).with_name('chat-start-probe.py')), run_name='__main__')
